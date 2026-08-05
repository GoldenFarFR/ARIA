"""Wick-confirmation shadow filter (08/05) -- logs, NEVER blocks.

Empirical basis (05/08 backtest on 58 real closed trades, candle-by-candle
reconstruction): entries whose signal candle showed a lower-wick ratio >= 0.3
(``indicators.hammer_wick_ratio``) won 60% vs 25.6% below it (Fisher exact
p=0.026), consistent across pockets and periods. Before promoting that
threshold to a hard gate on the EXISTING pockets, this shadow layer records
the ratio on every real scalping limit-order trigger (v6/v7 -- the paths that
historically produced the losing no-wick entries) so the threshold can be
validated on FORWARD trades first, anti-overfitting doctrine.

The new ``scalping_v8`` pocket (skills/scalping_variants.py) uses the SAME
``hammer_wick_ratio`` as a hard entry gate -- v6 (logged, ungated) vs v8
(gated) is therefore a natural A/B on live paper trades.

Same design as ``chasing_filter_shadow.py`` (the established shadow-filter
pattern this deliberately mirrors): dedicated append-only table, best-effort
writes that NEVER raise into a real trading path, per-DB-path ensure cache.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import aiosqlite

from aria_core.paths import aria_db_path

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())

# Candidate threshold under shadow evaluation -- the one the 05/08 backtest
# validated. A single threshold (not a ladder like chasing_filter_shadow's
# 3/5/7/10%) because the backtest already picked it; the raw ratio is always
# persisted too, so any other cut can be re-derived later without re-fetching.
WICK_SHADOW_THRESHOLD = 0.30

_ensured_db_paths: set[str] = set()


def _db_path() -> str:
    # Read the module attribute at call time (never a from-import copy) so
    # tests monkeypatching ``DB_PATH`` to a tmp path work -- same doctrine as
    # chasing_filter_shadow.py.
    return DB_PATH


async def _ensure_table() -> None:
    path = _db_path()
    if path in _ensured_db_paths:
        return
    async with aiosqlite.connect(path) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS wick_filter_shadow_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                contract TEXT NOT NULL,
                chain TEXT NOT NULL DEFAULT 'base',
                symbol TEXT,
                wallet TEXT,
                source TEXT NOT NULL,
                wick_ratio REAL,
                would_block INTEGER,
                recorded_at TEXT NOT NULL
            )
            """
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_wick_filter_shadow_recorded_at "
            "ON wick_filter_shadow_log (recorded_at)"
        )
        await db.commit()
    _ensured_db_paths.add(path)


async def record_trigger(
    contract: str, chain: str, *, wallet: str, source: str,
    wick_ratio: float | None, symbol: str | None = None,
) -> None:
    """Logs one shadow observation at a real trigger/buy moment. Best-effort:
    NEVER raises into the caller's trading path (same contract as
    ``chasing_filter_shadow.record_check``). ``would_block`` is ``None`` when
    the ratio itself is unknown (zero-range candle) -- never fabricated."""
    if not contract:
        return
    would_block = None if wick_ratio is None else (1 if wick_ratio < WICK_SHADOW_THRESHOLD else 0)
    try:
        await _ensure_table()
        async with aiosqlite.connect(_db_path()) as db:
            await db.execute(
                """
                INSERT INTO wick_filter_shadow_log (
                    contract, chain, symbol, wallet, source, wick_ratio, would_block, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    contract, chain or "base", symbol, wallet, source,
                    wick_ratio, would_block,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            await db.commit()
    except Exception as exc:  # noqa: BLE001 -- shadow logging must never break a real buy
        logger.info("wick_filter_shadow: record failed (%s)", exc)


async def list_recent(limit: int = 200) -> list[dict]:
    """Recent shadow observations, newest first -- for the future forward-
    validation pass (compare would_block against the trades' real outcomes)."""
    await _ensure_table()
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM wick_filter_shadow_log ORDER BY id DESC LIMIT ?", (limit,)
        )
        return [dict(r) for r in await cur.fetchall()]
