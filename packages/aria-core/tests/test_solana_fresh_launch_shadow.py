"""Solana fresh-launch shadow (19/08) -- replaces the retired support-bounce
v1/v2 pockets. No entry filter beyond age<=5min + liquidity>=3000$ (a live
test found a distance-from-support filter counterproductive on a small
sample), real exit mechanics ported from solana_pump_shadow.py (scale-out
ladder + trailing stop), PumpSwap-aware liquidity-collapse guard, corrupted-
price sanity guard, and an unbounded-sourcing + 50-closure-checkpoint design
(never stops trading, unlike solana_variant_shadow.py's own 50-closure cap).
Same isolated-tmp-db + injected-client pattern as every other shadow test
file in this dome."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import aiosqlite
import pytest

from aria_core import shadow_candle_archive as archive
from aria_core import solana_fresh_launch_shadow as shadow
from aria_core.services.dexpaprika import Candle
from aria_core.services.geckoterminal import OHLCVResult, PoolSnapshot, TrendingPool

CHAIN = "solana"


@pytest.fixture(autouse=True)
async def _tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(shadow, "DB_PATH", str(tmp_path / "shadow.db"))
    shadow._ensured_db_paths.clear()
    await shadow._ensure_table()
    monkeypatch.setattr(archive, "DB_PATH", str(tmp_path / "shadow.db"))
    archive._ensured_db_paths.clear()
    yield
    shadow._ensured_db_paths.clear()


async def _rows() -> list[dict]:
    async with aiosqlite.connect(shadow._db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(f"SELECT * FROM {shadow.TABLE}")
        return [dict(r) for r in await cur.fetchall()]


async def _checkpoint_rows() -> list[dict]:
    async with aiosqlite.connect(shadow._db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(f"SELECT * FROM {shadow.CHECKPOINT_TABLE} ORDER BY checkpoint_number ASC")
        return [dict(r) for r in await cur.fetchall()]


def _pool(
    *, pool_address="poolA", token_address="tokA", symbol="FRESH",
    price_usd=1.0, reserve=5000.0, age_minutes=2.0, price_change_pct=None,
) -> TrendingPool:
    return TrendingPool(
        pool_address=pool_address, token_address=token_address, symbol=symbol,
        price_usd=price_usd,
        price_change_pct=price_change_pct or {},
        transactions_m15=None,
        volume_usd_m15=None,
        reserve_usd=reserve,
        pool_created_at=datetime.now(timezone.utc) - timedelta(minutes=age_minutes),
    )


def _candle(ts: float, *, open_: float, high: float, low: float, close: float, volume: float = 100.0) -> Candle:
    return Candle(ts=int(ts), open=open_, high=high, low=low, close=close, volume=volume)


def _flat_candles(n: int, price: float) -> list[Candle]:
    return [_candle(i, open_=price, high=price, low=price, close=price) for i in range(n)]


async def _insert_open_row(
    *, pool_address="poolA", entry_price=1.0, minutes_ago=10.0, reserve_usd=8000.0,
    support_range_high=None, realistic_entry_price=None,
) -> int:
    if realistic_entry_price is None:
        realistic_entry_price = entry_price
    detected_at = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
    async with aiosqlite.connect(shadow._db_path()) as db:
        cur = await db.execute(
            f"""
            INSERT INTO {shadow.TABLE}
                (pool_address, chain, detected_at, entry_price, reserve_usd, support_range_high,
                 remaining_qty, realized_proceeds, peak_price, next_scale_level, realistic_entry_price)
            VALUES (?, ?, ?, ?, ?, ?, 1.0, 0.0, ?, ?, ?)
            """,
            (
                pool_address, CHAIN, detected_at, entry_price, reserve_usd, support_range_high,
                entry_price, entry_price * (1 + shadow.SCALE_OUT_STEP_PCT / 100.0), realistic_entry_price,
            ),
        )
        await db.commit()
        return cur.lastrowid


class FakeClient:
    def __init__(self, price_by_pool, reserve_by_pool=None, dex_id_by_pool=None, ohlcv_by_pool=None):
        self._prices = price_by_pool
        self._reserves = dict(reserve_by_pool or {})
        self._dex_ids = dict(dex_id_by_pool or {})
        self._ohlcv = dict(ohlcv_by_pool or {})
        self.calls = []
        self.ohlcv_calls = []

    async def get_pool_snapshot(self, pool_address, *, network="solana"):
        self.calls.append(pool_address)
        price = self._prices.get(pool_address)
        if price is None:
            return PoolSnapshot(pool_address=pool_address, available=False, error="unavailable")
        reserve = self._reserves.get(pool_address, 1000.0)
        dex_id = self._dex_ids.get(pool_address)
        return PoolSnapshot(pool_address=pool_address, price_usd=price, reserve_usd=reserve, available=True, dex_id=dex_id)

    async def get_ohlcv(self, pool_address, *, network="solana", mode="standard", **_kwargs):
        self.ohlcv_calls.append(pool_address)
        result = self._ohlcv.get(pool_address)
        if result is None:
            return OHLCVResult(candles=[], available=False, error="unavailable")
        return result


# --- record_signals: entry criteria (age + liquidity only) ------------------

@pytest.mark.asyncio
async def test_age_within_ceiling_accepted(monkeypatch):
    monkeypatch.setattr(shadow.dexpaprika, "_fetch_one_interval", AsyncMock(return_value=_flat_candles(3, 1.0)))
    result = await shadow.record_signals([_pool(age_minutes=4.9)], chain=CHAIN)
    assert result["logged"] == 1


@pytest.mark.asyncio
async def test_age_beyond_ceiling_rejected(monkeypatch):
    monkeypatch.setattr(shadow.dexpaprika, "_fetch_one_interval", AsyncMock(return_value=_flat_candles(3, 1.0)))
    result = await shadow.record_signals([_pool(age_minutes=5.1)], chain=CHAIN)
    assert result["logged"] == 0


@pytest.mark.asyncio
async def test_liquidity_below_floor_rejected(monkeypatch):
    monkeypatch.setattr(shadow.dexpaprika, "_fetch_one_interval", AsyncMock(return_value=_flat_candles(3, 1.0)))
    result = await shadow.record_signals([_pool(reserve=2999.0)], chain=CHAIN)
    assert result["logged"] == 0


@pytest.mark.asyncio
async def test_liquidity_at_floor_accepted(monkeypatch):
    monkeypatch.setattr(shadow.dexpaprika, "_fetch_one_interval", AsyncMock(return_value=_flat_candles(3, 1.0)))
    result = await shadow.record_signals([_pool(reserve=3000.0)], chain=CHAIN)
    assert result["logged"] == 1


@pytest.mark.asyncio
async def test_no_momentum_filter_pool_with_no_price_change_data_still_qualifies(monkeypatch):
    """Deliberate: unlike every other pocket in this dome, this module has
    NO h1/m5 momentum gate at all -- a pool reporting zero price-change data
    still qualifies purely on age+liquidity."""
    monkeypatch.setattr(shadow.dexpaprika, "_fetch_one_interval", AsyncMock(return_value=_flat_candles(3, 1.0)))
    result = await shadow.record_signals([_pool(price_change_pct={})], chain=CHAIN)
    assert result["logged"] == 1


@pytest.mark.asyncio
async def test_zero_price_candidate_never_logged(monkeypatch):
    """19/08, real bug caught live minutes after first deployment: a
    genuinely brand-new pool can report price_usd=0.0 (a real reading, not
    a missing/None value) when no candle overrides it. A zero entry_price
    can never be traded/priced -- must never be logged, else it becomes a
    permanently-stuck phantom position (advance_exit_simulation's own
    falsy-entry_price guard would silently skip it forever)."""
    monkeypatch.setattr(shadow.dexpaprika, "_fetch_one_interval", AsyncMock(return_value=[]))
    result = await shadow.record_signals([_pool(price_usd=0.0)], chain=CHAIN)
    assert result["logged"] == 0
    assert await _rows() == []


@pytest.mark.asyncio
async def test_dedup_per_pool(monkeypatch):
    monkeypatch.setattr(shadow.dexpaprika, "_fetch_one_interval", AsyncMock(return_value=_flat_candles(3, 1.0)))
    await shadow.record_signals([_pool()], chain=CHAIN)
    result = await shadow.record_signals([_pool()], chain=CHAIN)
    assert result["logged"] == 0
    assert len(await _rows()) == 1


# --- distance_from_support_pct: informational only, never gates entry -------

@pytest.mark.asyncio
async def test_distance_computed_from_fewer_than_5_candles(monkeypatch):
    # 3 candles, range [0.9, 1.1], last close 0.95 -> position = (0.95-0.9)/(1.1-0.9)*100 = 25%
    candles = [
        _candle(0, open_=1.0, high=1.1, low=0.9, close=1.0),
        _candle(1, open_=1.0, high=1.0, low=0.9, close=1.0),
        _candle(2, open_=1.0, high=1.0, low=0.9, close=0.95),
    ]
    monkeypatch.setattr(shadow.dexpaprika, "_fetch_one_interval", AsyncMock(return_value=candles))
    result = await shadow.record_signals([_pool(price_usd=0.95)], chain=CHAIN)
    assert result["logged"] == 1
    rows = await _rows()
    assert rows[0]["support_candle_count"] == 3
    assert rows[0]["support_range_low"] == pytest.approx(0.9)
    assert rows[0]["support_range_high"] == pytest.approx(1.1)
    assert rows[0]["distance_from_support_pct"] == pytest.approx(25.0)
    assert rows[0]["entry_price"] == pytest.approx(0.95)  # freshest candle close, not the stale scan price


@pytest.mark.asyncio
async def test_distance_uses_at_most_5_most_recent_candles(monkeypatch):
    # 7 candles: the 2 oldest sit far outside [low, high] of the last 5 -- must be ignored.
    old = [_candle(0, open_=5.0, high=5.0, low=5.0, close=5.0), _candle(1, open_=5.0, high=5.0, low=5.0, close=5.0)]
    recent = [
        _candle(2, open_=1.0, high=1.1, low=0.9, close=1.0),
        _candle(3, open_=1.0, high=1.0, low=0.9, close=1.0),
        _candle(4, open_=1.0, high=1.0, low=0.9, close=1.0),
        _candle(5, open_=1.0, high=1.0, low=0.9, close=1.0),
        _candle(6, open_=1.0, high=1.0, low=0.9, close=0.95),
    ]
    monkeypatch.setattr(shadow.dexpaprika, "_fetch_one_interval", AsyncMock(return_value=old + recent))
    result = await shadow.record_signals([_pool(price_usd=0.95)], chain=CHAIN)
    assert result["logged"] == 1
    rows = await _rows()
    assert rows[0]["support_candle_count"] == 5
    assert rows[0]["support_range_low"] == pytest.approx(0.9)
    assert rows[0]["support_range_high"] == pytest.approx(1.1)


@pytest.mark.asyncio
async def test_distance_null_with_zero_candles_available_but_position_still_logged(monkeypatch):
    monkeypatch.setattr(shadow.dexpaprika, "_fetch_one_interval", AsyncMock(return_value=[]))
    result = await shadow.record_signals([_pool(price_usd=1.0)], chain=CHAIN)
    assert result["logged"] == 1  # candles are informational only, never block entry
    rows = await _rows()
    assert rows[0]["distance_from_support_pct"] is None
    assert rows[0]["support_range_low"] is None
    assert rows[0]["support_range_high"] is None
    assert rows[0]["support_candle_count"] == 0
    assert rows[0]["entry_price"] == pytest.approx(1.0)  # falls back to the scan-time price


@pytest.mark.asyncio
async def test_distance_null_with_a_single_degenerate_candle(monkeypatch):
    # 1 candle accepted (never wait for 5), but low==high -- a degenerate range has no
    # meaningful "position within it".
    monkeypatch.setattr(
        shadow.dexpaprika, "_fetch_one_interval",
        AsyncMock(return_value=[_candle(0, open_=1.0, high=1.0, low=1.0, close=1.0)]),
    )
    result = await shadow.record_signals([_pool(price_usd=1.0)], chain=CHAIN)
    assert result["logged"] == 1
    rows = await _rows()
    assert rows[0]["support_candle_count"] == 1
    assert rows[0]["distance_from_support_pct"] is None
    assert rows[0]["support_range_low"] is None


@pytest.mark.asyncio
async def test_candle_fetch_failure_never_blocks_logging(monkeypatch):
    async def _raise(*a, **k):
        raise RuntimeError("dexpaprika down")
    monkeypatch.setattr(shadow.dexpaprika, "_fetch_one_interval", AsyncMock(side_effect=_raise))
    result = await shadow.record_signals([_pool(price_usd=1.0)], chain=CHAIN)
    assert result["logged"] == 1  # a candle-fetch failure is never a reason to skip the entry
    rows = await _rows()
    assert rows[0]["distance_from_support_pct"] is None


@pytest.mark.asyncio
async def test_record_signals_archives_the_before_candles(monkeypatch):
    candles = [
        _candle(0, open_=1.0, high=1.1, low=0.9, close=1.0),
        _candle(1, open_=1.0, high=1.0, low=0.9, close=0.95),
    ]
    monkeypatch.setattr(shadow.dexpaprika, "_fetch_one_interval", AsyncMock(return_value=candles))
    result = await shadow.record_signals([_pool(price_usd=0.95)], chain=CHAIN)
    assert result["logged"] == 1
    rows = await _rows()
    archived = await archive.get_candles(module="solana_fresh_launch", position_id=rows[0]["id"], phase="before")
    assert len(archived) == 2


# --- advance_exit_simulation: scale-out ladder -------------------------------

@pytest.mark.asyncio
async def test_scale_out_multiple_rungs_filled_in_one_slow_cycle():
    await _insert_open_row(pool_address="poolA", entry_price=1.0, minutes_ago=10.0)
    client = FakeClient({"poolA": 2.0}, reserve_by_pool={"poolA": 8000.0})  # jumps straight past 3 rungs
    counts = await shadow.advance_exit_simulation(client, chain=CHAIN)
    assert counts["scale_out_fills"] == 3
    rows = await _rows()
    assert rows[0]["remaining_qty"] == pytest.approx(0.421875)
    assert rows[0]["next_scale_level"] == pytest.approx(2.44140625)
    assert rows[0]["peak_price"] == pytest.approx(2.0)
    assert rows[0]["exit_reason"] is None  # still open, dust threshold not reached


@pytest.mark.asyncio
async def test_scale_out_dust_closes_position():
    await _insert_open_row(pool_address="poolA", entry_price=1.0, minutes_ago=10.0)
    # 45x entry -- far past enough rungs (17) to leave <1% remaining, but still
    # under PEAK_PRICE_SANITY_MULTIPLE=50x so the corrupted-price guard doesn't
    # (correctly) skip this cycle instead.
    client = FakeClient({"poolA": 45.0}, reserve_by_pool={"poolA": 8000.0})
    counts = await shadow.advance_exit_simulation(client, chain=CHAIN)
    assert counts["closed_scale_out_complete"] == 1
    rows = await _rows()
    assert rows[0]["exit_reason"] == "scale_out_complete"
    assert rows[0]["remaining_qty"] == 0.0
    assert rows[0]["final_multiplier"] > 1.0


# --- advance_exit_simulation: trailing stop ----------------------------------

@pytest.mark.asyncio
async def test_trailing_stop_fires_from_peak_not_entry():
    await _insert_open_row(pool_address="poolA", entry_price=1.0, minutes_ago=10.0)
    # Cycle 1: price rises under the first rung (1.25), sets peak at 1.20.
    await shadow.advance_exit_simulation(FakeClient({"poolA": 1.20}, reserve_by_pool={"poolA": 8000.0}), chain=CHAIN)
    rows = await _rows()
    assert rows[0]["exit_reason"] is None
    assert rows[0]["peak_price"] == pytest.approx(1.20)
    # Cycle 2: price falls to exactly -15% of the 1.20 peak -> trailing stop.
    stop_price = 1.20 * (1 - shadow.TRAILING_STOP_PCT / 100.0)
    counts = await shadow.advance_exit_simulation(
        FakeClient({"poolA": stop_price}, reserve_by_pool={"poolA": 8000.0}), chain=CHAIN,
    )
    assert counts["closed_trailing_stop"] == 1
    rows = await _rows()
    assert rows[0]["exit_reason"] == "trailing_stop"
    assert rows[0]["remaining_qty"] == 0.0
    assert rows[0]["final_multiplier"] > 1.0  # stopped out above entry thanks to the earlier peak


@pytest.mark.asyncio
async def test_window_low_catches_stop_missed_by_point_sample_recovery():
    """Same window-detection doctrine as every other pocket here: a stop
    touched then partially recovered between two polls must still be caught
    by the closed-candle window low, not just the point-sample spot."""
    await _insert_open_row(pool_address="poolA", entry_price=1.0, minutes_ago=10.0)
    client1 = FakeClient({"poolA": 1.16}, reserve_by_pool={"poolA": 8000.0})
    await shadow.advance_exit_simulation(client1, chain=CHAIN)
    rows = await _rows()
    assert rows[0]["peak_price"] == pytest.approx(1.16)
    last_checked_epoch = shadow._epoch_of(rows[0]["last_checked_at"])
    assert last_checked_epoch is not None

    # A closed candle reveals a real low of 0.90 -- past the -15% stop line
    # from peak 1.16 (threshold 0.986) -- but the point-sample spot polled
    # afterward has already recovered to 1.10, ABOVE the threshold.
    candles = [_candle(last_checked_epoch + 60, open_=1.16, high=1.16, low=0.90, close=1.10)]
    client2 = FakeClient(
        {"poolA": 1.10}, reserve_by_pool={"poolA": 8000.0},
        ohlcv_by_pool={"poolA": OHLCVResult(candles=candles, available=True, error=None)},
    )
    counts = await shadow.advance_exit_simulation(client2, chain=CHAIN)
    assert counts["closed_trailing_stop"] == 1
    rows = await _rows()
    stop_price = 1.16 * (1 - shadow.TRAILING_STOP_PCT / 100.0)
    assert rows[0]["exit_reason"] == "trailing_stop"
    assert rows[0]["final_multiplier"] == pytest.approx(stop_price / 1.0)


# --- advance_exit_simulation: liquidity_collapse (PumpSwap-aware) -----------

@pytest.mark.asyncio
async def test_liquidity_collapse_closes_immediately():
    await _insert_open_row(pool_address="poolA", entry_price=1.0, minutes_ago=10.0, reserve_usd=8000.0)
    client = FakeClient({"poolA": 0.98}, reserve_by_pool={"poolA": 3000.0})  # < 50% of entry reserve
    counts = await shadow.advance_exit_simulation(client, chain=CHAIN)
    assert counts["closed_liquidity_collapse"] == 1
    rows = await _rows()
    assert rows[0]["exit_reason"] == "liquidity_collapse"


@pytest.mark.asyncio
async def test_pumpswap_pool_never_triggers_liquidity_collapse():
    """Critical, not an edge case here: this module's whole discovery window
    (age<=5min) means most candidates ARE pump.fun/PumpSwap pools, which
    misreport reserve regardless of real depth (see module docstring)."""
    await _insert_open_row(pool_address="poolA", entry_price=1.0, minutes_ago=10.0, reserve_usd=8000.0)
    client = FakeClient({"poolA": 0.98}, reserve_by_pool={"poolA": 0.0}, dex_id_by_pool={"poolA": "pumpswap"})
    counts = await shadow.advance_exit_simulation(client, chain=CHAIN)
    assert counts["closed_liquidity_collapse"] == 0


# --- advance_exit_simulation: max_hold ---------------------------------------

@pytest.mark.asyncio
async def test_max_hold_closes_after_60_minutes():
    await _insert_open_row(
        pool_address="poolA", entry_price=1.0, minutes_ago=shadow.MAX_HOLD_MINUTES + 1, reserve_usd=8000.0,
    )
    client = FakeClient({"poolA": 1.05}, reserve_by_pool={"poolA": 8000.0})  # no rung/stop triggered
    counts = await shadow.advance_exit_simulation(client, chain=CHAIN)
    assert counts["closed_max_hold"] == 1
    rows = await _rows()
    assert rows[0]["exit_reason"] == "max_hold"
    assert rows[0]["final_multiplier"] == pytest.approx(1.05)


@pytest.mark.asyncio
async def test_stays_open_before_60_minutes():
    await _insert_open_row(
        pool_address="poolA", entry_price=1.0, minutes_ago=shadow.MAX_HOLD_MINUTES - 1, reserve_usd=8000.0,
    )
    client = FakeClient({"poolA": 1.05}, reserve_by_pool={"poolA": 8000.0})
    counts = await shadow.advance_exit_simulation(client, chain=CHAIN)
    assert counts["closed_max_hold"] == 0


# --- advance_exit_simulation: PEAK_PRICE_SANITY_MULTIPLE guard --------------

@pytest.mark.asyncio
async def test_implausible_price_skipped_against_support_range_reference():
    await _insert_open_row(
        pool_address="poolA", entry_price=0.001, minutes_ago=10.0, reserve_usd=8000.0,
        support_range_high=0.0011,
    )
    # 60x the entry-time support_range_high -- past PEAK_PRICE_SANITY_MULTIPLE=50.
    client = FakeClient({"poolA": 0.0011 * 60}, reserve_by_pool={"poolA": 8000.0})
    counts = await shadow.advance_exit_simulation(client, chain=CHAIN)
    assert counts["checked"] == 1
    assert counts["scale_out_fills"] == 0
    rows = await _rows()
    assert rows[0]["exit_reason"] is None
    assert rows[0]["peak_price"] == pytest.approx(0.001)  # untouched, corrupted read ignored


@pytest.mark.asyncio
async def test_implausible_price_falls_back_to_entry_price_when_no_support_range():
    # No support_range_high captured at entry (e.g. zero candles available) --
    # the sanity guard must still work, using entry_price as its reference.
    await _insert_open_row(
        pool_address="poolA", entry_price=1.0, minutes_ago=10.0, reserve_usd=8000.0,
        support_range_high=None,
    )
    client = FakeClient({"poolA": 51.0}, reserve_by_pool={"poolA": 8000.0})  # 51x entry_price
    counts = await shadow.advance_exit_simulation(client, chain=CHAIN)
    assert counts["scale_out_fills"] == 0
    rows = await _rows()
    assert rows[0]["exit_reason"] is None
    assert rows[0]["peak_price"] == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_plausible_price_within_sanity_bound_still_processed():
    await _insert_open_row(
        pool_address="poolA", entry_price=1.0, minutes_ago=10.0, reserve_usd=8000.0,
        support_range_high=1.1,
    )
    client = FakeClient({"poolA": 1.20}, reserve_by_pool={"poolA": 8000.0})  # well under 50x
    counts = await shadow.advance_exit_simulation(client, chain=CHAIN)
    assert counts["checked"] == 1
    rows = await _rows()
    assert rows[0]["peak_price"] == pytest.approx(1.20)


# --- chain_pnl_summary_realistic ---------------------------------------------

@pytest.mark.asyncio
async def test_pnl_realistic_closed_position_counted():
    async with aiosqlite.connect(shadow._db_path()) as db:
        await db.execute(
            f"INSERT INTO {shadow.TABLE} (pool_address, chain, detected_at, entry_price, "
            "realistic_entry_price, exit_reason, realistic_final_multiplier) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("p1", CHAIN, datetime.now(timezone.utc).isoformat(), 1.0, 1.0, "trailing_stop", 0.9),
        )
        await db.commit()
    pnl = await shadow.chain_pnl_summary_realistic(CHAIN)
    assert pnl["closed"] == 1
    assert pnl["total_pnl_units"] == pytest.approx(-0.1)
    assert pnl["total_pnl_usd"] == pytest.approx(-0.1 * shadow.SIMULATED_TRADE_SIZE_USD)


@pytest.mark.asyncio
async def test_pnl_realistic_unreachable_liquidity_never_fabricated():
    async with aiosqlite.connect(shadow._db_path()) as db:
        await db.execute(
            f"INSERT INTO {shadow.TABLE} (pool_address, chain, detected_at, entry_price, "
            "realistic_entry_price) VALUES (?, ?, ?, ?, ?)",
            ("p1", CHAIN, datetime.now(timezone.utc).isoformat(), 1.0, None),
        )
        await db.commit()
    pnl = await shadow.chain_pnl_summary_realistic(CHAIN)
    assert pnl["unreachable_liquidity"] == 1
    assert pnl["closed"] == 0
    assert pnl["capital_deployed_usd"] == 0.0


@pytest.mark.asyncio
async def test_pnl_realistic_stranded_position_counted_as_loss_not_dropped():
    async with aiosqlite.connect(shadow._db_path()) as db:
        await db.execute(
            f"INSERT INTO {shadow.TABLE} (pool_address, chain, detected_at, entry_price, "
            "realistic_entry_price, exit_reason, realistic_final_multiplier, realistic_realized_proceeds) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("p1", CHAIN, datetime.now(timezone.utc).isoformat(), 1.0, 1.0, "liquidity_collapse", None, 0.0),
        )
        await db.commit()
    pnl = await shadow.chain_pnl_summary_realistic(CHAIN)
    assert pnl["stranded"] == 1
    assert pnl["total_pnl_units"] == pytest.approx(-1.0)


@pytest.mark.asyncio
async def test_pnl_realistic_excludes_outlier_via_support_range_reference():
    async with aiosqlite.connect(shadow._db_path()) as db:
        # realistic_final_multiplier=60 implies exit_price=60*entry=60*0.001=0.06,
        # which is 60x support_range_high=0.0011*50=0.055 -- past the sanity bound.
        await db.execute(
            f"INSERT INTO {shadow.TABLE} (pool_address, chain, detected_at, entry_price, "
            "realistic_entry_price, exit_reason, realistic_final_multiplier, support_range_high) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("p1", CHAIN, datetime.now(timezone.utc).isoformat(), 0.001, 0.001, "trailing_stop", 60.0, 0.0011),
        )
        await db.commit()
    pnl = await shadow.chain_pnl_summary_realistic(CHAIN)
    assert pnl["outlier_excluded"] == 1
    assert pnl["closed"] == 0


# --- No closure cap + 50-closure checkpoint (19/08, operator correction) ----

@pytest.mark.asyncio
async def test_sourcing_never_stops_past_50_closures(monkeypatch):
    """Deliberately DISTINCT from solana_variant_shadow.py's own
    TARGET_CLOSURES_PER_VARIANT cap (which stops sourcing once hit) -- this
    module keeps opening new positions indefinitely regardless of the total
    closed count."""
    async with aiosqlite.connect(shadow._db_path()) as db:
        for i in range(60):
            await db.execute(
                f"INSERT INTO {shadow.TABLE} (pool_address, chain, detected_at, entry_price, exit_reason) "
                "VALUES (?, ?, ?, ?, ?)",
                (f"old{i}", CHAIN, datetime.now(timezone.utc).isoformat(), 1.0, "trailing_stop"),
            )
        await db.commit()
    assert await shadow.closures_so_far() == 60
    monkeypatch.setattr(shadow.dexpaprika, "_fetch_one_interval", AsyncMock(return_value=_flat_candles(3, 1.0)))
    result = await shadow.record_signals([_pool(pool_address="newPool")], chain=CHAIN)
    assert result["logged"] == 1  # never blocked by the closure count, unlike solana_variant_shadow.py


@pytest.mark.asyncio
async def test_checkpoint_written_when_crossing_50_closures():
    # 49 already-closed rows, then close a 50th via advance_exit_simulation (max_hold).
    async with aiosqlite.connect(shadow._db_path()) as db:
        for i in range(49):
            await db.execute(
                f"INSERT INTO {shadow.TABLE} (pool_address, chain, detected_at, entry_price, "
                "realistic_entry_price, exit_reason, realistic_final_multiplier) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (f"old{i}", CHAIN, datetime.now(timezone.utc).isoformat(), 1.0, 1.0, "trailing_stop", 1.0),
            )
        await db.commit()
    await _insert_open_row(
        pool_address="poolLast", entry_price=1.0, minutes_ago=shadow.MAX_HOLD_MINUTES + 1, reserve_usd=8000.0,
    )
    assert await shadow.closures_so_far() == 49
    client = FakeClient({"poolLast": 1.05}, reserve_by_pool={"poolLast": 8000.0})
    counts = await shadow.advance_exit_simulation(client, chain=CHAIN)
    assert counts["closed_max_hold"] == 1
    assert await shadow.closures_so_far() == 50

    checkpoints = await _checkpoint_rows()
    assert len(checkpoints) == 1
    assert checkpoints[0]["checkpoint_number"] == 50
    assert checkpoints[0]["closed"] == 50


@pytest.mark.asyncio
async def test_checkpoint_never_duplicates_on_repeated_calls():
    async with aiosqlite.connect(shadow._db_path()) as db:
        for i in range(50):
            await db.execute(
                f"INSERT INTO {shadow.TABLE} (pool_address, chain, detected_at, entry_price, "
                "realistic_entry_price, exit_reason, realistic_final_multiplier) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (f"old{i}", CHAIN, datetime.now(timezone.utc).isoformat(), 1.0, 1.0, "trailing_stop", 1.0),
            )
        await db.commit()
    await shadow._maybe_write_checkpoint(CHAIN)
    await shadow._maybe_write_checkpoint(CHAIN)
    checkpoints = await _checkpoint_rows()
    assert len(checkpoints) == 1


@pytest.mark.asyncio
async def test_no_checkpoint_below_50_closures():
    async with aiosqlite.connect(shadow._db_path()) as db:
        for i in range(49):
            await db.execute(
                f"INSERT INTO {shadow.TABLE} (pool_address, chain, detected_at, entry_price, "
                "realistic_entry_price, exit_reason, realistic_final_multiplier) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (f"old{i}", CHAIN, datetime.now(timezone.utc).isoformat(), 1.0, 1.0, "trailing_stop", 1.0),
            )
        await db.commit()
    await shadow._maybe_write_checkpoint(CHAIN)
    assert await _checkpoint_rows() == []


@pytest.mark.asyncio
async def test_get_checkpoints_returns_ordered_list():
    async with aiosqlite.connect(shadow._db_path()) as db:
        for i in range(100):
            await db.execute(
                f"INSERT INTO {shadow.TABLE} (pool_address, chain, detected_at, entry_price, "
                "realistic_entry_price, exit_reason, realistic_final_multiplier) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (f"old{i}", CHAIN, datetime.now(timezone.utc).isoformat(), 1.0, 1.0, "trailing_stop", 1.0),
            )
        await db.commit()
    await shadow._maybe_write_checkpoint(CHAIN)
    checkpoints = await shadow.get_checkpoints()
    assert [c["checkpoint_number"] for c in checkpoints] == [100]


# --- summary -----------------------------------------------------------------

@pytest.mark.asyncio
async def test_summary_computes_winrate_and_multiplier():
    async with aiosqlite.connect(shadow._db_path()) as db:
        await db.execute(
            f"INSERT INTO {shadow.TABLE} (pool_address, chain, detected_at, entry_price, "
            "exit_reason, final_multiplier) VALUES (?, ?, ?, ?, ?, ?)",
            ("p1", CHAIN, datetime.now(timezone.utc).isoformat(), 1.0, "trailing_stop", 0.9),
        )
        await db.execute(
            f"INSERT INTO {shadow.TABLE} (pool_address, chain, detected_at, entry_price, "
            "exit_reason, final_multiplier) VALUES (?, ?, ?, ?, ?, ?)",
            ("p2", CHAIN, datetime.now(timezone.utc).isoformat(), 1.0, "max_hold", 1.2),
        )
        await db.commit()
    s = await shadow.summary(chain=CHAIN)
    assert s["completed"] == 2
    assert s["wins"] == 1
    assert s["win_rate"] == 0.5
    assert s["avg_multiplier"] == pytest.approx(1.05)
    assert s["by_exit_reason"] == {"trailing_stop": 1, "max_hold": 1}
