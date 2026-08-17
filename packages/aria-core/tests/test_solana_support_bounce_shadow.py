"""Support-bounce Solana shadow (17/08) -- h1>0% + established pool (age
>=70min, no ceiling) + price near the low of its own last-10x5min-candle
range. Exit: -10% trailing stop only, no scale-out ladder. Same isolated-
tmp-db + injected-client pattern as the other shadow test files."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import aiosqlite
import pytest

from aria_core import solana_support_bounce_shadow as shadow
from aria_core.services.dexpaprika import Candle
from aria_core.services.geckoterminal import PoolSnapshot, TrendingPool
from aria_core.services.rugcheck import RugCheckReport

CHAIN = "solana"


@pytest.fixture(autouse=True)
async def _tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(shadow, "DB_PATH", str(tmp_path / "shadow.db"))
    shadow._ensured_db_paths.clear()
    await shadow._ensure_table()
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
    """10 candles whose low values are exactly `lows` (padded/truncated to
    10), high/open/close held near base_price so only the low varies."""
    lows = (lows + [base_price] * 10)[:10]
    return [
        Candle(ts=i, open=base_price, high=base_price * 1.01, low=low, close=base_price, volume=100.0)
        for i, low in enumerate(lows)
    ]


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
    def __init__(self, price_by_pool, reserve_by_pool=None, dex_id_by_pool=None):
        self._prices = price_by_pool
        self._reserves = dict(reserve_by_pool or {})
        self._dex_ids = dict(dex_id_by_pool or {})
        self.calls = []

    async def get_pool_snapshot(self, pool_address, *, network="solana"):
        self.calls.append(pool_address)
        price = self._prices.get(pool_address)
        if price is None:
            return PoolSnapshot(pool_address=pool_address, available=False, error="unavailable")
        reserve = self._reserves.get(pool_address, 1000.0)
        dex_id = self._dex_ids.get(pool_address)
        return PoolSnapshot(pool_address=pool_address, price_usd=price, reserve_usd=reserve, available=True, dex_id=dex_id)


# --- record_signals: entry criteria -----------------------------------------

@pytest.mark.asyncio
async def test_h1_at_zero_or_below_rejected(monkeypatch):
    monkeypatch.setattr(shadow.dexpaprika, "_fetch_one_interval", AsyncMock(return_value=_candles([0.9])))
    logged = await shadow.record_signals([_pool(h1=0.0)], chain=CHAIN)
    assert logged == 0
    logged = await shadow.record_signals([_pool(pool_address="poolB", h1=-5.0)], chain=CHAIN)
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
    # entry price 1.0, range low 0.85 -> distance = (1.0/0.85 - 1)*100 = 17.6% <= 20%
    monkeypatch.setattr(shadow.dexpaprika, "_fetch_one_interval", AsyncMock(return_value=_candles([0.85])))
    logged = await shadow.record_signals([_pool(price_usd=1.0)], chain=CHAIN)
    assert logged == 1
    rows = await _rows()
    assert rows[0]["range_low_10c"] == 0.85
    assert rows[0]["distance_from_support_pct"] == pytest.approx((1.0 / 0.85 - 1) * 100, rel=1e-6)


@pytest.mark.asyncio
async def test_price_beyond_20pct_of_support_rejected(monkeypatch):
    # entry price 1.0, range low 0.7 -> distance = 42.9% > 20%
    monkeypatch.setattr(shadow.dexpaprika, "_fetch_one_interval", AsyncMock(return_value=_candles([0.7])))
    logged = await shadow.record_signals([_pool(price_usd=1.0)], chain=CHAIN)
    assert logged == 0


@pytest.mark.asyncio
async def test_uses_fresh_candle_close_not_stale_scan_price(monkeypatch):
    # 17/08, real bug caught live by the operator (Niles) -- pool.price_usd
    # is a STALE snapshot from the broad discovery scan; under real
    # DexPaprika contention the candle fetch for a given candidate can lag
    # that snapshot by minutes. Here pool.price_usd=0.5 is deliberately
    # stale/low (would give distance -44.4% vs range_low=0.9 -> wrongly
    # rejected), but the last candle's FRESH close=0.95 sits right at
    # support (distance +5.6% <= 20%) -- the fresh price must win.
    stale_price = 0.5
    fresh_close = 0.95
    candles = [
        Candle(ts=i, open=1.0, high=1.0, low=0.9, close=1.0, volume=100.0)
        for i in range(9)
    ] + [Candle(ts=9, open=0.92, high=0.96, low=0.9, close=fresh_close, volume=100.0)]
    monkeypatch.setattr(shadow.dexpaprika, "_fetch_one_interval", AsyncMock(return_value=candles))
    logged = await shadow.record_signals([_pool(price_usd=stale_price)], chain=CHAIN)
    assert logged == 1
    rows = await _rows()
    assert rows[0]["entry_price"] == pytest.approx(fresh_close)
    assert rows[0]["distance_from_support_pct"] == pytest.approx((fresh_close / 0.9 - 1) * 100, rel=1e-6)


@pytest.mark.asyncio
async def test_price_below_support_low_rejected(monkeypatch):
    # entry price 1.0, range low 1.2 (all 10 candles) -> price is BELOW the
    # 10-candle low (distance = -16.7%, negative): a breakdown, not a
    # bounce -- must be rejected even though |distance| <= 20%.
    monkeypatch.setattr(shadow.dexpaprika, "_fetch_one_interval", AsyncMock(return_value=_candles([1.2] * 10)))
    logged = await shadow.record_signals([_pool(price_usd=1.0)], chain=CHAIN)
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


# --- advance_exit_simulation: -10% trailing stop, no ladder -----------------

@pytest.mark.asyncio
async def test_trailing_stop_closes_full_position_no_partial(monkeypatch):
    monkeypatch.setattr(shadow.dexpaprika, "_fetch_one_interval", AsyncMock(return_value=_candles([0.9])))
    await shadow.record_signals([_pool(pool_address="poolA", price_usd=1.0, reserve=8000.0)], chain=CHAIN)
    # peak never rises (first check IS the peak at 1.0), price crashes -15% -> past -10% stop
    client = FakeClient({"poolA": 0.85}, reserve_by_pool={"poolA": 8000.0})
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
