"""V8 limit-order shadow (07/08 23h40, Claude's own pocket, carte blanche --
operator: "il n'y a pas de session dedie a v8 c'est ton agent tu le code en
temps reel").

Real diagnosis (see scalping_variants._V8_ENTRY_PAUSED's own comment): v8's
whole pocket is paused because BOTH tiers share the same failure signature --
0 winners in 43 real trades, n_touched_positive=0/9 on the divergence tier
that had an actual backtest basis. Not an exit-tuning problem: ``entry`` was
always a LIVE price sampled at evaluation time, un-anchored to the signal
candle's own close -- a "confirmed" wick can finish fading between candle
close and the pocket's next scan pass, before the anti-chase/giveback gates
(both already ATR-scaled) ever see it. Banked and now built (Devil's
Advocate report 2db20159, cited in scalping_variants.py's giveback-gate
comment): simulate a LIMIT order anchored to the signal candle's own close
instead of buying at whatever price happens to be live -- in SHADOW mode
first, same doctrine as combo_signal_shadow.py/wick_filter_shadow.py, so
this gets forward-validated on real candidates before ever routing a real
paper trade through it.

Unlike those two (stateless, one log line per observation), this shadow is
STATEFUL: a signal opens a PENDING simulated limit order, which the same
process later either FILLS (price actually returns to signal_close within a
bounded window) or lets EXPIRE (never came back -- itself a useful data
point: how often does a "confirmed" wick simply never get revisited?). A
FILLED shadow is then managed with the exact same exit mechanics as the real
v8 pocket (ATR trailing stop, stagnation timeout, absolute hold cap) --
reuses paper_trader.py's own pure functions rather than re-deriving them, so
this can never silently drift from what a real v8 position would do.

Purely observational: never touches the real $1M paper portfolio, never
places a real or even a real-simulated trade -- a completely separate table,
read by nothing else in the pipeline. Best-effort throughout: a failure here
must never block a real v8 evaluation.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Awaitable, Callable

import aiosqlite

from aria_core.paths import aria_db_path

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())

# How long a pending shadow order waits for price to return to signal_close
# before being marked "never filled" -- generous relative to v8's own
# freshness gate (_V8_MAX_BARS_SINCE_PIVOT=7 candles, ~1.75-3.5h at 15/30min
# width): gives the hypothesis a real chance without waiting so long the
# candidate is no longer comparable to a live entry.
FILL_WINDOW_HOURS = 3.0

# Same exit mechanics as the real v8 pocket (paper_trader.py's own
# per-wallet overrides for "scalping_v8") -- duplicated as constants here
# rather than imported, since importing paper_trader (which imports
# scalping_variants, which imports this module) would be circular. Revisit
# together if the real v8 constants ever change.
STAGNATION_TIMEOUT_HOURS = 1.5
STAGNATION_MIN_MOVE_PCT = 1.0
MAX_HOLD_HOURS = 2.0

_PROCESS_THROTTLE_SECONDS = 300.0
_last_processed_at = 0.0

_ensured_db_paths: set[str] = set()


def _db_path() -> str:
    # Read the module attribute at call time (never a from-import copy) so
    # tests monkeypatching ``DB_PATH`` to a tmp path work -- same doctrine as
    # wick_filter_shadow.py/chasing_filter_shadow.py.
    return DB_PATH


async def _ensure_table() -> None:
    path = _db_path()
    if path in _ensured_db_paths:
        return
    async with aiosqlite.connect(path) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS v8_limit_shadow (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                contract TEXT NOT NULL,
                chain TEXT NOT NULL DEFAULT 'base',
                symbol TEXT,
                signal_close REAL NOT NULL,
                atr_at_signal REAL NOT NULL,
                limit_price REAL NOT NULL,
                stop_price REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                opened_at TEXT NOT NULL,
                fill_price REAL,
                filled_at TEXT,
                high_water_price REAL,
                pending_high_water REAL,
                pending_high_water_since TEXT,
                closed_at TEXT,
                close_reason TEXT,
                pnl_pct REAL
            )
            """
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_v8_limit_shadow_status "
            "ON v8_limit_shadow (status)"
        )
        await db.commit()
    _ensured_db_paths.add(path)


async def record_signal(
    contract: str, chain: str, *, symbol: str | None, signal_close: float,
    atr_at_signal: float, stop_price: float,
) -> None:
    """Opens a new PENDING shadow order for a real wick+divergence signal --
    called on every v8 evaluation that reaches one, regardless of whether the
    real buy path is paused. Deduplicated: a contract with an already-open
    (pending or filled) shadow doesn't get a second one layered on top --
    same signal re-observed on a later scan of the same candidate."""
    if not contract or signal_close <= 0 or atr_at_signal <= 0:
        return
    try:
        await _ensure_table()
        async with aiosqlite.connect(_db_path()) as db:
            cur = await db.execute(
                "SELECT 1 FROM v8_limit_shadow WHERE contract = ? AND chain = ? "
                "AND status IN ('pending', 'filled') LIMIT 1",
                (contract, chain or "base"),
            )
            if await cur.fetchone() is not None:
                return
            await db.execute(
                """
                INSERT INTO v8_limit_shadow (
                    contract, chain, symbol, signal_close, atr_at_signal,
                    limit_price, stop_price, status, opened_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                """,
                (
                    contract, chain or "base", symbol, signal_close, atr_at_signal,
                    signal_close, stop_price,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            await db.commit()
    except Exception as exc:  # noqa: BLE001 -- shadow logging must never break a real evaluation
        logger.info("v8_limit_shadow: record_signal failed (%s)", exc)


def _hours_since(iso_ts: str | None) -> float | None:
    if not iso_ts:
        return None
    try:
        then = datetime.fromisoformat(iso_ts)
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - then).total_seconds() / 3600.0


async def process_shadows(
    price_atr_fn: Callable[[str, str], Awaitable[tuple[float | None, float | None]]],
) -> None:
    """Advances every open shadow order one step: fills or expires pending
    ones, manages already-filled ones with v8's real exit mechanics. Called
    from every real v8 evaluation (scalping_variants.py) -- throttled here
    (module-level, not per-candidate) since a single sourcing cycle evaluates
    many candidates but this only needs to run once per cycle.

    ``price_atr_fn(contract, chain)`` is injected rather than imported to
    avoid a circular import (scalping_variants -> this module -> back to
    scalping_variants' own _gates_and_candles) -- the caller already has that
    plumbing and the exact same cache, so this costs zero extra network
    calls beyond what v8's real scan already does this cycle."""
    global _last_processed_at
    now_monotonic = time.monotonic()
    if now_monotonic - _last_processed_at < _PROCESS_THROTTLE_SECONDS:
        return
    _last_processed_at = now_monotonic
    try:
        await _ensure_table()
        async with aiosqlite.connect(_db_path()) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM v8_limit_shadow WHERE status IN ('pending', 'filled')"
            )
            rows = [dict(r) for r in await cur.fetchall()]
    except Exception as exc:  # noqa: BLE001
        logger.info("v8_limit_shadow: process_shadows read failed (%s)", exc)
        return

    for row in rows:
        try:
            price, _atr = await price_atr_fn(row["contract"], row["chain"])
        except Exception as exc:  # noqa: BLE001 -- one bad candidate must never block the rest
            logger.info(
                "v8_limit_shadow: price fetch failed for %s (%s)", row.get("symbol"), exc,
            )
            continue
        if price is None or price <= 0:
            continue
        try:
            if row["status"] == "pending":
                await _process_pending(row, price)
            else:
                await _process_open(row, price)
        except Exception as exc:  # noqa: BLE001
            logger.info(
                "v8_limit_shadow: processing failed for %s (%s)", row.get("symbol"), exc,
            )


async def _process_pending(row: dict, price: float) -> None:
    age_hours = _hours_since(row["opened_at"]) or 0.0
    if price <= row["limit_price"]:
        async with aiosqlite.connect(_db_path()) as db:
            await db.execute(
                """
                UPDATE v8_limit_shadow SET status = 'filled', fill_price = ?, filled_at = ?,
                    high_water_price = ? WHERE id = ?
                """,
                (price, datetime.now(timezone.utc).isoformat(), price, row["id"]),
            )
            await db.commit()
        return
    if age_hours >= FILL_WINDOW_HOURS:
        async with aiosqlite.connect(_db_path()) as db:
            await db.execute(
                "UPDATE v8_limit_shadow SET status = 'expired', closed_at = ?, "
                "close_reason = 'never filled' WHERE id = ?",
                (datetime.now(timezone.utc).isoformat(), row["id"]),
            )
            await db.commit()


async def _process_open(row: dict, price: float) -> None:
    from aria_core.paper_trader import _advance_high_water, _compute_active_stop

    fill_price = row["fill_price"]
    high_water, pending_hw, pending_since = _advance_high_water(
        row["high_water_price"] or fill_price, row["pending_high_water"],
        row["pending_high_water_since"], price, datetime.now(timezone.utc),
    )
    atr_pct = (row["atr_at_signal"] / fill_price) if fill_price else None
    active_stop, stop_source = _compute_active_stop(
        entry_price=fill_price, entry_atr_pct=atr_pct, high_water_price=high_water,
        invalidation_price=row["stop_price"], breakeven_locked=False, mode="scalping",
    )

    close_reason: str | None = None
    if active_stop and price <= active_stop:
        close_reason = stop_source
    else:
        hours_open = _hours_since(row["filled_at"]) or 0.0
        best_seen = max(high_water, pending_hw or 0.0, price)
        peak_gain_pct = (best_seen / fill_price - 1.0) * 100.0 if fill_price else 0.0
        exit_gain_pct = (price / fill_price - 1.0) * 100.0 if fill_price else 0.0
        if (
            hours_open >= STAGNATION_TIMEOUT_HOURS
            and peak_gain_pct < STAGNATION_MIN_MOVE_PCT
            and exit_gain_pct > -STAGNATION_MIN_MOVE_PCT
        ):
            close_reason = "timeout stagnation (scalping)"
        elif hours_open >= MAX_HOLD_HOURS:
            close_reason = "duree max scalping"

    if close_reason is None:
        async with aiosqlite.connect(_db_path()) as db:
            await db.execute(
                "UPDATE v8_limit_shadow SET high_water_price = ?, pending_high_water = ?, "
                "pending_high_water_since = ? WHERE id = ?",
                (high_water, pending_hw, pending_since, row["id"]),
            )
            await db.commit()
        return

    pnl_pct = (price / fill_price - 1.0) * 100.0 if fill_price else 0.0
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            """
            UPDATE v8_limit_shadow SET status = 'closed', closed_at = ?, close_reason = ?,
                pnl_pct = ?, high_water_price = ? WHERE id = ?
            """,
            (datetime.now(timezone.utc).isoformat(), close_reason, pnl_pct, high_water, row["id"]),
        )
        await db.commit()


async def summary() -> dict:
    """Aggregate read for session/monitoring use -- never called from a real
    trading path."""
    await _ensure_table()
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT status, close_reason, pnl_pct FROM v8_limit_shadow")
        rows = [dict(r) for r in await cur.fetchall()]
    closed = [r for r in rows if r["status"] == "closed"]
    wins = sum(1 for r in closed if (r["pnl_pct"] or 0) > 0)
    return {
        "pending": sum(1 for r in rows if r["status"] == "pending"),
        "filled_open": sum(1 for r in rows if r["status"] == "filled"),
        "expired": sum(1 for r in rows if r["status"] == "expired"),
        "closed": len(closed),
        "wins": wins,
        "avg_pnl_pct": (sum(r["pnl_pct"] or 0 for r in closed) / len(closed)) if closed else None,
    }
