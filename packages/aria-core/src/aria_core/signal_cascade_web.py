"""Multi-source signal cascade -- web column, stages 1+2. Third column
built (operator build order: GitHub/Farcaster free -> web budget-bounded ->
X pay-per-use, cf. docs/HANDOFF_PIPELINE_MOMENTUM.md's "multi-source signal
cascade" entry), same structural pattern as the two free columns -- read
``signal_cascade_github.py``'s docstring for the shared doctrine.

Reuses ``skills/website_substance.py`` AS-IS (already-calibrated judgment,
"positive" >= 70/100, same doctrine as ``github_substance.py``) -- built
23/07. Its own crawl (``services/tavily.TavilyClient.crawl``) already
enforces the SHARED monthly Tavily budget internally
(``tavily_budget.can_spend``/``record_spend``, fail-closed -- an exhausted
budget degrades to ``available=False`` -> 'unknown' verdict, never a crash,
never a second budget check needed here).

Unlike the two free columns, this one IS a real recurring cost against a
budget shared with every other Tavily caller in the codebase (general web
research, the learning loop) -- two deliberate throttles on top of the
budget's own fail-closed behavior: (1) ``REEVALUATION_TTL_DAYS = 7`` (09/08,
lowered from an incorrectly-justified 15 -- the original claim of matching
``vc_analysis._WEBSITE_SUBSTANCE_TTL_DAYS`` was FALSE, that constant/caller
no longer exists in ``vc_analysis.py``, this module is today the ONLY
caller of ``website_substance.py`` in the codebase; 7 days is a reasonable
default, never rigorously calibrated -- "a project site's content rarely
changes meaningfully faster than that" -- open to recalibration if real
data ever justifies a different value); (2) the heartbeat cycle itself runs
hourly, not every 15min like the free columns (see ``heartbeat.py``'s
``web_signal_cascade_cycle``) -- a deliberately slower drip so this column
never monopolizes the shared budget at the other callers' expense.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import aiosqlite

from aria_core.paths import aria_db_path

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())

REEVALUATION_TTL_DAYS = 7.0

_WATCHLIST_DDL = """
CREATE TABLE IF NOT EXISTS web_signal_cascade_watchlist (
    website_url TEXT PRIMARY KEY,
    contract TEXT NOT NULL,
    chain TEXT NOT NULL DEFAULT 'base',
    symbol TEXT,
    first_seen_at TEXT NOT NULL,
    last_evaluated_at TEXT,
    last_score REAL,
    last_signal TEXT,
    previous_signal TEXT,
    accelerating INTEGER NOT NULL DEFAULT 0
)
"""

_table_ready = False

# 09/08, operator design: "je vois pas sur X et sur le site web l'équipe
# écrire ce contrat" -- impersonation-risk column, added after the very
# first deployment. Same hot-migration pattern as
# signal_cascade_convergence._QUEUE_ADDED_COLUMNS.
_WATCHLIST_ADDED_COLUMNS = (("contract_confirmed", "INTEGER"),)


async def _ensure_table() -> None:
    global _table_ready
    if _table_ready:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(_WATCHLIST_DDL)
        existing = {
            row[1]
            for row in await (await db.execute("PRAGMA table_info(web_signal_cascade_watchlist)")).fetchall()
        }
        for name, ddl in _WATCHLIST_ADDED_COLUMNS:
            if name not in existing:
                await db.execute(f"ALTER TABLE web_signal_cascade_watchlist ADD COLUMN {name} {ddl}")
        await db.commit()
    _table_ready = True


def _find_website_link(project_links: list[dict] | None) -> str | None:
    """Same matching rule as ``vc_analysis._find_link_by_label(links,
    ("website",))`` -- deliberately duplicated (not imported) rather than
    reaching into vc_analysis.py's private helper, same doctrine as
    ``signal_cascade_github._find_github_link``: each column owns its own
    link recognition, never a cross-column import."""
    for link in project_links or []:
        if not isinstance(link, dict):
            continue
        label = str(link.get("label") or "").strip().lower()
        if "website" not in label:
            continue
        url = str(link.get("url") or "").strip()
        if url.lower().startswith(("http://", "https://")):
            return url
    return None


async def enqueue_candidate(contract: str, chain: str, project_links: list[dict] | None, *, symbol: str | None = None) -> None:
    """Stage 1 COLLECT -- zero network cost (Tavily is never called here,
    only on the stage-2 refresh below). Best-effort, never raises."""
    try:
        website_url = _find_website_link(project_links)
        if not website_url:
            return
        await _ensure_table()
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT OR IGNORE INTO web_signal_cascade_watchlist "
                "(website_url, contract, chain, symbol, first_seen_at) VALUES (?, ?, ?, ?, ?)",
                (website_url, contract, chain, symbol, datetime.now(timezone.utc).isoformat()),
            )
            await db.commit()
    except Exception as exc:  # noqa: BLE001 -- stage 1, never blocking the caller
        logger.info("signal_cascade_web: enqueue failed for %s (%s)", contract[:10], exc)


# 09/08, explicit operator instruction ("reste en alerte et adapte la
# quantité de token pour que ça tienne toujours sous 7 jours"): the fixed
# 1-candidate-per-cycle batch could never keep up once the backlog grew
# past ~168 (REEVALUATION_TTL_DAYS * 24 cycles/week at this hourly
# cadence). Recalculated EVERY cycle from the REAL pending count -- never a
# guessed constant. Capped at _MAX_BATCH_PER_CYCLE so a single heartbeat
# pass can never run unboundedly long (each item still processed strictly
# SEQUENTIALLY, one Tavily crawl at a time -- never parallel, same
# "jamais plusieurs en même temps" doctrine as the rest of this cascade).
# If the real deficit exceeds this cap, run_refresh_cycle logs a WARNING
# rather than silently under-covering (no silent caps doctrine).
_MAX_BATCH_PER_CYCLE = 20


def _adaptive_batch_size(pending_count: int, *, cycle_interval_hours: float = 1.0) -> tuple[int, int]:
    """Returns (capped_batch_size, real_need) -- the caller logs a warning
    when real_need exceeds the cap, never silently under-covering."""
    if pending_count <= 0:
        return 0, 0
    cycles_available = max(1, int((REEVALUATION_TTL_DAYS * 24) / cycle_interval_hours))
    needed = -(-pending_count // cycles_available)  # ceil division
    return min(needed, _MAX_BATCH_PER_CYCLE), needed


async def _count_pending(db: aiosqlite.Connection, cutoff: str) -> int:
    cursor = await db.execute(
        "SELECT COUNT(*) FROM web_signal_cascade_watchlist WHERE last_evaluated_at IS NULL OR last_evaluated_at < ?",
        (cutoff,),
    )
    row = await cursor.fetchone()
    return int(row[0]) if row else 0


async def _pick_next_due(db: aiosqlite.Connection, *, limit: int = 1) -> list:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=REEVALUATION_TTL_DAYS)).isoformat()
    cursor = await db.execute(
        "SELECT website_url, contract, chain, symbol, last_signal FROM web_signal_cascade_watchlist "
        "WHERE last_evaluated_at IS NULL OR last_evaluated_at < ? "
        "ORDER BY last_evaluated_at IS NOT NULL, last_evaluated_at ASC LIMIT ?",
        (cutoff, max(0, limit)),
    )
    return await cursor.fetchall()


async def _evaluate_one(website_url: str, contract: str, chain: str, symbol: str | None, previous_signal: str | None) -> dict:
    """One candidate's full stage-2 evaluation -- factored out of
    run_refresh_cycle so the batch loop below can call it sequentially,
    never in parallel."""
    from aria_core.skills.website_substance import gather_website_substance_facts, judge_website_substance

    facts = await gather_website_substance_facts(website_url, contract=contract)
    verdict = judge_website_substance(facts)
    accelerating = previous_signal in (None, "weak", "unknown") and verdict.signal == "positive"

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE web_signal_cascade_watchlist SET last_evaluated_at = ?, last_score = ?, "
            "previous_signal = last_signal, last_signal = ?, accelerating = ?, "
            "contract_confirmed = ? WHERE website_url = ?",
            (
                datetime.now(timezone.utc).isoformat(), verdict.score, verdict.signal,
                int(accelerating),
                None if facts.contract_confirmed is None else int(facts.contract_confirmed),
                website_url,
            ),
        )
        await db.commit()

    from aria_core import signal_cascade_convergence

    confirmed_note = (
        "contrat NON trouvé sur le site" if facts.contract_confirmed is False
        else "contrat confirmé sur le site" if facts.contract_confirmed is True
        else "contenu insuffisant pour vérifier le contrat"
    )
    await signal_cascade_convergence.record_source_signal(
        contract, chain, "web", verdict.signal,
        accelerating=accelerating,
        detail=f"{website_url} score {verdict.score or 0.0:.0f}/100 -- {confirmed_note}",
        symbol=symbol,
        contract_confirmed_on_site=facts.contract_confirmed,
    )

    if accelerating:
        logger.info(
            "signal_cascade_web: %s (%s) accelerating -- %s -> positive (score %.0f)",
            symbol or contract[:10], website_url, previous_signal, verdict.score or 0.0,
        )
    return {
        "evaluated": website_url, "contract": contract, "chain": chain,
        "signal": verdict.signal, "score": verdict.score, "accelerating": accelerating,
    }


async def run_refresh_cycle() -> dict:
    """Stage 2 QUANTITATIVE FILTER -- an ADAPTIVE batch per call (09/08,
    explicit operator instruction), never a fixed 1. Batch size recomputed
    from the REAL pending count every cycle so the backlog always clears
    within REEVALUATION_TTL_DAYS at this cycle's cadence -- capped at
    _MAX_BATCH_PER_CYCLE, deficit logged loudly if the real need exceeds it.
    Each candidate is still processed strictly SEQUENTIALLY (one Tavily
    crawl at a time). Best-effort: never raises. A budget-exhausted Tavily
    crawl degrades cleanly to an 'unknown' verdict per candidate, never a
    failure worth logging loudly."""
    try:
        await _ensure_table()
        cutoff = (datetime.now(timezone.utc) - timedelta(days=REEVALUATION_TTL_DAYS)).isoformat()
        async with aiosqlite.connect(DB_PATH) as db:
            pending_total = await _count_pending(db, cutoff)
            batch_size, needed = _adaptive_batch_size(pending_total)
            if needed > _MAX_BATCH_PER_CYCLE:
                logger.warning(
                    "signal_cascade_web: backlog (%s) needs %s/cycle to stay under %s days, "
                    "capped at %s -- real coverage will fall behind the target",
                    pending_total, needed, REEVALUATION_TTL_DAYS, _MAX_BATCH_PER_CYCLE,
                )
            if batch_size == 0:
                return {"evaluated": None, "evaluated_count": 0, "pending_before": pending_total, "results": []}
            rows = await _pick_next_due(db, limit=batch_size)

        results = [await _evaluate_one(*row) for row in rows]
        return {
            "evaluated": results[0]["evaluated"] if results else None,
            "evaluated_count": len(results), "pending_before": pending_total,
            "batch_size": batch_size, "results": results,
        }
    except Exception as exc:  # noqa: BLE001 -- shadow-style stage, never blocking
        logger.info("signal_cascade_web: refresh cycle failed (%s)", exc)
        return {"evaluated": None, "evaluated_count": 0, "results": [], "error": str(exc)}


async def list_stage2_positive() -> list[dict]:
    """What stage 2 lets through. Every 'positive' result is also pushed to
    stage 3 (``signal_cascade_convergence.record_source_signal``, called
    from ``run_refresh_cycle`` above)."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT website_url, contract, chain, symbol, last_score, accelerating, last_evaluated_at, "
            "contract_confirmed FROM web_signal_cascade_watchlist WHERE last_signal = 'positive' "
            "ORDER BY accelerating DESC, last_evaluated_at DESC"
        )
        rows = await cursor.fetchall()
    return [
        {
            "website_url": r[0], "contract": r[1], "chain": r[2], "symbol": r[3],
            "score": r[4], "accelerating": bool(r[5]), "last_evaluated_at": r[6],
            "contract_confirmed": None if r[7] is None else bool(r[7]),
        }
        for r in rows
    ]
