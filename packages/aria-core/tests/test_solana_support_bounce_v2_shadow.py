"""Support-bounce v2 Solana shadow (18/08) -- parallel variant of
test_solana_support_bounce_shadow.py, adapted for the 3 recalibrated
constants (MAX_RANGE_RATIO 3.0->1.5, TRAILING_STOP_PCT 10.0->5.0, new
MAX_H1_PCT=20.0 ceiling). See solana_support_bounce_v2_shadow.py's own
module docstring for the real-data basis of each change. Every test not
touching one of these 3 differs from the original test file follows the
SAME logic/intent, only the numeric fixtures differ where the tightened
constant changes what should pass/fail."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import aiosqlite
import pytest

from aria_core import shadow_candle_archive as archive
from aria_core import shadow_snapshot_archive as snapshot_archive
from aria_core import solana_support_bounce_v2_shadow as shadow
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
    monkeypatch.setattr(snapshot_archive, "DB_PATH", str(tmp_path / "shadow.db"))
    snapshot_archive._ensured_db_paths.clear()
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
    lows = ([base_price] * 10 + lows)[-10:]
    return [
        Candle(ts=i, open=low, high=low * 1.1, low=low, close=low * 1.01, volume=100.0)
        for i, low in enumerate(lows)
    ]


def _range_candles(*, low: float, high: float, last_close: float) -> list[Candle]:
    filler = [
        Candle(ts=i, open=low, high=high, low=low, close=(low + high) / 2, volume=100.0)
        for i in range(9)
    ]
    return filler + [Candle(ts=9, open=last_close, high=high, low=low, close=last_close, volume=100.0)]


def _candle(ts: float, *, open_: float, high: float, low: float, close: float, volume: float = 0.0) -> Candle:
    """Exit-side OHLCV candle (18/08 window-detection fix) -- distinct from
    the entry-side 10-candle support window built above."""
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
async def test_h1_mildly_negative_still_qualifies(monkeypatch):
    monkeypatch.setattr(shadow.dexpaprika, "_fetch_one_interval", AsyncMock(return_value=_candles([0.9])))
    logged = await shadow.record_signals([_pool(h1=-4.9)], chain=CHAIN)
    assert logged == 1
    logged = await shadow.record_signals([_pool(pool_address="poolB", h1=0.0)], chain=CHAIN)
    assert logged == 1


@pytest.mark.asyncio
async def test_h1_at_20pct_ceiling_still_qualifies(monkeypatch):
    """18/08, new v2-only ceiling -- boundary is inclusive (`h1 > MAX_H1_PCT`
    rejects, so exactly 20.0 must still pass)."""
    monkeypatch.setattr(shadow.dexpaprika, "_fetch_one_interval", AsyncMock(return_value=_candles([0.9])))
    logged = await shadow.record_signals([_pool(h1=shadow.MAX_H1_PCT)], chain=CHAIN)
    assert logged == 1


@pytest.mark.asyncio
async def test_h1_above_20pct_ceiling_rejected(monkeypatch):
    """18/08, new v2-only ceiling -- real data showed the 20-60% h1 bucket
    net-underperforming (17% winrate, x0.97 avg) on the original pocket's
    first 91 closures. The original has no such ceiling at all; this is the
    one entry criterion unique to v2, not just a tightened original value."""
    logged = await shadow.record_signals([_pool(h1=shadow.MAX_H1_PCT + 0.1)], chain=CHAIN)
    assert logged == 0


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
async def test_price_within_20pct_of_support_qualifies(monkeypatch):
    # range [0.85, 1.0], close 0.87 -> position = (0.87-0.85)/(1.0-0.85)*100 = 13.3% <= 20%
    # ratio 1.0/0.85 = 1.176x, under v2's tightened MAX_RANGE_RATIO=1.5
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
    # range [0.85, 1.0] (ratio 1.176x, under cap), close 0.978 -> position
    # = (0.978-0.85)/(1.0-0.85)*100 = 85.3% > 20%
    candles = _range_candles(low=0.85, high=1.0, last_close=0.978)
    monkeypatch.setattr(shadow.dexpaprika, "_fetch_one_interval", AsyncMock(return_value=candles))
    logged = await shadow.record_signals([_pool(price_usd=0.978)], chain=CHAIN)
    assert logged == 0


@pytest.mark.asyncio
async def test_price_at_range_high_rejected(monkeypatch):
    candles = _range_candles(low=0.469501, high=0.561006, last_close=0.561006)
    monkeypatch.setattr(shadow.dexpaprika, "_fetch_one_interval", AsyncMock(return_value=candles))
    logged = await shadow.record_signals([_pool(price_usd=0.561006)], chain=CHAIN)
    assert logged == 0


@pytest.mark.asyncio
async def test_uses_fresh_candle_close_not_stale_scan_price(monkeypatch):
    stale_price = 0.99
    fresh_close = 0.91
    candles = _range_candles(low=0.9, high=1.0, last_close=fresh_close)  # ratio 1.11x, under cap
    monkeypatch.setattr(shadow.dexpaprika, "_fetch_one_interval", AsyncMock(return_value=candles))
    logged = await shadow.record_signals([_pool(price_usd=stale_price)], chain=CHAIN)
    assert logged == 1
    rows = await _rows()
    assert rows[0]["entry_price"] == pytest.approx(fresh_close)
    assert rows[0]["distance_from_support_pct"] == pytest.approx((fresh_close - 0.9) / (1.0 - 0.9) * 100, rel=1e-6)


@pytest.mark.asyncio
async def test_price_below_support_low_rejected(monkeypatch):
    candles = _range_candles(low=1.0, high=1.1, last_close=0.95)  # ratio 1.1x, under cap
    monkeypatch.setattr(shadow.dexpaprika, "_fetch_one_interval", AsyncMock(return_value=candles))
    logged = await shadow.record_signals([_pool(price_usd=0.95)], chain=CHAIN)
    assert logged == 0


@pytest.mark.asyncio
async def test_extreme_range_ratio_rejected(monkeypatch):
    candles = _range_candles(low=0.000001, high=0.01, last_close=0.000001)  # ratio 10000x
    monkeypatch.setattr(shadow.dexpaprika, "_fetch_one_interval", AsyncMock(return_value=candles))
    logged = await shadow.record_signals([_pool(price_usd=0.000001)], chain=CHAIN)
    assert logged == 0


@pytest.mark.asyncio
async def test_range_ratio_within_new_tighter_cap_still_qualifies(monkeypatch):
    # range [1.0, 1.4] -> ratio 1.4x, under v2's MAX_RANGE_RATIO=1.5;
    # close=1.02 -> position = (1.02-1.0)/(1.4-1.0)*100 = 5%, within tolerance.
    candles = _range_candles(low=1.0, high=1.4, last_close=1.02)
    monkeypatch.setattr(shadow.dexpaprika, "_fetch_one_interval", AsyncMock(return_value=candles))
    logged = await shadow.record_signals([_pool(price_usd=1.02)], chain=CHAIN)
    assert logged == 1


@pytest.mark.asyncio
async def test_range_ratio_above_new_tighter_cap_rejected(monkeypatch):
    """18/08 -- the real point of v2's change: a ratio that WOULD have
    qualified under the original's MAX_RANGE_RATIO=3.0 must now be rejected
    under v2's tightened 1.5. Range [1.0, 2.0] -> ratio 2.0x (above 1.5,
    below the original's 3.0); close=1.05 -> position = 5%, well within
    the distance tolerance on its own -- the ratio is what must reject it."""
    candles = _range_candles(low=1.0, high=2.0, last_close=1.05)
    monkeypatch.setattr(shadow.dexpaprika, "_fetch_one_interval", AsyncMock(return_value=candles))
    logged = await shadow.record_signals([_pool(price_usd=1.05)], chain=CHAIN)
    assert logged == 0


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


# --- advance_exit_simulation: -5% trailing stop (tightened), no ladder -----

@pytest.mark.asyncio
async def test_trailing_stop_closes_full_position_no_partial(monkeypatch):
    monkeypatch.setattr(shadow.dexpaprika, "_fetch_one_interval", AsyncMock(return_value=_candles([0.9])))
    await shadow.record_signals([_pool(pool_address="poolA", price_usd=1.0, reserve=8000.0)], chain=CHAIN)
    # entry_price = 0.9*1.01 = 0.909; peak never rises (first check IS the
    # peak), price crashes to 0.75 (-17.5% from entry) -> past the -5% stop
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
    # price falls to 1.40 -- -6.7% from peak 1.50, past the -5% stop
    client2 = FakeClient({"poolA": 1.40}, reserve_by_pool={"poolA": 8000.0})
    counts2 = await shadow.advance_exit_simulation(client2, chain=CHAIN)
    assert counts2["closed_trailing_stop"] == 1
    rows = await _rows()
    assert rows[0]["final_multiplier"] > 1.0  # stopped out above entry thanks to the earlier peak


@pytest.mark.asyncio
async def test_window_low_catches_stop_missed_by_point_sample_recovery(monkeypatch):
    """Same 18/08 window-detection fix as the original module, adapted for
    v2's tighter -5% stop. See the original's own test for the full
    rationale (2 real live-bug precedents this closes)."""
    monkeypatch.setattr(shadow.dexpaprika, "_fetch_one_interval", AsyncMock(return_value=_candles([0.9])))
    await shadow.record_signals([_pool(pool_address="poolA", price_usd=1.0, reserve=8000.0)], chain=CHAIN)
    client1 = FakeClient({"poolA": 1.16}, reserve_by_pool={"poolA": 8000.0})
    await shadow.advance_exit_simulation(client1, chain=CHAIN)
    rows = await _rows()
    assert rows[0]["peak_price"] == pytest.approx(1.16)
    assert rows[0]["exit_reason"] is None
    last_checked_epoch = shadow._epoch_of(rows[0]["last_checked_at"])
    assert last_checked_epoch is not None

    # Real low 1.05 -- past the -5% stop line from peak 1.16 (threshold
    # 1.102) -- but the point-sample spot polled afterward has already
    # recovered to 1.12, ABOVE the threshold. Point-sample alone would miss
    # this entirely.
    candles = [_candle(last_checked_epoch + 60, open_=1.16, high=1.16, low=1.05, close=1.12)]
    client2 = FakeClient(
        {"poolA": 1.12}, reserve_by_pool={"poolA": 8000.0},
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
    # peak is set to current on this SAME check (peak was None -> entry,
    # then max(entry, 0.95)=0.95), so trailing stop can never fire here
    # regardless of TRAILING_STOP_PCT -- same property as the original.
    client = FakeClient({"poolA": 0.95}, reserve_by_pool={"poolA": 8000.0})
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
        module="solana_support_bounce_v2", position_id=rows[0]["id"], phase="before",
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

    archived = await archive.get_candles(module="solana_support_bounce_v2", position_id=position_id, phase="after")
    assert len(archived) == 2
    assert {c["candle_ts"] for c in archived} == {c.ts for c in ohlcv_candles}


# --- 18/08 exhaustive-capture pass -------------------------------------------

@pytest.mark.asyncio
async def test_multi_window_distance_persisted_alongside_official_10c(monkeypatch):
    candles = _range_candles(low=0.85, high=1.0, last_close=0.87)
    monkeypatch.setattr(shadow.dexpaprika, "_fetch_one_interval", AsyncMock(return_value=candles))
    logged = await shadow.record_signals([_pool(price_usd=0.87)], chain=CHAIN)
    assert logged == 1
    rows = await _rows()
    row = rows[0]
    assert row["distance_from_support_pct_5"] == pytest.approx(row["distance_from_support_pct"], rel=1e-6)
    assert row["distance_from_support_pct_15"] is None
    assert row["distance_from_support_pct_20"] is None
    assert row["distance_from_support_pct_30"] is None


@pytest.mark.asyncio
async def test_extra_dexpaprika_fields_persisted(monkeypatch):
    pool = TrendingPool(
        pool_address="poolA", token_address="tokA", symbol="BOUNCE",
        price_usd=0.87,
        price_change_pct={"h1": 10.0, "m5": 1.5, "h6": 8.0, "h24": 25.0},
        transactions_m15=None, volume_usd_m15=None,
        reserve_usd=8000.0,
        pool_created_at=datetime.now(timezone.utc) - timedelta(minutes=90),
        dex_id="raydium", dex_name="Raydium", volume_usd_24h=54321.0, transactions_24h=777,
    )
    candles = _range_candles(low=0.85, high=1.0, last_close=0.87)
    monkeypatch.setattr(shadow.dexpaprika, "_fetch_one_interval", AsyncMock(return_value=candles))
    logged = await shadow.record_signals([pool], chain=CHAIN)
    assert logged == 1
    rows = await _rows()
    row = rows[0]
    assert row["m5_pct"] == pytest.approx(1.5)
    assert row["h6_pct"] == pytest.approx(8.0)
    assert row["h24_pct"] == pytest.approx(25.0)
    assert row["dex_id"] == "raydium"
    assert row["volume_usd_24h"] == pytest.approx(54321.0)
    assert row["transactions_24h"] == 777


@pytest.mark.asyncio
async def test_advance_exit_simulation_archives_a_full_snapshot(monkeypatch):
    monkeypatch.setattr(shadow.dexpaprika, "_fetch_one_interval", AsyncMock(return_value=_candles([0.9])))
    await shadow.record_signals([_pool(pool_address="poolA", price_usd=1.0, reserve=8000.0)], chain=CHAIN)
    rows = await _rows()
    position_id = rows[0]["id"]

    client = FakeClient({"poolA": 1.05}, reserve_by_pool={"poolA": 8000.0}, dex_id_by_pool={"poolA": "raydium"})
    await shadow.advance_exit_simulation(client, chain=CHAIN)

    archived = await snapshot_archive.get_snapshots(module="solana_support_bounce_v2", position_id=position_id)
    assert len(archived) == 1
    assert archived[0]["price_usd"] == pytest.approx(1.05)
    assert archived[0]["reserve_usd"] == pytest.approx(8000.0)
    assert archived[0]["dex_id"] == "raydium"

    await shadow.advance_exit_simulation(client, chain=CHAIN)
    archived_again = await snapshot_archive.get_snapshots(module="solana_support_bounce_v2", position_id=position_id)
    assert len(archived_again) == 2
