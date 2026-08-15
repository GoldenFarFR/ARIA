"""All-time-high shadow persistence (backlog, 15/08, Devil's Advocate finding
verified against real code -- see docs/HANDOFF_PIPELINE_MOMENTUM.md).

Real gap this closes: ``services/geckoterminal.get_all_time_high`` repaginates
up to 20 pages of 180 daily candles from the pool's creation date on EVERY
call -- the only caller is the VC x20 entry filter (``paper_trader.py``'s
``_vc_x20_potential_filter``), which re-evaluates any still-pending candidate
on every sourcing cycle with no rejection cache, so the same full history scan
repeats for the same token across cycles even though the ATH is a near-
immutable, monotonically non-decreasing value.

Shadow-only (Etape 1 of the transition plan, operator-approved 15/08): this
module ONLY records what each full scan found, alongside whether the newly
scanned value would ever come back LOWER than the previously recorded one --
the invariant a future incremental-scan optimization depends on ("the ATH
never goes down, so once known, only forward-scanning from the last-seen
point is needed"). Nothing reads from this table to change a real decision
yet -- ``get_all_time_high`` keeps doing its full scan exactly as before,
this module just watches. Once a week of real observations confirms the
invariant holds, Etape 2 can switch the filter to read this table first and
scan forward-only from ``scanned_until_ts``.

Same append-only, best-effort, never-raises design as candle_staleness_shadow.py.
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
    # Read at call time (never a from-import copy) so tests monkeypatching
    # DB_PATH to a tmp path work -- same doctrine as the other shadow modules.
    return DB_PATH


async def _ensure_table() -> None:
    path = _db_path()
    if path in _ensured_db_paths:
        return
    async with aiosqlite.connect(path) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS ath_shadow_cache (
                pool_address TEXT NOT NULL,
                network TEXT NOT NULL,
                ath_price REAL NOT NULL,
                ath_at TEXT,
                scanned_until_ts INTEGER,
                pages_scanned INTEGER,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (pool_address, network)
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS ath_shadow_observation_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pool_address TEXT NOT NULL,
                network TEXT NOT NULL,
                previous_ath_price REAL,
                new_ath_price REAL NOT NULL,
                invariant_violated INTEGER NOT NULL,
                pages_scanned INTEGER,
                recorded_at TEXT NOT NULL
            )
            """
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_ath_shadow_observation_recorded_at "
            "ON ath_shadow_observation_log (recorded_at)"
        )
        await db.commit()
    _ensured_db_paths.add(path)


async def get_cached(pool_address: str, network: str) -> dict | None:
    """The last shadow-recorded scan for this pool, or ``None`` if never
    observed yet. Read-only lookup -- never used to short-circuit a real
    scan in this Etape 1 (shadow-only)."""
    if not pool_address or not network:
        return None
    await _ensure_table()
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM ath_shadow_cache WHERE pool_address = ? AND network = ?",
            (pool_address, network),
        )
        row = await cur.fetchone()
        return dict(row) if row else None


async def record_scan(
    pool_address: str,
    network: str,
    *,
    ath_price: float,
    ath_at: datetime | None,
    scanned_until_ts: int | None,
    pages_scanned: int,
) -> None:
    """Records one full ATH scan's result. Best-effort: NEVER raises into
    ``get_all_time_high``'s real fetch path. Logs whether this scan's value
    ever comes back LOWER than the previously cached one for the same pool --
    a violation would mean the "ATH only goes up" invariant doesn't hold in
    practice (e.g. provider data revision), which the future incremental-scan
    optimization (Etape 2) depends on."""
    if not pool_address or not network or ath_price is None:
        return
    try:
        await _ensure_table()
        previous = await get_cached(pool_address, network)
        previous_price = previous["ath_price"] if previous else None
        violated = bool(previous_price is not None and ath_price < previous_price)
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(_db_path()) as db:
            await db.execute(
                """
                INSERT INTO ath_shadow_observation_log (
                    pool_address, network, previous_ath_price, new_ath_price,
                    invariant_violated, pages_scanned, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (pool_address, network, previous_price, ath_price, int(violated), pages_scanned, now),
            )
            await db.execute(
                """
                INSERT INTO ath_shadow_cache (
                    pool_address, network, ath_price, ath_at, scanned_until_ts, pages_scanned, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (pool_address, network) DO UPDATE SET
                    ath_price = excluded.ath_price,
                    ath_at = excluded.ath_at,
                    scanned_until_ts = excluded.scanned_until_ts,
                    pages_scanned = excluded.pages_scanned,
                    updated_at = excluded.updated_at
                """,
                (
                    pool_address, network, ath_price,
                    ath_at.isoformat() if ath_at else None,
                    scanned_until_ts, pages_scanned, now,
                ),
            )
            await db.commit()
        if violated:
            logger.warning(
                "ath_shadow: invariant violated for %s/%s -- previous=%.6g new=%.6g",
                pool_address[:14], network, previous_price, ath_price,
            )
    except Exception as exc:  # noqa: BLE001 -- shadow logging must never break a real scan
        logger.info("ath_shadow: record failed (%s)", exc)


async def list_recent(limit: int = 200) -> list[dict]:
    """Recent shadow observations, newest first."""
    await _ensure_table()
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM ath_shadow_observation_log ORDER BY id DESC LIMIT ?", (limit,)
        )
        return [dict(r) for r in await cur.fetchall()]


async def violation_rate(limit: int = 500) -> float | None:
    """Fraction of recent observations (with a previous value to compare
    against) where the invariant was violated -- ``None`` if nothing
    judgeable yet. The number to check before ever building Etape 2."""
    rows = await list_recent(limit=limit)
    judged = [r for r in rows if r.get("previous_ath_price") is not None]
    if not judged:
        return None
    violated = sum(1 for r in judged if r["invariant_violated"])
    return violated / len(judged)
