"""Support-bounce Solana shadow (17/08) -- h1>-5% (widened 18/08) + established pool (age
>=70min, no ceiling) + price near the low of its own last-10x5min-candle
range. Exit: -10% trailing stop only, no scale-out ladder. Same isolated-
tmp-db + injected-client pattern as the other shadow test files."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import aiosqlite
import pytest

from aria_core import shadow_candle_archive as archive
from aria_core import solana_support_bounce_shadow as shadow
from aria_core.services.dexpaprika import Candle
from aria_core.services.geckoterminal import OHLCVResult, PoolSnapshot, TrendingPool
from aria_core.services.rugcheck import RugCheckReport

CHAIN = "solana"


@pytest.fixture(autouse=True)
async def _tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(shadow, "DB_PATH", str(tmp_path / "shadow.db"))
    shadow._ensured_db_paths.clear()
    await shadow._ensure_table()
    monkeypatch.setattr(archive, "DB_PATH", str(tmp_path / "shadow.db"))
    archive._ensured_db_paths.clear()
    monkeypatch.setattr(
        shadow.rugcheck, "get_token_report",
        AsyncMock(return_value=RugCheckReport(available=False, error="test default")),
    )
    from aria_core import solana_pump_shadow as base_shadow
    monkeypatch.setattr(base_shadow.dexscreener, "fetch_token_pairs", AsyncMock(return_value=[]))
    yield
    shadow._ensured_db_paths.clear()


async def _rows():
    async with aiosqlite.connect(shadow._db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(f"SELECT * FROM {shadow.TABLE}")
        return [dict(r) for r in await cur.fetchall()]


def _candles(lows: list[float], base_price: float = 1.0) -> list[Candle]:
    """10 candles (oldest first); `lows` are placed as the MOST RECENT
    candles (padded with `base_price` on the OLDER side, then truncated to
    the last 10) so the given values land where they matter: the support
    math (17/08, position-in-range formula) reads `current_price` from the
    LAST candle's close. Each candle's own high/close track its own low
    (high=low*1.1, close=low*1.01, so entry_price for a generic call is
    predictably `low*1.01`) -- by default this makes the freshest candle
    sit close to whatever low it was given (position ~9%), which is what
    "qualifies" under the new formula without callers needing to think
    about the exact math -- tests asserting exact distance values use the
    dedicated `_range_candles` helper instead."""
    lows = ([base_price] * 10 + lows)[-10:]
    return [
        Candle(ts=i, open=low, high=low * 1.1, low=low, close=low * 1.01, volume=100.0)
        for i, low in enumerate(lows)
    ]


def _candle(ts: float, *, open_: float, high: float, low: float, close: float, volume: float = 0.0) -> Candle:
    """A single exit-side OHLCV candle (GeckoTerminal's `get_ohlcv`, 18/08
    window-detection fix) -- distinct from `_candles` above, which builds the
    entry-side 10-candle support window (DexPaprika)."""
    return Candle(ts=int(ts), open=open_, high=high, low=low, close=close, volume=volume)


def _pool(
    *, pool_address="poolA", token_address="tokA", symbol="BOUNCE",
    price_usd=1.0, h1=10.0, reserve=8000.0, age_minutes=90.0,
) -> TrendingPool:
    return TrendingPool(
        pool_address=pool_address, token_address=token_address, symbol=symbol,
        price_usd=price_usd,
        price_change_pct={"h1": h1},
        transactions_m15=None,
        volume_usd_m15=None,
        reserve_usd=reserve,
        pool_created_at=datetime.now(timezone.utc) - timedelta(minutes=age_minutes),
    )


class FakeClient:
    """``ohlcv_by_pool`` defaults to nothing configured -> ``available=False``
    for every pool, exercising the documented fall-back-to-point-sample path
    (18/08 window-detection fix) unless a test opts in -- same pattern as
    solana_pump_shadow.py's own FakeClient."""

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


# --- record_signals: entry criteria -----------------------------------------

@pytest.mark.asyncio
async def test_h1_at_or_below_minus_5pct_rejected(monkeypatch):
    monkeypatch.setattr(shadow.dexpaprika, "_fetch_one_interval", AsyncMock(return_value=_candles([0.9])))
    logged = await shadow.record_signals([_pool(h1=-5.0)], chain=CHAIN)
    assert logged == 0
    logged = await shadow.record_signals([_pool(pool_address="poolB", h1=-10.0)], chain=CHAIN)
    assert logged == 0


@pytest.mark.asyncio
async def test_h1_mildly_negative_now_qualifies(monkeypatch):
    monkeypatch.setattr(shadow.dexpaprika, "_fetch_one_interval", AsyncMock(return_value=_candles([0.9])))
    logged = await shadow.record_signals([_pool(h1=-4.9)], chain=CHAIN)
    assert logged == 1
    logged = await shadow.record_signals([_pool(pool_address="poolB", h1=0.0)], chain=CHAIN)
    assert logged == 1


@pytest.mark.asyncio
async def test_liquidity_below_floor_rejected(monkeypatch):
    monkeypatch.setattr(shadow.dexpaprika, "_fetch_one_interval", AsyncMock(return_value=_candles([0.9])))
    logged = await shadow.record_signals([_pool(reserve=4999.0)], chain=CHAIN)
    assert logged == 0


@pytest.mark.asyncio
async def test_age_below_70min_rejected(monkeypatch):
    monkeypatch.setattr(shadow.dexpaprika, "_fetch_one_interval", AsyncMock(return_value=_candles([0.9])))
    logged = await shadow.record_signals([_pool(age_minutes=69.9)], chain=CHAIN)
    assert logged == 0


@pytest.mark.asyncio
async def test_no_upper_age_ceiling(monkeypatch):
    monkeypatch.setattr(shadow.dexpaprika, "_fetch_one_interval", AsyncMock(return_value=_candles([0.9])))
    logged = await shadow.record_signals([_pool(age_minutes=60000.0)], chain=CHAIN)  # ~41 days
    assert logged == 1


@pytest.mark.asyncio
def _range_candles(*, low: float, high: float, last_close: float) -> list[Candle]:
    """9 filler candles spanning [low, high] plus a LAST candle whose close
    is exactly `last_close` -- lets support-position tests pin down
    range_low/range_high/current_price precisely (17/08, position-in-range
    formula: distance_from_support_pct = (close-low)/(high-low)*100)."""
    filler = [
        Candle(ts=i, open=low, high=high, low=low, close=(low + high) / 2, volume=100.0)
        for i in range(9)
    ]
    return filler + [Candle(ts=9, open=last_close, high=high, low=low, close=last_close, volume=100.0)]


@pytest.mark.asyncio
async def test_price_within_20pct_of_support_qualifies(monkeypatch):
    # range [0.85, 1.0], close 0.87 -> position = (0.87-0.85)/(1.0-0.85)*100 = 13.3% <= 20%
    candles = _range_candles(low=0.85, high=1.0, last_close=0.87)
    monkeypatch.setattr(shadow.dexpaprika, "_fetch_one_interval", AsyncMock(return_value=candles))
    logged = await shadow.record_signals([_pool(price_usd=0.87)], chain=CHAIN)
    assert logged == 1
    rows = await _rows()
    assert rows[0]["range_low_10c"] == 0.85
    assert rows[0]["entry_price"] == pytest.approx(0.87)
    assert rows[0]["distance_from_support_pct"] == pytest.approx((0.87 - 0.85) / (1.0 - 0.85) * 100, rel=1e-6)


@pytest.mark.asyncio
async def test_price_beyond_20pct_of_support_rejected(monkeypatch):
    # range [0.7, 1.0], close 0.85 -> position = (0.85-0.7)/(1.0-0.7)*100 = 50% > 20%
    candles = _range_candles(low=0.7, high=1.0, last_close=0.85)
    monkeypatch.setattr(shadow.dexpaprika, "_fetch_one_interval", AsyncMock(return_value=candles))
    logged = await shadow.record_signals([_pool(price_usd=0.85)], chain=CHAIN)
    assert logged == 0


@pytest.mark.asyncio
async def test_price_at_range_high_rejected(monkeypatch):
    # 17/08, real bug caught live by the operator (Redbull: entry_price
    # landed EXACTLY equal to range_high after a smooth uninterrupted
    # climb -- the OLD formula, distance-from-low-in-absolute-percent, was
    # trivially satisfied whenever the whole range was narrow, even at its
    # very top). New formula: position = (high-low)/(high-low)*100 = 100%,
    # correctly rejected regardless of how narrow the range is.
    candles = _range_candles(low=0.469501, high=0.561006, last_close=0.561006)
    monkeypatch.setattr(shadow.dexpaprika, "_fetch_one_interval", AsyncMock(return_value=candles))
    logged = await shadow.record_signals([_pool(price_usd=0.561006)], chain=CHAIN)
    assert logged == 0


@pytest.mark.asyncio
async def test_uses_fresh_candle_close_not_stale_scan_price(monkeypatch):
    # 17/08, real bug caught live by the operator (Niles) -- pool.price_usd
    # is a STALE snapshot from the broad discovery scan; under real
    # DexPaprika contention the candle fetch for a given candidate can lag
    # that snapshot by minutes. Range [0.9, 1.0]: stale_price=0.99 sits near
    # the HIGH (position 90%, would be wrongly rejected if used), but the
    # last candle's FRESH close=0.91 sits near the LOW (position 10%,
    # qualifies) -- the fresh price must win.
    stale_price = 0.99
    fresh_close = 0.91
    candles = _range_candles(low=0.9, high=1.0, last_close=fresh_close)
    monkeypatch.setattr(shadow.dexpaprika, "_fetch_one_interval", AsyncMock(return_value=candles))
    logged = await shadow.record_signals([_pool(price_usd=stale_price)], chain=CHAIN)
    assert logged == 1
    rows = await _rows()
    assert rows[0]["entry_price"] == pytest.approx(fresh_close)
    assert rows[0]["distance_from_support_pct"] == pytest.approx((fresh_close - 0.9) / (1.0 - 0.9) * 100, rel=1e-6)


@pytest.mark.asyncio
async def test_price_below_support_low_rejected(monkeypatch):
    # range [1.0, 1.1], close 0.95 -> price is BELOW the 10-candle low
    # (position = -50%, negative): a breakdown, not a bounce -- must be
    # rejected even though it would be "close" in absolute percent terms.
    candles = _range_candles(low=1.0, high=1.1, last_close=0.95)
    monkeypatch.setattr(shadow.dexpaprika, "_fetch_one_interval", AsyncMock(return_value=candles))
    logged = await shadow.record_signals([_pool(price_usd=0.95)], chain=CHAIN)
    assert logged == 0


@pytest.mark.asyncio
async def test_extreme_range_ratio_rejected(monkeypatch):
    # 17/08, real bug caught live by the operator (BULLSHIT: range_high was
    # 7386x range_low -- a near-total collapse within the 50min lookback,
    # not a real consolidation). Position-in-range math alone can't catch
    # this: close=low means position=0% (technically "at support"), but the
    # range itself is a falling-knife crash, not a bounce.
    candles = _range_candles(low=0.000001, high=0.01, last_close=0.000001)  # ratio 10000x
    monkeypatch.setattr(shadow.dexpaprika, "_fetch_one_interval", AsyncMock(return_value=candles))
    logged = await shadow.record_signals([_pool(price_usd=0.000001)], chain=CHAIN)
    assert logged == 0


@pytest.mark.asyncio
async def test_range_ratio_within_cap_still_qualifies(monkeypatch):
    # range [1.0, 2.9] -> ratio 2.9x, under MAX_RANGE_RATIO=3.0; close=1.05
    # -> position = (1.05-1.0)/(2.9-1.0)*100 = 2.6%, well within tolerance.
    candles = _range_candles(low=1.0, high=2.9, last_close=1.05)
    monkeypatch.setattr(shadow.dexpaprika, "_fetch_one_interval", AsyncMock(return_value=candles))
    logged = await shadow.record_signals([_pool(price_usd=1.05)], chain=CHAIN)
    assert logged == 1


@pytest.mark.asyncio
async def test_fewer_than_10_candles_rejected(monkeypatch):
    short = _candles([0.9])[:9]
    monkeypatch.setattr(shadow.dexpaprika, "_fetch_one_interval", AsyncMock(return_value=short))
    logged = await shadow.record_signals([_pool()], chain=CHAIN)
    assert logged == 0


@pytest.mark.asyncio
async def test_candle_fetch_failure_never_blocks_batch(monkeypatch):
    async def _raise(*a, **k):
        raise RuntimeError("dexpaprika down")
    monkeypatch.setattr(shadow.dexpaprika, "_fetch_one_interval", AsyncMock(side_effect=_raise))
    logged = await shadow.record_signals([_pool()], chain=CHAIN)
    assert logged == 0  # skipped, never raised


@pytest.mark.asyncio
async def test_dedupe_per_pool(monkeypatch):
    monkeypatch.setattr(shadow.dexpaprika, "_fetch_one_interval", AsyncMock(return_value=_candles([0.9])))
    await shadow.record_signals([_pool()], chain=CHAIN)
    logged_again = await shadow.record_signals([_pool()], chain=CHAIN)
    assert logged_again == 0
    assert len(await _rows()) == 1


@pytest.mark.asyncio
async def test_stops_logging_once_target_reached(monkeypatch):
    monkeypatch.setattr(shadow, "TARGET_CLOSURES", 1)
    monkeypatch.setattr(shadow.dexpaprika, "_fetch_one_interval", AsyncMock(return_value=_candles([0.9])))
    await shadow.record_signals([_pool(pool_address="poolA")], chain=CHAIN)
    async with aiosqlite.connect(shadow._db_path()) as db:
        await db.execute(f"UPDATE {shadow.TABLE} SET exit_reason = 'trailing_stop'")
        await db.commit()
    logged = await shadow.record_signals([_pool(pool_address="poolB")], chain=CHAIN)
    assert logged == 0


# --- advance_exit_simulation: -10% trailing stop, no ladder -----------------

@pytest.mark.asyncio
async def test_trailing_stop_closes_full_position_no_partial(monkeypatch):
    monkeypatch.setattr(shadow.dexpaprika, "_fetch_one_interval", AsyncMock(return_value=_candles([0.9])))
    await shadow.record_signals([_pool(pool_address="poolA", price_usd=1.0, reserve=8000.0)], chain=CHAIN)
    # entry_price = 0.9*1.01 = 0.909 (see _candles); peak never rises (first
    # check IS the peak), price crashes to 0.75 (-17.5% from entry) -> past
    # the -10% stop
    client = FakeClient({"poolA": 0.75}, reserve_by_pool={"poolA": 8000.0})
    counts = await shadow.advance_exit_simulation(client, chain=CHAIN)
    assert counts["closed_trailing_stop"] == 1
    rows = await _rows()
    assert rows[0]["exit_reason"] == "trailing_stop"
    assert rows[0]["remaining_qty"] == 0.0  # full exit, never partial


@pytest.mark.asyncio
async def test_trailing_stop_fires_from_peak_not_entry(monkeypatch):
    monkeypatch.setattr(shadow.dexpaprika, "_fetch_one_interval", AsyncMock(return_value=_candles([0.9])))
    await shadow.record_signals([_pool(pool_address="poolA", price_usd=1.0, reserve=8000.0)], chain=CHAIN)
    client = FakeClient({"poolA": 1.50}, reserve_by_pool={"poolA": 8000.0})
    counts = await shadow.advance_exit_simulation(client, chain=CHAIN)  # peak now 1.50, no stop yet
    assert counts["closed_trailing_stop"] == 0
    # price falls to 1.30 -- only -13.3% from peak 1.50, still above -10%... adjust: use exactly the boundary
    client2 = FakeClient({"poolA": 1.34}, reserve_by_pool={"poolA": 8000.0})  # -10.7% from 1.50 peak
    counts2 = await shadow.advance_exit_simulation(client2, chain=CHAIN)
    assert counts2["closed_trailing_stop"] == 1
    rows = await _rows()
    assert rows[0]["final_multiplier"] > 1.0  # stopped out above entry thanks to the earlier peak


@pytest.mark.asyncio
async def test_window_low_catches_stop_missed_by_point_sample_recovery(monkeypatch):
    """18/08 -- this module was point-sample-only, exposed to the same
    detection gap already fixed in solana_pump_shadow.py/robinhood_pump_
    shadow.py on 16/08-17/08 (2 real live bugs there). A stop crossed then
    PARTIALLY RECOVERED between two ~75s polls would be invisible to a
    point-sample-only check (the recovered spot sits back above the
    threshold) -- the window low must still catch it. Fill price stays the
    theoretical threshold, never the crash extreme nor the recovered spot."""
    monkeypatch.setattr(shadow.dexpaprika, "_fetch_one_interval", AsyncMock(return_value=_candles([0.9])))
    await shadow.record_signals([_pool(pool_address="poolA", price_usd=1.0, reserve=8000.0)], chain=CHAIN)
    # Cycle 1: sets the peak at 1.16, no OHLCV configured (falls back to
    # point-sample, unaffected).
    client1 = FakeClient({"poolA": 1.16}, reserve_by_pool={"poolA": 8000.0})
    await shadow.advance_exit_simulation(client1, chain=CHAIN)
    rows = await _rows()
    assert rows[0]["peak_price"] == pytest.approx(1.16)
    assert rows[0]["exit_reason"] is None
    last_checked_epoch = shadow._epoch_of(rows[0]["last_checked_at"])
    assert last_checked_epoch is not None

    # Cycle 2: a closed candle reveals a real low of 0.90 -- past the -10%
    # stop line from peak 1.16 (threshold 1.044) -- but the point-sample spot
    # polled afterward has already recovered to 1.10, ABOVE the threshold.
    # Point-sample alone would miss this entirely.
    candles = [_candle(last_checked_epoch + 60, open_=1.16, high=1.16, low=0.90, close=1.10)]
    client2 = FakeClient(
        {"poolA": 1.10}, reserve_by_pool={"poolA": 8000.0},
        ohlcv_by_pool={"poolA": OHLCVResult(candles=candles, available=True, error=None)},
    )
    counts = await shadow.advance_exit_simulation(client2, chain=CHAIN)
    assert counts["closed_trailing_stop"] == 1
    assert client2.ohlcv_calls == ["poolA"]

    rows = await _rows()
    stop_price = 1.16 * (1 - shadow.TRAILING_STOP_PCT / 100.0)
    assert rows[0]["exit_reason"] == "trailing_stop"
    assert rows[0]["final_multiplier"] == pytest.approx(stop_price / (0.9 * 1.01))
    assert rows[0]["remaining_qty"] == 0.0


@pytest.mark.asyncio
async def test_never_produces_a_scale_out_exit_reason(monkeypatch):
    monkeypatch.setattr(shadow.dexpaprika, "_fetch_one_interval", AsyncMock(return_value=_candles([0.9])))
    await shadow.record_signals([_pool(pool_address="poolA", price_usd=1.0, reserve=8000.0)], chain=CHAIN)
    client = FakeClient({"poolA": 5.0}, reserve_by_pool={"poolA": 8000.0})  # +400%, no ladder to fill
    await shadow.advance_exit_simulation(client, chain=CHAIN)
    rows = await _rows()
    assert rows[0]["exit_reason"] is None  # still open, no rung logic exists to fire
    assert rows[0]["remaining_qty"] == 1.0


@pytest.mark.asyncio
async def test_pumpswap_pool_never_triggers_liquidity_collapse(monkeypatch):
    monkeypatch.setattr(shadow.dexpaprika, "_fetch_one_interval", AsyncMock(return_value=_candles([0.9])))
    await shadow.record_signals([_pool(pool_address="poolA", price_usd=1.0, reserve=8000.0)], chain=CHAIN)
    client = FakeClient({"poolA": 0.98}, reserve_by_pool={"poolA": 0.0}, dex_id_by_pool={"poolA": "pumpswap"})
    counts = await shadow.advance_exit_simulation(client, chain=CHAIN)
    assert counts["closed_liquidity_collapse"] == 0


@pytest.mark.asyncio
async def test_liquidity_collapse_closes_immediately(monkeypatch):
    monkeypatch.setattr(shadow.dexpaprika, "_fetch_one_interval", AsyncMock(return_value=_candles([0.9])))
    await shadow.record_signals([_pool(pool_address="poolA", price_usd=1.0, reserve=8000.0)], chain=CHAIN)
    client = FakeClient({"poolA": 0.98}, reserve_by_pool={"poolA": 3000.0})  # < 50% of 8000
    counts = await shadow.advance_exit_simulation(client, chain=CHAIN)
    assert counts["closed_liquidity_collapse"] == 1


@pytest.mark.asyncio
async def test_max_hold_closes_after_2h_with_no_stop_or_liquidity_trigger(monkeypatch):
    monkeypatch.setattr(shadow.dexpaprika, "_fetch_one_interval", AsyncMock(return_value=_candles([0.9])))
    await shadow.record_signals([_pool(pool_address="poolA", price_usd=1.0, reserve=8000.0)], chain=CHAIN)
    stale = (datetime.now(timezone.utc) - timedelta(minutes=shadow.MAX_HOLD_MINUTES + 1)).isoformat()
    async with aiosqlite.connect(shadow._db_path()) as db:
        await db.execute(f"UPDATE {shadow.TABLE} SET detected_at = ?", (stale,))
        await db.commit()
    client = FakeClient({"poolA": 0.95}, reserve_by_pool={"poolA": 8000.0})  # -5%, above the -10% stop
    counts = await shadow.advance_exit_simulation(client, chain=CHAIN)
    assert counts["closed_max_hold"] == 1


# --- summary -----------------------------------------------------------------

@pytest.mark.asyncio
async def test_summary_computes_winrate_and_multiplier():
    await shadow._ensure_table()
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


# --- chain_pnl_summary_realistic ---------------------------------------------

@pytest.mark.asyncio
async def test_pnl_realistic_closed_position_counted():
    await shadow._ensure_table()
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
    assert pnl["capital_deployed_usd"] == pytest.approx(shadow.SIMULATED_TRADE_SIZE_USD)


@pytest.mark.asyncio
async def test_pnl_realistic_unreachable_liquidity_never_fabricated():
    await shadow._ensure_table()
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
    await shadow._ensure_table()
    async with aiosqlite.connect(shadow._db_path()) as db:
        # bought (realistic_entry_price set) but exit turned unsellable
        # (realistic_final_multiplier NULL, realistic_realized_proceeds 0)
        await db.execute(
            f"INSERT INTO {shadow.TABLE} (pool_address, chain, detected_at, entry_price, "
            "realistic_entry_price, exit_reason, realistic_final_multiplier, realistic_realized_proceeds) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("p1", CHAIN, datetime.now(timezone.utc).isoformat(), 1.0, 1.0, "liquidity_collapse", None, 0.0),
        )
        await db.commit()
    pnl = await shadow.chain_pnl_summary_realistic(CHAIN)
    assert pnl["stranded"] == 1
    assert pnl["total_pnl_units"] == pytest.approx(-1.0)  # total loss, never dropped from the aggregate


# --- candle archiving (18/08, operator-directed) -----------------------------

@pytest.mark.asyncio
async def test_record_signals_archives_the_before_candles(monkeypatch):
    candles = _range_candles(low=0.85, high=1.0, last_close=0.87)
    monkeypatch.setattr(shadow.dexpaprika, "_fetch_one_interval", AsyncMock(return_value=candles))
    logged = await shadow.record_signals([_pool(price_usd=0.87)], chain=CHAIN)
    assert logged == 1
    rows = await _rows()
    archived = await archive.get_candles(
        module="solana_support_bounce", position_id=rows[0]["id"], phase="before",
    )
    assert len(archived) == len(candles)
    assert {c["candle_ts"] for c in archived} == {c.ts for c in candles}


@pytest.mark.asyncio
async def test_advance_exit_simulation_archives_the_after_candles(monkeypatch):
    monkeypatch.setattr(shadow.dexpaprika, "_fetch_one_interval", AsyncMock(return_value=_candles([0.9])))
    await shadow.record_signals([_pool(pool_address="poolA", price_usd=1.0, reserve=8000.0)], chain=CHAIN)
    rows = await _rows()
    position_id = rows[0]["id"]
    detected_epoch = shadow._epoch_of(rows[0]["detected_at"])

    ohlcv_candles = [
        _candle(detected_epoch + 60, open_=1.0, high=1.05, low=0.98, close=1.02),
        _candle(detected_epoch + 120, open_=1.02, high=1.08, low=1.0, close=1.05),
    ]
    client = FakeClient(
        {"poolA": 1.05}, reserve_by_pool={"poolA": 8000.0},
        ohlcv_by_pool={"poolA": OHLCVResult(candles=ohlcv_candles, available=True, error=None)},
    )
    await shadow.advance_exit_simulation(client, chain=CHAIN)

    archived = await archive.get_candles(module="solana_support_bounce", position_id=position_id, phase="after")
    assert len(archived) == 2
    assert {c["candle_ts"] for c in archived} == {c.ts for c in ohlcv_candles}

    # A second check overlapping the same window must never duplicate rows.
    await shadow.advance_exit_simulation(client, chain=CHAIN)
    archived_again = await archive.get_candles(
        module="solana_support_bounce", position_id=position_id, phase="after",
    )
    assert len(archived_again) == 2
