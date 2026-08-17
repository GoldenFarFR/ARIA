"""5-minute candle granularity shadow (17/08) -- logs, NEVER decides.

Operator question after the age_limit/trailing_stop fix ("on est pas censé
travailler en 5 minutes au lieu de 15"): the Solana/Robinhood pump shadow's
exit-tracking (``advance_exit_simulation``) reads the 15min/30min scalping
ladder (``mode="scalping"``, see ``services/ohlcv.py``). A real bug this same
session (SOLCATANA closing well past the trailing stop's -20% floor) traced
partly to a detection lag between checks -- finer candles shrink that lag.

Real trade-off, never assumed: a token this young may not have enough trades
to fill a 5min bucket reliably (thin/empty candle), and switching the LIVE
exit path would triple GeckoTerminal call volume on a provider whose rate
limit was just stabilized (17/08, ohlcv.py circuit-breaker fix) -- the wrong
moment to blindly add load. Same anti-overfitting doctrine already applied to
v8's wick-gate: observe on real forward data in SHADOW mode before ever
promoting a candle granularity to the live decision path.

Same design as ``wick_filter_shadow.py`` (the established pattern this
mirrors): dedicated append-only table, best-effort writes that NEVER raise
into the real exit-tracking path, per-DB-path ensure cache.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import aiosqlite

from aria_core.paths import aria_db_path

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())

_ensured_db_paths: set[str] = set()


def _db_path() -> str:
    # Read the module attribute at call time (never a from-import copy) so
    # tests monkeypatching ``DB_PATH`` to a tmp path work.
    return DB_PATH


async def _ensure_table() -> None:
    path = _db_path()
    if path in _ensured_db_paths:
        return
    async with aiosqlite.connect(path) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS candle_granularity_shadow_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pool_address TEXT NOT NULL,
                chain TEXT NOT NULL,
                symbol TEXT,
                window_low_15m REAL,
                window_low_5m REAL,
                stop_threshold REAL,
                would_15m_have_caught INTEGER,
                would_5m_have_caught INTEGER,
                five_min_available INTEGER NOT NULL,
                recorded_at TEXT NOT NULL
            )
            """
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_candle_granularity_shadow_recorded_at "
            "ON candle_granularity_shadow_log (recorded_at)"
        )
        await db.commit()
    _ensured_db_paths.add(path)


async def record_comparison(
    pool_address: str, chain: str, *,
    symbol: str | None,
    window_low_15m: float | None,
    window_low_5m: float | None,
    stop_threshold: float,
) -> None:
    """Logs one shadow observation at a real exit-tracking cycle. Best-effort:
    NEVER raises into the caller's exit-tracking path (same contract as
    ``wick_filter_shadow.record_trigger``).

    ``would_X_have_caught`` answers "would THIS granularity's window low have
    crossed the stop threshold this cycle" -- independently for each, so a
    case where 5min catches a breach that 15min's coarser window smoothed
    over is directly visible without recomputing anything later."""
    if not pool_address:
        return
    five_min_available = window_low_5m is not None
    would_15m = None if window_low_15m is None else (1 if window_low_15m <= stop_threshold else 0)
    would_5m = None if window_low_5m is None else (1 if window_low_5m <= stop_threshold else 0)
    try:
        await _ensure_table()
        async with aiosqlite.connect(_db_path()) as db:
            await db.execute(
                """
                INSERT INTO candle_granularity_shadow_log (
                    pool_address, chain, symbol, window_low_15m, window_low_5m,
                    stop_threshold, would_15m_have_caught, would_5m_have_caught,
                    five_min_available, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pool_address, chain, symbol, window_low_15m, window_low_5m,
                    stop_threshold, would_15m, would_5m,
                    1 if five_min_available else 0,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            await db.commit()
    except Exception as exc:  # noqa: BLE001 -- shadow logging must never break real exit-tracking
        logger.info("candle_granularity_shadow: record failed (%s)", exc)


async def list_recent(limit: int = 200) -> list[dict]:
    """Recent shadow observations, newest first -- for the future forward-
    validation pass (does 5min catch real stop-breaches that 15min misses)."""
    await _ensure_table()
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM candle_granularity_shadow_log ORDER BY id DESC LIMIT ?", (limit,)
        )
        return [dict(r) for r in await cur.fetchall()]


async def divergence_summary() -> dict:
    """Aggregate: how often would 5min have caught a stop-breach that 15min's
    coarser window missed THAT SAME cycle -- the number that decides whether
    promoting the granularity is worth the extra API load. Fail-safe: returns
    zeros on a fresh/empty table, never raises."""
    await _ensure_table()
    async with aiosqlite.connect(_db_path()) as db:
        cur = await db.execute(
            """
            SELECT
                COUNT(*),
                SUM(CASE WHEN five_min_available = 1 THEN 1 ELSE 0 END),
                SUM(CASE WHEN would_5m_have_caught = 1 AND would_15m_have_caught = 0 THEN 1 ELSE 0 END),
                SUM(CASE WHEN would_15m_have_caught = 1 AND would_5m_have_caught = 0 THEN 1 ELSE 0 END)
            FROM candle_granularity_shadow_log
            """
        )
        total, five_min_available, five_min_caught_more, fifteen_min_caught_more = await cur.fetchone()
    return {
        "total_observations": total or 0,
        "five_min_available": five_min_available or 0,
        "five_min_caught_a_breach_15min_missed": five_min_caught_more or 0,
        "fifteen_min_caught_a_breach_5min_missed": fifteen_min_caught_more or 0,
    }
