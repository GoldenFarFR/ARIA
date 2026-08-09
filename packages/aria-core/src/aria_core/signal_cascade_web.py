"""Multi-source signal cascade -- web column, stages 1+2. Third column
built (operator build order: GitHub/Farcaster free -> web budget-bounded ->
X pay-per-use, cf. docs/HANDOFF_PIPELINE_MOMENTUM.md's "multi-source signal
cascade" entry), same structural pattern as the two free columns -- read
``signal_cascade_github.py``'s docstring for the shared doctrine.

Reuses ``skills/website_substance.py`` AS-IS (already-calibrated judgment,
"positive" >= 70/100, same doctrine as ``github_substance.py``) -- built
23/07, used today only post-BUY via ``vc_analysis.py``. Its own crawl
(``services/tavily.TavilyClient.crawl``) already enforces the SHARED
monthly Tavily budget internally (``tavily_budget.can_spend``/
``record_spend``, fail-closed -- an exhausted budget degrades to
``available=False`` -> 'unknown' verdict, never a crash, never a second
budget check needed here).

Unlike the two free columns, this one IS a real recurring cost against a
budget shared with every other Tavily caller in the codebase (general web
research, the learning loop) -- two deliberate throttles on top of the
budget's own fail-closed behavior: (1) ``REEVALUATION_TTL_DAYS = 15``
(matches ``vc_analysis._WEBSITE_SUBSTANCE_TTL_DAYS`` -- a project site's
content rarely changes meaningfully faster than that); (2) the heartbeat
cycle itself runs hourly, not every 15min like the free columns (see
``heartbeat.py``'s ``web_signal_cascade_cycle``) -- a deliberately slower
drip so this column never monopolizes the shared budget at the other
callers' expense.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import aiosqlite

from aria_core.paths import aria_db_path

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())

REEVALUATION_TTL_DAYS = 15.0

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


async def _ensure_table() -> None:
    global _table_ready
    if _table_ready:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(_WATCHLIST_DDL)
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


async def _pick_next_due(db: aiosqlite.Connection):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=REEVALUATION_TTL_DAYS)).isoformat()
    cursor = await db.execute(
        "SELECT website_url, contract, chain, symbol, last_signal FROM web_signal_cascade_watchlist "
        "WHERE last_evaluated_at IS NULL OR last_evaluated_at < ? "
        "ORDER BY last_evaluated_at IS NOT NULL, last_evaluated_at ASC LIMIT 1",
        (cutoff,),
    )
    return await cursor.fetchone()


async def run_refresh_cycle() -> dict:
    """Stage 2 QUANTITATIVE FILTER, one website per call. Best-effort:
    never raises. A budget-exhausted Tavily crawl degrades cleanly to an
    'unknown' verdict (facts.available == False), same as any other
    unreachable source -- never treated as a failure worth logging loudly."""
    try:
        await _ensure_table()
        async with aiosqlite.connect(DB_PATH) as db:
            row = await _pick_next_due(db)
        if row is None:
            return {"evaluated": None}
        website_url, contract, chain, symbol, previous_signal = row

        from aria_core.skills.website_substance import gather_website_substance_facts, judge_website_substance

        facts = await gather_website_substance_facts(website_url)
        verdict = judge_website_substance(facts)
        accelerating = previous_signal in (None, "weak", "unknown") and verdict.signal == "positive"

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE web_signal_cascade_watchlist SET last_evaluated_at = ?, last_score = ?, "
                "previous_signal = last_signal, last_signal = ?, accelerating = ? WHERE website_url = ?",
                (
                    datetime.now(timezone.utc).isoformat(), verdict.score, verdict.signal,
                    int(accelerating), website_url,
                ),
            )
            await db.commit()

        from aria_core import signal_cascade_convergence

        await signal_cascade_convergence.record_source_signal(
            contract, chain, "web", verdict.signal,
            accelerating=accelerating,
            detail=f"{website_url} score {verdict.score or 0.0:.0f}/100",
            symbol=symbol,
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
    except Exception as exc:  # noqa: BLE001 -- shadow-style stage, never blocking
        logger.info("signal_cascade_web: refresh cycle failed (%s)", exc)
        return {"evaluated": None, "error": str(exc)}


async def list_stage2_positive() -> list[dict]:
    """What stage 2 lets through. Every 'positive' result is also pushed to
    stage 3 (``signal_cascade_convergence.record_source_signal``, called
    from ``run_refresh_cycle`` above)."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT website_url, contract, chain, symbol, last_score, accelerating, last_evaluated_at "
            "FROM web_signal_cascade_watchlist WHERE last_signal = 'positive' "
            "ORDER BY accelerating DESC, last_evaluated_at DESC"
        )
        rows = await cursor.fetchall()
    return [
        {
            "website_url": r[0], "contract": r[1], "chain": r[2], "symbol": r[3],
            "score": r[4], "accelerating": bool(r[5]), "last_evaluated_at": r[6],
        }
        for r in rows
    ]
