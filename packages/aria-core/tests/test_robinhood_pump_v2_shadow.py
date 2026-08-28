"""robinhood_pump_v2_shadow.py -- aggressive scale-out variant (specs/004-
shadow-robinhood T005). Same isolated-tmp-db + injected-client test doctrine
as test_robinhood_pump_shadow.py; v2 shares v1's DB_PATH for the regime-gate
table (both point at the same tmp shadow.db, only the position TABLE
differs) so the fixture patches both modules' DB_PATH to the same path."""
from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone

import aiosqlite
import pytest

from aria_core import robinhood_pump_shadow as shadow_v1
from aria_core import robinhood_pump_v2_shadow as v2
from aria_core.services.dexscreener import PairSnapshot
from aria_core.services.geckoterminal import OHLCVResult, PoolSnapshot, TrendingPool

CHAIN = "robinhood"


class _NetworkGuardClient:
    async def get_ohlcv(self, *_args, **_kwargs):
        return OHLCVResult(candles=[], available=False, error="test-isolation: no client injected")

    async def get_pool_snapshot(self, *_args, **_kwargs):
        return PoolSnapshot(pool_address="", available=False, error="test-isolation: no client injected")


@pytest.fixture(autouse=True)
def _no_real_network_client(monkeypatch):
    monkeypatch.setattr(v2, "geckoterminal_client", _NetworkGuardClient())


@pytest.fixture(autouse=True)
def _no_dexscreener_primary(monkeypatch):
    # _snapshot_with_fallback (imported from v1, called by v2) tries
    # DexScreener first -- empty pairs list forces the fallback to whatever
    # FakeClient a test injects, same doctrine as v1's own test file.
    async def _empty_pairs(*_args, **_kwargs):
        return []

    monkeypatch.setattr(shadow_v1.dexscreener, "fetch_token_pairs", _empty_pairs)


@pytest.fixture(autouse=True)
async def _tmp_db(tmp_path, monkeypatch):
    path = str(tmp_path / "shadow.db")
    # v2's own table AND v1's regime-candidates table (reused, not
    # duplicated -- see module docstring) live in the SAME file, matching
    # the real prod layout (one shadow.db, many tables).
    monkeypatch.setattr(v2, "DB_PATH", path)
    monkeypatch.setattr(shadow_v1, "DB_PATH", path)
    v2._ensured_db_paths.clear()
    shadow_v1._ensured_db_paths.clear()
    shadow_v1._ensured_regime_candidates_db_paths.clear()
    await v2._ensure_table()
    yield
    v2._ensured_db_paths.clear()
    shadow_v1._ensured_db_paths.clear()
    shadow_v1._ensured_regime_candidates_db_paths.clear()


@pytest.fixture(autouse=True)
def _stock_token_never_blocks(monkeypatch):
    async def _never_a_stock_token(contract, chain):
        return False

    monkeypatch.setattr(v2, "is_stock_token", _never_a_stock_token)


@pytest.fixture(autouse=True)
def _discovery_only_never_blocks(monkeypatch):
    monkeypatch.setattr(v2.shadow_discovery_only, "is_discovery_only", lambda: False)


@pytest.fixture(autouse=True)
def _chain_regime_never_toxic(monkeypatch):
    async def _no_regime(_chain):
        return None

    monkeypatch.setattr(v2.chain_liquidity_regime, "latest_regime", _no_regime)


async def _rows():
    async with aiosqlite.connect(v2._db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(f"SELECT * FROM {v2.TABLE}")
        return [dict(r) for r in await cur.fetchall()]


def _pool(
    *, pool_address="poolA", token_address="tokA", symbol="PUMP",
    price_usd=1.0, m5=30.0, reserve=100000.0, pool_created_at=None, dex_id="uniswap_v3",
) -> TrendingPool:
    return TrendingPool(
        pool_address=pool_address, token_address=token_address, symbol=symbol,
        price_usd=price_usd,
        price_change_pct={"m5": m5, "m15": m5 + 5, "m30": m5 + 10, "h1": m5 + 15, "h6": m5 + 25, "h24": m5 + 35},
        transactions_m15={"buys": 40, "sells": 10, "buyers": 20, "sellers": 5},
        volume_usd_m15=5000.0,
        reserve_usd=reserve,
        pool_created_at=pool_created_at if pool_created_at is not None else datetime.now(timezone.utc),
        dex_id=dex_id,
    )


class FakeClient:
    def __init__(self, price_by_pool: dict[str, float | None], reserve_by_pool: dict[str, float] | None = None):
        self._prices = price_by_pool
        self._reserves = dict(reserve_by_pool or {})

    async def get_pool_snapshot(self, pool_address, *, network="robinhood"):
        price = self._prices.get(pool_address)
        if price is None:
            return PoolSnapshot(pool_address=pool_address, available=False, error="unavailable")
        reserve = self._reserves.get(pool_address, 100000.0)
        return PoolSnapshot(pool_address=pool_address, price_usd=price, reserve_usd=reserve, available=True)

    async def get_ohlcv(self, *_args, **_kwargs):
        return OHLCVResult(candles=[], available=False, error="v2 is spot-only, never called")


# --- record_signals -------------------------------------------------------

@pytest.mark.asyncio
async def test_record_signals_logs_pool_above_threshold():
    logged = await v2.record_signals([_pool(m5=30.0)], chain=CHAIN)
    assert logged == 1
    rows = await _rows()
    assert len(rows) == 1
    assert rows[0]["pool_address"] == "poolA"
    assert rows[0]["entry_price"] == 1.0
    assert rows[0]["exit_reason"] is None
    # v2's own ladder step (+15%), not v1's (+25%)
    assert rows[0]["next_scale_level"] == pytest.approx(1.15)


@pytest.mark.asyncio
async def test_record_signals_ignores_pool_below_threshold():
    logged = await v2.record_signals([_pool(m5=10.0)], chain=CHAIN)
    assert logged == 0
    assert await _rows() == []


@pytest.mark.asyncio
async def test_record_signals_rejects_pool_older_than_max_age():
    old = datetime.now(timezone.utc) - timedelta(minutes=v2.MAX_POOL_AGE_MINUTES + 1)
    logged = await v2.record_signals([_pool(m5=40.0, pool_created_at=old)], chain=CHAIN)
    assert logged == 0


@pytest.mark.asyncio
async def test_record_signals_rejects_thin_liquidity():
    logged = await v2.record_signals([_pool(m5=40.0, reserve=100.0)], chain=CHAIN)
    assert logged == 0


# --- specs/010-robinhood-dayzero-liquidity: entry-mode-aware liquidity floor -

@pytest.mark.asyncio
async def test_day_zero_entry_mode_uses_the_lower_liquidity_floor():
    reserve = (v2.MIN_LIQUIDITY_USD_DAY_ZERO + v2.MIN_LIQUIDITY_USD) / 2
    assert reserve < v2.MIN_LIQUIDITY_USD
    logged = await v2.record_signals([_pool(reserve=reserve)], chain=CHAIN, entry_mode="day_zero")
    assert logged == 1


@pytest.mark.asyncio
async def test_non_day_zero_entry_mode_still_uses_the_dexpaprika_floor():
    reserve = (v2.MIN_LIQUIDITY_USD_DAY_ZERO + v2.MIN_LIQUIDITY_USD) / 2
    logged = await v2.record_signals([_pool(reserve=reserve)], chain=CHAIN)
    assert logged == 0


@pytest.mark.asyncio
async def test_day_zero_entry_mode_still_rejects_near_zero_liquidity():
    logged = await v2.record_signals([_pool(reserve=6.40)], chain=CHAIN, entry_mode="day_zero")
    assert logged == 0


@pytest.mark.asyncio
async def test_record_signals_rejects_unknown_age():
    pool = dataclasses.replace(_pool(m5=40.0), pool_created_at=None)
    logged = await v2.record_signals([pool], chain=CHAIN)
    assert logged == 0


@pytest.mark.asyncio
async def test_record_signals_excludes_stock_tokens(monkeypatch):
    async def _is_stock(contract, chain):
        return contract == "tokA"

    monkeypatch.setattr(v2, "is_stock_token", _is_stock)
    logged = await v2.record_signals([_pool(m5=40.0)], chain=CHAIN)
    assert logged == 0


@pytest.mark.asyncio
async def test_record_signals_dedupes_while_open():
    await v2.record_signals([_pool(m5=30.0)], chain=CHAIN)
    logged_again = await v2.record_signals([_pool(m5=45.0)], chain=CHAIN)
    assert logged_again == 0
    assert len(await _rows()) == 1


@pytest.mark.asyncio
async def test_record_signals_skips_new_candidate_when_pocket_at_cap():
    async with aiosqlite.connect(v2._db_path()) as db:
        for i in range(25):
            await db.execute(
                f"INSERT INTO {v2.TABLE} (pool_address, chain, detected_at, entry_price) "
                "VALUES (?, ?, '2026-08-28T00:00:00', 1.0)",
                (f"cap_pool_{i}", CHAIN),
            )
        await db.commit()
    logged = await v2.record_signals([_pool(pool_address="new_pool", m5=30.0)], chain=CHAIN)
    assert logged == 0
    assert "new_pool" not in {r["pool_address"] for r in await _rows()}


@pytest.mark.asyncio
async def test_record_signals_still_logs_below_pocket_cap():
    async with aiosqlite.connect(v2._db_path()) as db:
        for i in range(24):
            await db.execute(
                f"INSERT INTO {v2.TABLE} (pool_address, chain, detected_at, entry_price) "
                "VALUES (?, ?, '2026-08-28T00:00:00', 1.0)",
                (f"cap_pool_{i}", CHAIN),
            )
        await db.commit()
    logged = await v2.record_signals([_pool(pool_address="new_pool", m5=30.0)], chain=CHAIN)
    assert logged == 1
    assert "new_pool" in {r["pool_address"] for r in await _rows()}


@pytest.mark.asyncio
async def test_record_signals_independent_from_v1_table():
    """v2 opens its own row even when v1 already has an open signal for the
    same pool -- two independent parallel ledgers, per the module docstring."""
    await shadow_v1._ensure_table()
    await shadow_v1.record_signals([_pool(m5=30.0)], chain=CHAIN)
    logged = await v2.record_signals([_pool(m5=30.0)], chain=CHAIN)
    assert logged == 1


# --- advance_exit_simulation ----------------------------------------------

@pytest.mark.asyncio
async def test_advance_exit_scale_out_sells_half_at_first_rung():
    await v2.record_signals([_pool(pool_address="poolA", price_usd=1.0, m5=30.0)], chain=CHAIN)
    client = FakeClient({"poolA": 1.16})  # crossed the +15% rung (1.15)
    stats = await v2.advance_exit_simulation(client, chain=CHAIN)
    assert stats["scale_out_fills"] == 1
    rows = await _rows()
    assert rows[0]["remaining_qty"] == pytest.approx(0.5)
    assert rows[0]["realized_proceeds"] == pytest.approx(0.5 * 1.15)
    assert rows[0]["exit_reason"] is None


@pytest.mark.asyncio
async def test_advance_exit_scale_out_complete_closes_row():
    await v2.record_signals([_pool(pool_address="poolA", price_usd=1.0, m5=30.0)], chain=CHAIN)
    # Successive +15% rungs, each selling 50% of what remains, converges under
    # the dust fraction after enough rungs are crossed in one pass.
    price = 1.0
    for _ in range(12):
        price *= 1.16
    client = FakeClient({"poolA": price})
    stats = await v2.advance_exit_simulation(client, chain=CHAIN)
    assert stats["closed_scale_out_complete"] == 1
    rows = await _rows()
    assert rows[0]["exit_reason"] == "scale_out_complete"
    assert rows[0]["remaining_qty"] == 0.0
    assert rows[0]["closed_at"] is not None


@pytest.mark.asyncio
async def test_advance_exit_trailing_stop_fires_from_peak():
    await v2.record_signals([_pool(pool_address="poolA", price_usd=1.0, m5=30.0)], chain=CHAIN)
    client = FakeClient({"poolA": 1.16})
    await v2.advance_exit_simulation(client, chain=CHAIN)  # one scale-out fill, peak=1.16
    client2 = FakeClient({"poolA": 1.16 * (1 - v2.TRAILING_STOP_PCT / 100.0) - 0.001})
    stats = await v2.advance_exit_simulation(client2, chain=CHAIN)
    assert stats["closed_trailing_stop"] == 1
    rows = await _rows()
    assert rows[0]["exit_reason"] == "trailing_stop"
    assert rows[0]["remaining_qty"] == 0.0


@pytest.mark.asyncio
async def test_advance_exit_max_hold_closes_stale_position():
    old = datetime.now(timezone.utc) - timedelta(minutes=v2.MAX_HOLD_MINUTES + 1)
    await v2.record_signals([_pool(pool_address="poolA", price_usd=1.0, m5=30.0)], chain=CHAIN)
    async with aiosqlite.connect(v2._db_path()) as db:
        await db.execute(f"UPDATE {v2.TABLE} SET detected_at = ?", (old.isoformat(),))
        await db.commit()
    client = FakeClient({"poolA": 1.05})  # below the first rung, never scales out
    stats = await v2.advance_exit_simulation(client, chain=CHAIN)
    assert stats["closed_max_hold"] == 1


@pytest.mark.asyncio
async def test_advance_exit_liquidity_collapse_force_closes():
    await v2.record_signals([_pool(pool_address="poolA", price_usd=1.0, m5=30.0, reserve=10000.0)], chain=CHAIN)
    client = FakeClient({"poolA": 1.05}, reserve_by_pool={"poolA": 1000.0})  # -90%, past the 50% floor
    stats = await v2.advance_exit_simulation(client, chain=CHAIN)
    assert stats["closed_liquidity_collapse"] == 1


@pytest.mark.asyncio
async def test_advance_exit_suspect_peak_jump_does_not_ratchet_instantly():
    await v2.record_signals([_pool(pool_address="poolA", price_usd=1.0, m5=30.0)], chain=CHAIN)
    client = FakeClient({"poolA": 1.0 * v2._PEAK_JUMP_SUSPECT_RATIO * 2})
    await v2.advance_exit_simulation(client, chain=CHAIN)
    rows = await _rows()
    assert rows[0]["peak_price"] == pytest.approx(1.0)  # not ratcheted yet
    assert rows[0]["pending_peak_price"] is not None


@pytest.mark.asyncio
async def test_advance_exit_normal_jump_still_ratchets_instantly():
    await v2.record_signals([_pool(pool_address="poolA", price_usd=1.0, m5=30.0)], chain=CHAIN)
    client = FakeClient({"poolA": 1.16})  # under the suspect ratio -- instant, unchanged behavior
    await v2.advance_exit_simulation(client, chain=CHAIN)
    rows = await _rows()
    assert rows[0]["peak_price"] == pytest.approx(1.16)
    assert rows[0]["pending_peak_price"] is None


# --- summary ---------------------------------------------------------------

@pytest.mark.asyncio
async def test_summary_empty_reports_none():
    stats = await v2.summary(chain=CHAIN)
    assert stats == {"closed": 0, "avg_pnl_pct": None, "avg_pnl_pct_no_top5": None, "winrate": None}


@pytest.mark.asyncio
async def test_summary_aggregates_closed_positions():
    await v2.record_signals([_pool(pool_address="poolA", price_usd=1.0, m5=30.0)], chain=CHAIN)
    old = datetime.now(timezone.utc) - timedelta(minutes=v2.MAX_HOLD_MINUTES + 1)
    async with aiosqlite.connect(v2._db_path()) as db:
        await db.execute(f"UPDATE {v2.TABLE} SET detected_at = ?", (old.isoformat(),))
        await db.commit()
    client = FakeClient({"poolA": 1.05})
    await v2.advance_exit_simulation(client, chain=CHAIN)
    stats = await v2.summary(chain=CHAIN)
    assert stats["closed"] == 1
    assert stats["winrate"] == 1.0


@pytest.mark.asyncio
async def test_ensure_table_recreates_after_external_drop_despite_stale_cache():
    """26/08 -- real incident: an epoch-reset RENAME TABLE against a live
    process left solana_late_bonding_shadow.py's twin cache stale, so every
    write failed with "no such table" for 30+ minutes until a manual fix.
    `_ensure_table` must re-verify the table actually exists even on a cache
    hit, not just trust a stale in-memory flag."""
    async with aiosqlite.connect(v2._db_path()) as db:
        await db.execute(f"DROP TABLE {v2.TABLE}")
        await db.commit()
    assert v2._db_path() in v2._ensured_db_paths

    await v2._ensure_table()

    async with aiosqlite.connect(v2._db_path()) as db:
        cur = await db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (v2.TABLE,)
        )
        assert await cur.fetchone() is not None
