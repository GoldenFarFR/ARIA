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
        # HOT MIGRATION. The table predates this column and holds live rows from
        # several pockets, so it is added in place rather than by recreating it
        # -- the standing rule for a new metric is to start accumulating history
        # immediately, never to wait for a clean slate.
        existing = {r[1] for r in await db.execute_fetchall(f"PRAGMA table_info({TABLE})")}
        if "reserve_usd" not in existing:
            await db.execute(f"ALTER TABLE {TABLE} ADD COLUMN reserve_usd REAL")
        # 22/08 -- raw curve fields + the snapshot's own timestamp, for ONE
        # question the derived values cannot answer: a live closure showed
        # price -18.4% with reserve unchanged to the cent across 425ms, which a
        # bonding curve cannot do. Same timestamp on both reads means one price
        # is miscomputed; different timestamps mean the price really moved and
        # the reserve reading is the stale one.
        for column, kind in (("virtual_quote_raw", "INTEGER"),
                             ("virtual_token_raw", "INTEGER"),
                             ("real_quote_raw", "INTEGER"),
                             ("snapshot_updated_at", "REAL"),
                             ("snapshot_stale", "INTEGER")):
            if column not in existing:
                await db.execute(f"ALTER TABLE {TABLE} ADD COLUMN {column} {kind}")
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


# How often one position may write a tracking point, and how much the reserve
# must move to write one sooner. Both are needed: the interval alone would miss
# a collapse that happens between two ticks, and the threshold alone would write
# nothing at all for a position sitting still.
#
# Sizing, measured rather than guessed: the pocket tracks up to ~235 positions
# at a 2s refresh. Unthrottled that is ~10M rows/day for data whose only use is
# reconstructing a price path. At 15s, a typical 50-600s position leaves 3-40
# points, i.e. a few tens of thousands of rows/day -- enough to see the shape of
# a collapse, small enough to keep.
# 22/08 -- TEMPORARILY TIGHTENED (15s/3% -> 3s/1%) to settle one question the
# coarse setting cannot answer: when a stop set at -5% fills at -39.5%, did the
# price WALK down past -5% unobserved, or did it jump in a single transaction?
# On the one archived case the price went +0.5% -> -39.5% in 7s with NO
# intermediate point, but at a 3% capture threshold that is also what a fast
# walk would look like. At 1% a walk leaves a trail and a jump does not.
#
# This is the dome's standing "accelerated cadence on a new mechanism" rule:
# revert to 15s/3% once the question is settled -- the tight setting is roughly
# 5x the rows and is not meant to run indefinitely.
OBSERVATION_MIN_INTERVAL_SECONDS = 3.0
OBSERVATION_RESERVE_MOVE_PCT = 1.0
# 22/08 -- PRICE move, added after a real miss. On a bonding curve the price is
# `virtual_quote / virtual_token`, so it can move violently while the reserve
# barely twitches: position 1772 went 0% -> +121% -> 0% in 3.5s and the reserve
# stayed at 6036.55$ throughout, so the reserve trigger above never fired and
# the whole excursion left ONE archived row. Without a price trigger the
# archive cannot answer the only question that matters on these positions --
# did the price WALK down past the stop, or JUMP over it in one transaction.
OBSERVATION_PRICE_MOVE_PCT = 1.0

# position_id -> (last archived unix ts, last archived reserve, last archived price)
_last_observation: dict[tuple[str, int], tuple[float, float | None, float | None]] = {}


def should_record_observation(
    *, module: str, position_id: int, now_ts: float, reserve_usd: float | None,
    price_usd: float | None = None,
) -> bool:
    """Whether this tracking point is worth a row.

    Keeps the FIRST point of a position unconditionally: without it a collapse
    has no baseline to be measured against.

    Three triggers, deliberately: elapsed time, a reserve move, and a PRICE
    move. The last one is not redundant -- on a bonding curve the price is a
    ratio of virtual reserves, so it can multiply while the real reserve sits
    still, and a time-plus-reserve rule then archives nothing during exactly
    the excursion worth recording."""
    key = (module, position_id)
    previous = _last_observation.get(key)
    if previous is None:
        return True
    last_ts, last_reserve, last_price = previous
    if now_ts - last_ts >= OBSERVATION_MIN_INTERVAL_SECONDS:
        return True
    if reserve_usd is not None and last_reserve:
        move = abs(reserve_usd - last_reserve) / last_reserve * 100.0
        if move >= OBSERVATION_RESERVE_MOVE_PCT:
            return True
    if price_usd is not None and last_price:
        move = abs(price_usd - last_price) / last_price * 100.0
        if move >= OBSERVATION_PRICE_MOVE_PCT:
            return True
    return False


async def store_observation(
    *,
    module: str,
    position_id: int,
    pool_address: str,
    chain: str,
    price_usd: float,
    reserve_usd: float | None,
    phase: str = "after",
    now_ts: float | None = None,
    snapshot=None,
) -> int:
    """Archives ONE point of a position's path: price and pool reserve at a time.

    **Why (22/08).** Measured on 1019 closures past the new liquidity floor,
    `liquidity_collapse` carries 40.6% of all remaining loss -- 87 trades at
    -63.98% each, where the reserve had fallen 65% by the time we sold, over a
    median 11 minutes. The exit fires at a 50% reserve drop, which looks far too
    late, and NOTHING in the schema could confirm it: the row keeps the reserve
    at entry and the last one seen, never the path between them. Any simulation
    of a tighter threshold would have been invented.

    This is the 18/08 standing convention (every shadow module archives its
    path) applied to the pocket that never honoured it, extended with the
    reserve because for this pocket the pool draining IS the signal.

    A point is stored as a degenerate candle -- open=high=low=close -- so it
    shares the one table and the `module` column keeps pockets apart, rather
    than growing a second near-identical schema."""
    import time

    now_ts = time.time() if now_ts is None else now_ts
    if not should_record_observation(
        module=module, position_id=position_id, now_ts=now_ts, reserve_usd=reserve_usd,
        price_usd=price_usd,
    ):
        return 0
    try:
        await _ensure_table()
        async with aiosqlite.connect(_db_path()) as db:
            await db.execute(
                f"""
                INSERT OR IGNORE INTO {TABLE} (
                    module, position_id, pool_address, chain, phase,
                    candle_ts, open, high, low, close, volume, reserve_usd, recorded_at,
                    virtual_quote_raw, virtual_token_raw, real_quote_raw,
                    snapshot_updated_at, snapshot_stale
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    module, position_id, pool_address, chain, phase, int(now_ts),
                    float(price_usd), float(price_usd), float(price_usd), float(price_usd),
                    0.0, reserve_usd, datetime.now(timezone.utc).isoformat(),
                    getattr(snapshot, "virtual_quote_raw", None),
                    getattr(snapshot, "virtual_token_raw", None),
                    getattr(snapshot, "real_quote_raw", None),
                    getattr(snapshot, "updated_at", None),
                    1 if getattr(snapshot, "stale", False) else 0,
                ),
            )
            await db.commit()
        _last_observation[(module, position_id)] = (now_ts, reserve_usd, price_usd)
        return 1
    except Exception as exc:  # noqa: BLE001 -- archiving never breaks a real exit
        logger.info(
            "shadow_candle_archive: store_observation failed for %s#%s (%s)",
            module, position_id, exc,
        )
        return 0


def forget_position(*, module: str, position_id: int) -> None:
    """Drops a closed position's throttle state so the map cannot grow forever
    on a process that runs for weeks."""
    _last_observation.pop((module, position_id), None)


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
