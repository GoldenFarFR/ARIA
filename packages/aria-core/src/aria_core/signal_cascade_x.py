"""Multi-source signal cascade -- X column, stages 1+2. Fourth and last
column in the operator's build order (GitHub/Farcaster free -> web
budget-bounded -> X pay-per-use, cf. docs/HANDOFF_PIPELINE_MOMENTUM.md's
"multi-source signal cascade" entry), same structural pattern as the three
columns before it -- read ``signal_cascade_github.py``'s docstring for the
shared doctrine.

Reuses ``skills/x_substance.py`` AS-IS (already-calibrated judgment,
"positive" >= 70/100, built 23/07, used today only post-BUY via
``conviction_research.py``). Its data source is TwitterAPI.io (prepaid
credits on the operator's own dashboard, $0.18/1000 profiles, NO
programmatic cap built into that client -- ``services/twitterapi_io.py``'s
own docstring: "no dedicated budget built here, the operator manages its
top-up"), falling back to a Tavily extract (shares Tavily's own budget,
already fail-closed).

08/09 real finding BEFORE building this (never assume, verify): CLAUDE.md
claims "X reading cut off since July, never assume reactivated" -- false,
verified live against ``x_research_budget.py``/the real request log
(264 requests, most recent hours before this build, weekly cap 100/100
already exhausted this week). That budget is UNRELATED to this column
though (it gates ``conviction_research``'s buzz-search path, not
``x_substance``/TwitterAPI.io) -- CLAUDE.md itself needs a correction
separate from this build.

Since TwitterAPI.io has no built-in cap, this module owns its OWN
dedicated weekly budget (``WEEKLY_REQUEST_CAP = 15``, operator-approved
08/09) -- deliberately separate from any other budget in the codebase, so
this cascade column can NEVER starve ``conviction_research``'s own
existing use of the same signal for a real post-BUY candidate. Same
rolling-calendar-week, fail-closed doctrine as ``x_research_budget.py``/
``tavily_budget.py``. Heartbeat cycle runs DAILY (not hourly like the web
column) -- at most 1 real spend/day, ~7/week in normal use, wide margin
under the 15/week cap even if the watchlist backlog is large.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

import aiosqlite

from aria_core.paths import aria_db_path

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())

REEVALUATION_TTL_DAYS = 30.0

# Operator-approved 08/09 -- deliberately separate from every other budget
# in the codebase (x_research_budget.py serves a DIFFERENT purpose --
# conviction_research's buzz-search -- and must never be starved by this
# cascade column). See module docstring for the full reasoning.
WEEKLY_REQUEST_CAP = 15

_HANDLE_RE = re.compile(r"(?:x|twitter)\.com/(@?[\w]+)", re.IGNORECASE)

_WATCHLIST_DDL = """
CREATE TABLE IF NOT EXISTS x_signal_cascade_watchlist (
    x_handle TEXT PRIMARY KEY,
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
_BUDGET_DDL = """
CREATE TABLE IF NOT EXISTS x_signal_cascade_budget_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    x_handle TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
)
"""

_table_ready = False


async def _ensure_tables() -> None:
    global _table_ready
    if _table_ready:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(_WATCHLIST_DDL)
        await db.execute(_BUDGET_DDL)
        await db.commit()
    _table_ready = True


def _week_start(now: datetime | None = None) -> datetime:
    ref = now or datetime.now(timezone.utc)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    monday = ref - timedelta(days=ref.weekday())
    return monday.replace(hour=0, minute=0, second=0, microsecond=0)


async def _used_this_week(now: datetime | None = None) -> int:
    await _ensure_tables()
    start = _week_start(now).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (
            await db.execute(
                "SELECT COUNT(*) FROM x_signal_cascade_budget_log WHERE status = 'ok' AND created_at >= ?",
                (start,),
            )
        ).fetchone()
    return int(row[0]) if row else 0


async def can_spend(now: datetime | None = None) -> bool:
    """Fail-closed: when in doubt, refuse rather than risk exceeding the
    dedicated 15/week cap."""
    used = await _used_this_week(now)
    return used < WEEKLY_REQUEST_CAP


async def _record_spend(x_handle: str, *, status: str) -> None:
    await _ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO x_signal_cascade_budget_log (x_handle, status, created_at) VALUES (?, ?, ?)",
            (x_handle, status, datetime.now(timezone.utc).isoformat()),
        )
        await db.commit()


def _extract_x_handle(project_links: list[dict] | None) -> str | None:
    for link in project_links or []:
        if not isinstance(link, dict):
            continue
        label = str(link.get("label") or "").strip().lower()
        if "twitter" not in label and label != "x":
            continue
        m = _HANDLE_RE.search(str(link.get("url") or ""))
        if m:
            return m.group(1).lstrip("@")
    return None


async def enqueue_candidate(contract: str, chain: str, project_links: list[dict] | None, *, symbol: str | None = None) -> None:
    """Stage 1 COLLECT -- zero network cost, no budget touched here (only
    the stage-2 refresh below ever spends). Best-effort, never raises."""
    try:
        handle = _extract_x_handle(project_links)
        if not handle:
            return
        await _ensure_tables()
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT OR IGNORE INTO x_signal_cascade_watchlist "
                "(x_handle, contract, chain, symbol, first_seen_at) VALUES (?, ?, ?, ?, ?)",
                (handle, contract, chain, symbol, datetime.now(timezone.utc).isoformat()),
            )
            await db.commit()
    except Exception as exc:  # noqa: BLE001 -- stage 1, never blocking the caller
        logger.info("signal_cascade_x: enqueue failed for %s (%s)", contract[:10], exc)


async def _pick_next_due(db: aiosqlite.Connection):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=REEVALUATION_TTL_DAYS)).isoformat()
    cursor = await db.execute(
        "SELECT x_handle, contract, chain, symbol, last_signal FROM x_signal_cascade_watchlist "
        "WHERE last_evaluated_at IS NULL OR last_evaluated_at < ? "
        "ORDER BY last_evaluated_at IS NOT NULL, last_evaluated_at ASC LIMIT 1",
        (cutoff,),
    )
    return await cursor.fetchone()


async def run_refresh_cycle() -> dict:
    """Stage 2 QUANTITATIVE FILTER, one handle per call -- DAILY cadence
    (see module docstring). Checks the dedicated budget BEFORE spending;
    a spend is only recorded on an actual attempt (never on a budget-
    skipped or empty-watchlist pass). Best-effort: never raises."""
    try:
        await _ensure_tables()
        if not await can_spend():
            return {"evaluated": None, "reason": "budget dédié X épuisé cette semaine"}

        async with aiosqlite.connect(DB_PATH) as db:
            row = await _pick_next_due(db)
        if row is None:
            return {"evaluated": None}
        handle, contract, chain, symbol, previous_signal = row

        from aria_core.skills.x_substance import gather_x_substance_facts, judge_x_substance

        facts = await gather_x_substance_facts(handle)
        await _record_spend(handle, status="ok" if facts.available else "unavailable")
        verdict = judge_x_substance(facts)
        accelerating = previous_signal in (None, "weak", "unknown") and verdict.signal == "positive"

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE x_signal_cascade_watchlist SET last_evaluated_at = ?, last_score = ?, "
                "previous_signal = last_signal, last_signal = ?, accelerating = ? WHERE x_handle = ?",
                (
                    datetime.now(timezone.utc).isoformat(), verdict.score, verdict.signal,
                    int(accelerating), handle,
                ),
            )
            await db.commit()

        from aria_core import signal_cascade_convergence

        await signal_cascade_convergence.record_source_signal(
            contract, chain, "x", verdict.signal,
            accelerating=accelerating,
            detail=f"@{handle} score {verdict.score or 0.0:.0f}/100",
            symbol=symbol,
        )

        if accelerating:
            logger.info(
                "signal_cascade_x: %s (@%s) accelerating -- %s -> positive (score %.0f)",
                symbol or contract[:10], handle, previous_signal, verdict.score or 0.0,
            )
        return {
            "evaluated": handle, "contract": contract, "chain": chain,
            "signal": verdict.signal, "score": verdict.score, "accelerating": accelerating,
        }
    except Exception as exc:  # noqa: BLE001 -- shadow-style stage, never blocking
        logger.info("signal_cascade_x: refresh cycle failed (%s)", exc)
        return {"evaluated": None, "error": str(exc)}


async def list_stage2_positive() -> list[dict]:
    """What stage 2 lets through. Every 'positive' result is also pushed to
    stage 3 (``signal_cascade_convergence.record_source_signal``, called
    from ``run_refresh_cycle`` above)."""
    await _ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT x_handle, contract, chain, symbol, last_score, accelerating, last_evaluated_at "
            "FROM x_signal_cascade_watchlist WHERE last_signal = 'positive' "
            "ORDER BY accelerating DESC, last_evaluated_at DESC"
        )
        rows = await cursor.fetchall()
    return [
        {
            "x_handle": r[0], "contract": r[1], "chain": r[2], "symbol": r[3],
            "score": r[4], "accelerating": bool(r[5]), "last_evaluated_at": r[6],
        }
        for r in rows
    ]
