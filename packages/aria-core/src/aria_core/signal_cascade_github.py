"""Multi-source signal cascade -- GitHub column, stages 1+2 (architecture
carved 08/08, see docs/HANDOFF_PIPELINE_MOMENTUM.md's "multi-source signal
cascade" entry for the full 4-stage design: COLLECT -> QUANTITATIVE FILTER ->
CONVERGENCE -> PERSISTENT QUEUE).

Stage 1 COLLECT (mechanical, no judgment): ``enqueue_candidate`` is called
from ``momentum_entry.evaluate_hard_gates`` the moment a candidate's best
DexScreener pair resolves -- BEFORE any liquidity/technical/security filter,
so a repo enters the watchlist regardless of whether its token trades today.
This is the actual fix for the gap the aeon case study exposed: today
``skills/github_substance.py`` only ever runs on a candidate that ALREADY
cleared the technical BUY filter via ``vc_analysis.py`` -- a fresh fork with
real GitHub substance but no momentum yet (aeon at its ATL) was invisible.

Stage 2 QUANTITATIVE FILTER: reuses ``github_substance.judge_github_substance``
as-is (already-calibrated 0-100 score, "positive" >= 70) -- no new threshold
invented. Adds one thing the raw score doesn't carry: acceleration
(``previous_signal`` was weak/unknown/None, the new one is positive) -- the
real signal worth surfacing is a repo that just started producing real
substance, not a repo that has quietly been "positive" forever.

Never a veto, never a trigger, never blocks the momentum pipeline it's fed
by (best-effort, no exception ever propagates to the caller). Stage 3
(convergence table) and stage 4 (persistent triage queue) are a separate,
not-yet-built chantier -- this module only owns the GitHub column.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import aiosqlite

from aria_core.paths import aria_db_path

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())

# A repo's substance doesn't meaningfully change within a day -- re-running
# the up-to-31-call github_substance analysis on every 15min refresh pass
# would burn the authenticated 5000/h budget for no new information.
REEVALUATION_TTL_DAYS = 1.0

_WATCHLIST_DDL = """
CREATE TABLE IF NOT EXISTS github_signal_cascade_watchlist (
    repo_url TEXT PRIMARY KEY,
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


def _find_github_link(project_links: list[dict] | None) -> str | None:
    from aria_core.services.project_activity import is_github_link

    for link in project_links or []:
        if not isinstance(link, dict):
            continue
        url = link.get("url")
        if is_github_link(url):
            return url
    return None


async def enqueue_candidate(contract: str, chain: str, project_links: list[dict] | None, *, symbol: str | None = None) -> None:
    """Stage 1 COLLECT. Called for EVERY candidate whose best pair just
    resolved in ``momentum_entry.evaluate_hard_gates`` -- before any
    liquidity/technical/security filter, decoupled from whether this
    candidate will trade today. Zero network cost (one INSERT OR IGNORE) --
    a repo already in the watchlist is left untouched here, the periodic
    refresh cycle owns re-evaluation. Best-effort: never raises, never
    blocks the momentum pipeline that calls it."""
    try:
        github_url = _find_github_link(project_links)
        if not github_url:
            return
        await _ensure_table()
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT OR IGNORE INTO github_signal_cascade_watchlist "
                "(repo_url, contract, chain, symbol, first_seen_at) VALUES (?, ?, ?, ?, ?)",
                (github_url, contract, chain, symbol, datetime.now(timezone.utc).isoformat()),
            )
            await db.commit()
    except Exception as exc:  # noqa: BLE001 -- stage 1, never blocking the caller
        logger.info("signal_cascade_github: enqueue failed for %s (%s)", contract[:10], exc)


async def _pick_next_due(db: aiosqlite.Connection) -> tuple[str, str, str, str | None, str | None] | None:
    """Oldest never-evaluated repo first, then the oldest evaluation past
    the TTL -- never a repo already refreshed within the window."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=REEVALUATION_TTL_DAYS)).isoformat()
    cursor = await db.execute(
        "SELECT repo_url, contract, chain, symbol, last_signal FROM github_signal_cascade_watchlist "
        "WHERE last_evaluated_at IS NULL OR last_evaluated_at < ? "
        "ORDER BY last_evaluated_at IS NOT NULL, last_evaluated_at ASC LIMIT 1",
        (cutoff,),
    )
    return await cursor.fetchone()


async def run_refresh_cycle() -> dict:
    """Stage 2 QUANTITATIVE FILTER, one repo per call (same throttling
    doctrine as ``momentum_entry.run_goplus_watchlist_cycle`` -- a shared
    heartbeat cadence protects the GitHub API budget, no need for a second
    internal rate limiter here). Best-effort: never raises."""
    try:
        await _ensure_table()
        async with aiosqlite.connect(DB_PATH) as db:
            row = await _pick_next_due(db)
        if row is None:
            return {"evaluated": None}
        repo_url, contract, chain, symbol, previous_signal = row

        from aria_core.skills.github_substance import gather_github_substance_facts, judge_github_substance

        facts = await gather_github_substance_facts(repo_url)
        verdict = judge_github_substance(facts)
        accelerating = previous_signal in (None, "weak", "unknown") and verdict.signal == "positive"

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE github_signal_cascade_watchlist SET last_evaluated_at = ?, last_score = ?, "
                "previous_signal = last_signal, last_signal = ?, accelerating = ? WHERE repo_url = ?",
                (
                    datetime.now(timezone.utc).isoformat(), verdict.score, verdict.signal,
                    int(accelerating), repo_url,
                ),
            )
            await db.commit()

        if accelerating:
            logger.info(
                "signal_cascade_github: %s (%s) accelerating -- %s -> positive (score %.0f)",
                symbol or contract[:10], repo_url, previous_signal, verdict.score or 0.0,
            )
        return {
            "evaluated": repo_url, "contract": contract, "chain": chain,
            "signal": verdict.signal, "score": verdict.score, "accelerating": accelerating,
        }
    except Exception as exc:  # noqa: BLE001 -- shadow-style stage, never blocking
        logger.info("signal_cascade_github: refresh cycle failed (%s)", exc)
        return {"evaluated": None, "error": str(exc)}


async def list_stage2_positive() -> list[dict]:
    """What stage 2 lets through -- score>=70 (``judge_github_substance``'s
    own already-calibrated "positive" threshold, no new one invented here),
    with the acceleration flag surfaced explicitly. This is the future
    stage 3 (convergence table)'s intended input -- not built yet, this
    function is the seam."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT repo_url, contract, chain, symbol, last_score, accelerating, last_evaluated_at "
            "FROM github_signal_cascade_watchlist WHERE last_signal = 'positive' "
            "ORDER BY accelerating DESC, last_evaluated_at DESC"
        )
        rows = await cursor.fetchall()
    return [
        {
            "repo_url": r[0], "contract": r[1], "chain": r[2], "symbol": r[3],
            "score": r[4], "accelerating": bool(r[5]), "last_evaluated_at": r[6],
        }
        for r in rows
    ]
