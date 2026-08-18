"""Shared candle archive for shadow modules (18/08, operator-directed): "je
veut les bougies avant et apres le point dachat a chaque futur shadow" --
every shadow position now persists the raw OHLCV candles it actually saw,
both the ones used to justify the entry (``phase="before"``) and the ones
observed while tracking it toward an exit (``phase="after"``).

Motivation: the two existing shadow tables (``solana_support_bounce_shadow_log``
etc.) only ever stored ``entry_price``/``peak_price``/the final exit -- never
the intra-position price PATH. A real backtest of an alternate parameter
(a different trailing-stop %, a different max-hold duration) needs the full
candle sequence, not just the entry/peak/exit snapshot -- discovered live
18/08 while trying to answer exactly that question for the operator, and
the only path forward at the time was a fresh live re-fetch, not something
already on disk. This module closes that gap going FORWARD (existing closed
rows stay unrecoverable at the granularity that would be needed; a genuine
backtest on them still requires re-fetching historical OHLCV from
GeckoTerminal/DexPaprika, if those providers even retain history that far
back -- not attempted here).

One shared table across every shadow module (``module`` column
discriminates) rather than a table per module -- the shape is identical
everywhere (a candle is a candle), and a shared table means a future shadow
module gets this for free by calling ``store_candles`` once, never
duplicating the schema. Accepts any object with ``ts``/``open``/``high``/
``low``/``close``/``volume`` attributes (both ``aria_core.skills.ta_levels.Candle``
used by DexPaprika and ``aria_core.services.geckoterminal.Candle`` share this
exact shape but are technically distinct classes -- duck-typed on purpose,
never imports either).

Idempotent by construction: ``INSERT OR IGNORE`` on the
``(module, position_id, phase, candle_ts)`` unique index -- the "after"
phase is called repeatedly (once per exit-tracking check, with an
overlapping/growing candle window each time), so a naive re-insert would
otherwise duplicate every candle already stored on the previous check.
Never raises into the caller: shadow modules are pure observation, a
candle-archiving failure must never affect the real entry/exit logic it
rides alongside (same bright-line doctrine as every other shadow
sub-mechanism, e.g. ``telegram_notify.send``)."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Protocol

import aiosqlite

from aria_core.paths import shadow_db_path

logger = logging.getLogger(__name__)

TABLE = "shadow_candle_archive"

# Module-level, monkeypatchable in tests -- same pattern as every other
# shadow module (`solana_support_bounce_shadow.DB_PATH` etc.) so a test can
# redirect this to a tmp_path db instead of hitting the real production
# shadow.db.
DB_PATH = str(shadow_db_path())

_ensured_db_paths: set[str] = set()


class _CandleLike(Protocol):
    ts: int
    open: float
    high: float
    low: float
    close: float
    volume: float


def _db_path() -> str:
    return DB_PATH


async def _ensure_table() -> None:
    path = _db_path()
    if path in _ensured_db_paths:
        return
    async with aiosqlite.connect(path) as db:
        await db.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TABLE} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                module TEXT NOT NULL,
                position_id INTEGER NOT NULL,
                pool_address TEXT NOT NULL,
                chain TEXT NOT NULL,
                phase TEXT NOT NULL,
                candle_ts INTEGER NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume REAL NOT NULL DEFAULT 0.0,
                recorded_at TEXT NOT NULL
            )
            """
        )
        await db.execute(
            f"""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_{TABLE}_dedup
            ON {TABLE} (module, position_id, phase, candle_ts)
            """
        )
        await db.commit()
    _ensured_db_paths.add(path)


async def store_candles(
    *,
    module: str,
    position_id: int,
    pool_address: str,
    chain: str,
    phase: str,
    candles: list[_CandleLike],
) -> int:
    """Stores ``candles`` for one shadow position. Returns how many were
    genuinely NEW (already-seen (module, position_id, phase, candle_ts)
    triples are silently skipped, never duplicated, never raised as an
    error). ``phase`` is caller-defined but the two established values are
    "before" (the candles used to justify entry) and "after" (candles
    observed during exit-tracking)."""
    if not candles:
        return 0
    try:
        await _ensure_table()
        recorded_at = datetime.now(timezone.utc).isoformat()
        rows = [
            (
                module, position_id, pool_address, chain, phase,
                int(c.ts), float(c.open), float(c.high), float(c.low), float(c.close),
                float(c.volume), recorded_at,
            )
            for c in candles
        ]
        async with aiosqlite.connect(_db_path()) as db:
            cur = await db.executemany(
                f"""
                INSERT OR IGNORE INTO {TABLE} (
                    module, position_id, pool_address, chain, phase,
                    candle_ts, open, high, low, close, volume, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            await db.commit()
            return cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else 0
    except Exception as exc:  # noqa: BLE001 -- archiving must never break the caller's real exit/entry logic
        logger.info("shadow_candle_archive: store_candles failed for %s#%s (%s)", module, position_id, exc)
        return 0


async def get_candles(*, module: str, position_id: int, phase: str | None = None) -> list[dict]:
    """Reads back every archived candle for one position, ordered by
    ``candle_ts`` -- the read side used by a future backtest pass. Optional
    ``phase`` filter; omitted returns both "before" and "after" together."""
    await _ensure_table()
    query = f"SELECT * FROM {TABLE} WHERE module = ? AND position_id = ?"
    params: list[Any] = [module, position_id]
    if phase is not None:
        query += " AND phase = ?"
        params.append(phase)
    query += " ORDER BY candle_ts ASC"
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(query, params)
        return [dict(r) for r in await cur.fetchall()]
