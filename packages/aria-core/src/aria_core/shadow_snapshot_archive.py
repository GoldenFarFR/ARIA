"""Shared exit-check snapshot archive for shadow modules (18/08,
operator-directed exhaustive-capture request: future trades should carry
no analysis blind spots -- every already-fetched field persisted, even
ones that don't look useful yet).

Motivation: every shadow module's exit-tracking loop already fetches a full
``PoolSnapshot`` (via ``_snapshot_with_fallback``/``get_pool_snapshot``) on
EVERY check cycle -- typically every 60-75s for the life of a position --
but historically only ``price_usd``/``reserve_usd``/``dex_id`` were ever
read from it. ``price_change_pct`` (m5/m15/m30/h1/h6/h24),
``transactions`` (buys/sells per window) and ``volume_usd`` (per window)
were fetched and silently discarded on every single check -- a much bigger
gap than a one-time entry-side omission, since it recurs dozens of times
per position. This module closes it going FORWARD at ZERO extra network
cost (the data is already in memory from a call the caller was making
anyway) -- existing closed positions stay unrecoverable at this
granularity, same limitation already accepted for ``shadow_candle_archive``.

One shared table across every shadow module (``module`` column
discriminates), same reasoning as ``shadow_candle_archive``: the shape is
identical everywhere a ``PoolSnapshot`` is checked, so a future shadow
module gets this for free by calling ``store_snapshot`` once per check,
never duplicating the schema. ``transactions``/``volume_usd`` stay as JSON
text (their per-window sub-structure isn't guaranteed uniform across
providers) while ``price_change_pct`` gets flat named columns (always
simple floats keyed by a small fixed set of windows).

Never raises into the caller: shadow modules are pure observation, an
archiving failure must never affect the real exit-tracking logic it rides
alongside (same bright-line doctrine as ``shadow_candle_archive``,
``telegram_notify.send``)."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from aria_core.paths import shadow_db_path

logger = logging.getLogger(__name__)

TABLE = "shadow_snapshot_archive"

DB_PATH = str(shadow_db_path())

_ensured_db_paths: set[str] = set()


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
                checked_at TEXT NOT NULL,
                price_usd REAL,
                reserve_usd REAL,
                dex_id TEXT,
                price_change_m5 REAL,
                price_change_m15 REAL,
                price_change_m30 REAL,
                price_change_h1 REAL,
                price_change_h6 REAL,
                price_change_h24 REAL,
                window_high REAL,
                window_low REAL,
                transactions_json TEXT,
                volume_usd_json TEXT,
                recorded_at TEXT NOT NULL
            )
            """
        )
        await db.execute(
            f"""
            CREATE INDEX IF NOT EXISTS idx_{TABLE}_position
            ON {TABLE} (module, position_id)
            """
        )
        # 21/08 -- hot idempotent ALTER for the two window-extreme columns.
        # Added after they were silently DROPPED: they were first passed
        # through `price_change_pct`, which only maps a fixed key set
        # (m5/m15/m30/h1/h6/h24), so `window_high`/`window_low` went in and
        # nothing came out -- no error, just empty columns. A dict-keyed
        # passthrough will always swallow a key it does not know; named
        # columns cannot.
        cur = await db.execute(f"PRAGMA table_info({TABLE})")
        existing = {r[1] for r in await cur.fetchall()}
        for col in ("window_high", "window_low"):
            if col not in existing:
                await db.execute(f"ALTER TABLE {TABLE} ADD COLUMN {col} REAL")
        await db.commit()
    _ensured_db_paths.add(path)


async def store_snapshot(
    *,
    module: str,
    position_id: int,
    pool_address: str,
    chain: str,
    price_usd: float | None,
    reserve_usd: float | None,
    dex_id: str | None,
    price_change_pct: dict[str, float] | None,
    transactions: dict[str, Any] | None,
    volume_usd: dict[str, float] | None,
    window_high: float | None = None,
    window_low: float | None = None,
) -> bool:
    """Archives one exit-check snapshot for a shadow position. Returns
    whether the row was actually written (best-effort, never raises)."""
    try:
        await _ensure_table()
        price_change_pct = price_change_pct or {}
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(_db_path()) as db:
            await db.execute(
                f"""
                INSERT INTO {TABLE} (
                    module, position_id, pool_address, chain, checked_at,
                    price_usd, reserve_usd, dex_id,
                    price_change_m5, price_change_m15, price_change_m30,
                    price_change_h1, price_change_h6, price_change_h24,
                    window_high, window_low,
                    transactions_json, volume_usd_json, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    module, position_id, pool_address, chain, now,
                    price_usd, reserve_usd, dex_id,
                    price_change_pct.get("m5"), price_change_pct.get("m15"), price_change_pct.get("m30"),
                    price_change_pct.get("h1"), price_change_pct.get("h6"), price_change_pct.get("h24"),
                    window_high, window_low,
                    json.dumps(transactions) if transactions else None,
                    json.dumps(volume_usd) if volume_usd else None,
                    now,
                ),
            )
            await db.commit()
            return True
    except Exception as exc:  # noqa: BLE001 -- archiving must never break the caller's real exit logic
        logger.info("shadow_snapshot_archive: store_snapshot failed for %s#%s (%s)", module, position_id, exc)
        return False


async def get_snapshots(*, module: str, position_id: int) -> list[dict]:
    """Reads back every archived snapshot for one position, ordered by
    ``checked_at`` -- the read side used by a future analysis pass."""
    await _ensure_table()
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            f"SELECT * FROM {TABLE} WHERE module = ? AND position_id = ? ORDER BY checked_at ASC",
            (module, position_id),
        )
        return [dict(r) for r in await cur.fetchall()]
