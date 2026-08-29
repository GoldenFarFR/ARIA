"""Observation-only log of every liquidity/price decision made by
``OnChainPoolDiscoveryFeed.check_candidates()`` (29/08, operator-directed).

**Why this exists.** Robinhood's on-chain discovery (specs/015) ran clean
for a full 6h window -- zero DexPaprika calls, zero anomalies in the
priceability counters -- yet produced zero qualifications. `MIN_LIQUIDITY_
USD_DAY_ZERO`'s comparison against a real `reserve_usd` happens inline in
`check_candidates` and was never persisted: a candidate that failed the
floor, or never resolved a price at all, simply hit a silent `continue`.
Without a record of what actually crossed that line, "the floor eliminates
almost everything" and "the market itself produces almost nothing above
$200" are indistinguishable -- exactly the kind of missing-data dead end
this project's ingestion doctrine forbids settling for.

**Strictly log-only.** This module never influences `check_candidates`'
own decision, never triggers a network call, and records only values
already computed at the call site. `record_observation` is best-effort:
a logging failure must never break discovery.

**Records the FULL population, not just rejects.** A rejects-only sample
would recreate the exact selection bias this exists to eliminate -- every
candidate that reaches a liquidity/price verdict (qualified, floor-failed,
or unpriceable) gets one row, so the eventual analysis can compute a real
percentage against the true denominator.

**`None` stays `None`.** `reserve_usd`/`price_usd` are inserted as-is via a
parameterised query -- SQLite stores an actual `NULL`, never a fabricated
`0`, so "unknown" is never silently indistinguishable from "measured
zero"."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import aiosqlite

from .paths import shadow_db_path

logger = logging.getLogger(__name__)

TABLE = "onchain_discovery_liquidity_log"

_ensured_db_paths: set[str] = set()


def _db_path() -> str:
    return str(shadow_db_path())


async def _ensure_table(db_path: str | None = None) -> None:
    path = db_path or _db_path()
    if path in _ensured_db_paths:
        return
    async with aiosqlite.connect(path) as db:
        await db.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TABLE} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                observed_at TEXT NOT NULL,
                chain TEXT NOT NULL,
                pool_address TEXT NOT NULL,
                token_address TEXT NOT NULL,
                reserve_usd REAL,
                price_usd REAL,
                min_liquidity_usd REAL NOT NULL,
                meets_liquidity_floor INTEGER,
                source TEXT,
                qualified INTEGER NOT NULL
            )
            """
        )
        await db.commit()
    _ensured_db_paths.add(path)


async def record_observation(
    *,
    chain: str,
    pool_address: str,
    token_address: str,
    reserve_usd: float | None,
    price_usd: float | None,
    min_liquidity_usd: float,
    source: str | None,
    qualified: bool,
    db_path: str | None = None,
) -> None:
    """One row per candidate reaching a liquidity/price verdict in
    ``check_candidates``. ``source`` is ``"event"`` (live websocket tick),
    ``"cold_read"`` (the bounded on-chain fallback), or ``None`` (neither
    ever resolved reserve+price this cycle). Never raises -- a failure here
    must never turn a real discovery cycle into a broken one."""
    path = db_path or _db_path()
    meets_floor = None if reserve_usd is None else reserve_usd >= min_liquidity_usd
    try:
        await _ensure_table(path)
        async with aiosqlite.connect(path) as db:
            await db.execute(
                f"""
                INSERT INTO {TABLE}
                    (observed_at, chain, pool_address, token_address, reserve_usd,
                     price_usd, min_liquidity_usd, meets_liquidity_floor, source, qualified)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.now(timezone.utc).isoformat(),
                    chain, pool_address, token_address,
                    reserve_usd, price_usd, min_liquidity_usd,
                    None if meets_floor is None else int(meets_floor),
                    source, int(qualified),
                ),
            )
            await db.commit()
    except Exception as exc:  # noqa: BLE001 -- observation must never break discovery
        logger.info("discovery_liquidity_observation: record failed for %s (%s)", pool_address, exc)
