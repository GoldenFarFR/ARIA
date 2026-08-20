"""Solana fresh-launch WEBSOCKET-EXIT shadow (19/08) -- A/B counterpart to
``solana_fresh_launch_shadow.py``. Same isolated-tmp-db + injected-client/
injected-feed pattern as every other shadow test file in this dome; never a
real network call."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import aiosqlite
import pytest

from aria_core import shadow_candle_archive as archive
from aria_core import solana_fresh_launch_shadow as original_shadow
from aria_core import solana_fresh_launch_ws_exit_shadow as shadow
from aria_core.services.dexpaprika import Candle
from aria_core.services.geckoterminal import OHLCVResult, PoolSnapshot, TrendingPool

CHAIN = "solana"


@pytest.fixture(autouse=True)
async def _tmp_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "shadow.db")
    monkeypatch.setattr(shadow, "DB_PATH", db_path)
    shadow._ensured_db_paths.clear()
    await shadow._ensure_table()
    # The original module's own table lives in the SAME physical file when
    # both pockets run in prod -- exercised explicitly in the dedup test
    # below via its own separate ensure_table call, so it must resolve to
    # the same tmp db here too.
    monkeypatch.setattr(original_shadow, "DB_PATH", db_path)
    original_shadow._ensured_db_paths.clear()
    monkeypatch.setattr(archive, "DB_PATH", db_path)
    archive._ensured_db_paths.clear()
    yield
    shadow._ensured_db_paths.clear()
    original_shadow._ensured_db_paths.clear()


async def _rows() -> list[dict]:
    async with aiosqlite.connect(shadow._db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(f"SELECT * FROM {shadow.TABLE}")
        return [dict(r) for r in await cur.fetchall()]


def _pool(
    *, pool_address="poolA", token_address=None, symbol="FRESH",
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
                 remaining_qty, realized_proceeds, peak_price, realistic_entry_price)
            VALUES (?, ?, ?, ?, ?, ?, 1.0, 0.0, ?, ?)
            """,
            (pool_address, CHAIN, detected_at, entry_price, reserve_usd, support_range_high,
             entry_price, realistic_entry_price),
        )
        await db.commit()
        return cur.lastrowid


class FakeClient:
    """Same shape as the original module's test double -- REST fallback
    path only, exercised whenever no ws_feed is given or the feed reports
    a pool unavailable."""

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


class FakeSnapshot:
    def __init__(self, *, available, price_usd=None, reserve_usd=None, dex_id="pumpswap"):
        self.available = available
        self.price_usd = price_usd
        self.reserve_usd = reserve_usd
        self.dex_id = dex_id


class FakeWsFeed:
    def __init__(self, snapshots_by_pool: dict[str, FakeSnapshot]):
        self._snapshots = snapshots_by_pool
        self.calls: list[str] = []

    def get_snapshot(self, pool_address: str) -> FakeSnapshot:
        self.calls.append(pool_address)
        return self._snapshots.get(pool_address, FakeSnapshot(available=False))


# --- entry criterion: identical to the original module (imported thresholds) --

def test_thresholds_are_the_same_imported_objects_as_original_module():
    """The whole A/B pairing hinges on this: MIN_LIQUIDITY_USD/
    MAX_POOL_AGE_MINUTES must be the SAME objects (imported), never a
    redefinition that could silently drift from the original module."""
    assert shadow.MIN_LIQUIDITY_USD is original_shadow.MIN_LIQUIDITY_USD
    assert shadow.MAX_POOL_AGE_MINUTES is original_shadow.MAX_POOL_AGE_MINUTES
    assert shadow.PEAK_PRICE_SANITY_MULTIPLE is original_shadow.PEAK_PRICE_SANITY_MULTIPLE


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
    # Threshold value read LIVE from the imported constant, never hardcoded
    # here -- it is operator-recalibrated from time to time (e.g. 3000->2000
    # on 19/08) and this test must track whatever the original module
    # currently uses, exactly like the module under test itself does.
    monkeypatch.setattr(shadow.dexpaprika, "_fetch_one_interval", AsyncMock(return_value=_flat_candles(3, 1.0)))
    result = await shadow.record_signals([_pool(reserve=shadow.MIN_LIQUIDITY_USD - 1.0)], chain=CHAIN)
    assert result["logged"] == 0


@pytest.mark.asyncio
async def test_liquidity_at_floor_accepted(monkeypatch):
    monkeypatch.setattr(shadow.dexpaprika, "_fetch_one_interval", AsyncMock(return_value=_flat_candles(3, 1.0)))
    result = await shadow.record_signals([_pool(reserve=shadow.MIN_LIQUIDITY_USD)], chain=CHAIN)
    assert result["logged"] == 1


@pytest.mark.asyncio
async def test_pumpportal_liquidity_above_max_entry_rejected():
    """19/08 -- MAX_LIQUIDITY_USD_ENTRY: unlike every other liquidity check
    in this dome (a floor), this pocket's real closures showed the opposite
    risk at the high end (see that constant's own docstring for the real
    numbers). Abandoned immediately, never left polling."""
    from aria_core.services.pumpportal_ws import PumpPortalNewTokenEvent

    event = PumpPortalNewTokenEvent(
        mint="mintA", symbol="FRESH", name=None, pool="pump", bonding_curve_key="poolA",
        market_cap_sol=None, v_sol_in_bonding_curve=None, v_tokens_in_bonding_curve=None,
        sol_amount=None, initial_buy=None, signature=None, detected_at=__import__("time").time(),
    )
    resolve_fn = AsyncMock(return_value=(1.0, shadow.MAX_LIQUIDITY_USD_ENTRY, None, "rest_dexpaprika"))
    result = await shadow._track_candidate_pumpportal(event, resolve_fn=resolve_fn, sleep_fn=AsyncMock())
    assert result is None
    resolve_fn.assert_awaited_once()


@pytest.mark.asyncio
async def test_pumpportal_liquidity_within_range_accepted():
    from aria_core.services.pumpportal_ws import PumpPortalNewTokenEvent

    event = PumpPortalNewTokenEvent(
        mint="mintA", symbol="FRESH", name=None, pool="pump", bonding_curve_key="poolA",
        market_cap_sol=None, v_sol_in_bonding_curve=None, v_tokens_in_bonding_curve=None,
        sol_amount=None, initial_buy=None, signature=None, detected_at=__import__("time").time(),
    )
    mid_liquidity = (shadow.MIN_LIQUIDITY_USD + shadow.MAX_LIQUIDITY_USD_ENTRY) / 2
    resolve_fn = AsyncMock(return_value=(1.0, mid_liquidity, None, "rest_dexpaprika"))
    result = await shadow._track_candidate_pumpportal(event, resolve_fn=resolve_fn, sleep_fn=AsyncMock())
    assert result is not None
    assert result["pool_address"] == "poolA"
    assert result["reserve_usd"] == mid_liquidity


@pytest.mark.asyncio
async def test_zero_price_candidate_never_logged(monkeypatch):
    monkeypatch.setattr(shadow.dexpaprika, "_fetch_one_interval", AsyncMock(return_value=[]))
    result = await shadow.record_signals([_pool(price_usd=0.0)], chain=CHAIN)
    assert result["logged"] == 0
    assert await _rows() == []


@pytest.mark.asyncio
async def test_dedup_is_against_own_table_not_shared_with_original(monkeypatch):
    """Both pockets independently log a signal for the SAME candidate pool
    -- one pocket's table must never suppress the other's entry (each
    dedups only against its own open positions)."""
    monkeypatch.setattr(shadow.dexpaprika, "_fetch_one_interval", AsyncMock(return_value=_flat_candles(3, 1.0)))
    monkeypatch.setattr(original_shadow.dexpaprika, "_fetch_one_interval", AsyncMock(return_value=_flat_candles(3, 1.0)))

    result_original = await original_shadow.record_signals([_pool()], chain=CHAIN)
    result_ws = await shadow.record_signals([_pool()], chain=CHAIN)
    assert result_original["logged"] == 1
    assert result_ws["logged"] == 1  # NOT suppressed by the original module's own row

    # Second pass against each pocket's own table dedups normally.
    result_original_2 = await original_shadow.record_signals([_pool()], chain=CHAIN)
    result_ws_2 = await shadow.record_signals([_pool()], chain=CHAIN)
    assert result_original_2["logged"] == 0
    assert result_ws_2["logged"] == 0

    assert len(await _rows()) == 1
    async with aiosqlite.connect(original_shadow._db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(f"SELECT * FROM {original_shadow.TABLE}")
        assert len(await cur.fetchall()) == 1


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
    archived = await archive.get_candles(module="solana_fresh_launch_ws_exit", position_id=rows[0]["id"], phase="before")
    assert len(archived) == 2


# --- evaluate_exit: pure function, no ladder --------------------------------

def test_evaluate_exit_no_ladder_stays_fully_open_below_stop():
    row = {"entry_price": 1.0, "peak_price": 1.0, "remaining_qty": 1.0, "realized_proceeds": 0.0,
           "realistic_entry_price": 1.0, "realistic_realized_proceeds": 0.0, "reserve_usd": 8000.0,
           "support_range_high": None, "pool_address": "poolA"}
    # +1000% move: no scale-out rung exists in this variant -- position stays 100% open.
    result = shadow.evaluate_exit(
        row, current_price=11.0, reserve_usd=8000.0, dex_id=None, age_minutes=5.0,
    )
    assert result["skipped"] is False
    assert result["exit_reason"] is None
    assert result["remaining_qty"] == 1.0
    assert result["peak_price"] == pytest.approx(11.0)


def test_evaluate_exit_trailing_stop_closes_entire_position_in_one_shot():
    row = {"entry_price": 1.0, "peak_price": 1.20, "remaining_qty": 1.0, "realized_proceeds": 0.0,
           "realistic_entry_price": 1.0, "realistic_realized_proceeds": 0.0, "reserve_usd": 8000.0,
           "support_range_high": None, "pool_address": "poolA"}
    stop_price = 1.20 * (1 - shadow.TRAILING_STOP_PCT / 100.0)
    result = shadow.evaluate_exit(
        row, current_price=stop_price, reserve_usd=8000.0, dex_id=None, age_minutes=5.0,
    )
    assert result["exit_reason"] == "trailing_stop"
    assert result["remaining_qty"] == 0.0  # entire position, never a partial ladder fill
    assert result["final_multiplier"] == pytest.approx(stop_price / 1.0)


def test_evaluate_exit_window_low_catches_stop_missed_by_point_sample():
    row = {"entry_price": 1.0, "peak_price": 1.16, "remaining_qty": 1.0, "realized_proceeds": 0.0,
           "realistic_entry_price": 1.0, "realistic_realized_proceeds": 0.0, "reserve_usd": 8000.0,
           "support_range_high": None, "pool_address": "poolA"}
    result = shadow.evaluate_exit(
        row, current_price=1.10, reserve_usd=8000.0, dex_id=None, age_minutes=5.0,
        window_high=1.16, window_low=0.90,
    )
    assert result["exit_reason"] == "trailing_stop"


def test_evaluate_exit_liquidity_collapse_priority_over_trailing_stop():
    row = {"entry_price": 1.0, "peak_price": 1.20, "remaining_qty": 1.0, "realized_proceeds": 0.0,
           "realistic_entry_price": 1.0, "realistic_realized_proceeds": 0.0, "reserve_usd": 8000.0,
           "support_range_high": None, "pool_address": "poolA"}
    result = shadow.evaluate_exit(
        row, current_price=0.98, reserve_usd=3000.0, dex_id=None, age_minutes=5.0,  # < 50% of entry reserve
    )
    assert result["exit_reason"] == "liquidity_collapse"


def test_evaluate_exit_pumpswap_never_triggers_liquidity_collapse():
    # peak_price == entry_price (a fresh row, never rallied) so a mild dip to
    # 0.98 stays well above the -15% trailing-stop line from peak (0.85) --
    # isolates the liquidity_collapse guard specifically, same real scenario
    # as solana_fresh_launch_shadow.py's own equivalent test.
    row = {"entry_price": 1.0, "peak_price": 1.0, "remaining_qty": 1.0, "realized_proceeds": 0.0,
           "realistic_entry_price": 1.0, "realistic_realized_proceeds": 0.0, "reserve_usd": 8000.0,
           "support_range_high": None, "pool_address": "poolA"}
    result = shadow.evaluate_exit(
        row, current_price=0.98, reserve_usd=0.0, dex_id="pumpswap", age_minutes=5.0,
    )
    assert result["exit_reason"] is None


def test_evaluate_exit_max_hold_after_60_minutes():
    row = {"entry_price": 1.0, "peak_price": 1.0, "remaining_qty": 1.0, "realized_proceeds": 0.0,
           "realistic_entry_price": 1.0, "realistic_realized_proceeds": 0.0, "reserve_usd": 8000.0,
           "support_range_high": None, "pool_address": "poolA"}
    result = shadow.evaluate_exit(
        row, current_price=1.05, reserve_usd=8000.0, dex_id=None, age_minutes=shadow.MAX_HOLD_MINUTES + 1,
    )
    assert result["exit_reason"] == "max_hold"
    assert result["final_multiplier"] == pytest.approx(1.05)


def test_evaluate_exit_implausible_price_skipped():
    row = {"entry_price": 0.001, "peak_price": 0.001, "remaining_qty": 1.0, "realized_proceeds": 0.0,
           "realistic_entry_price": 0.001, "realistic_realized_proceeds": 0.0, "reserve_usd": 8000.0,
           "support_range_high": 0.0011, "pool_address": "poolA"}
    result = shadow.evaluate_exit(
        row, current_price=0.0011 * 60, reserve_usd=8000.0, dex_id=None, age_minutes=5.0,
    )
    assert result["skipped"] is True


def test_evaluate_exit_implausible_price_falls_back_to_entry_price_reference():
    row = {"entry_price": 1.0, "peak_price": 1.0, "remaining_qty": 1.0, "realized_proceeds": 0.0,
           "realistic_entry_price": 1.0, "realistic_realized_proceeds": 0.0, "reserve_usd": 8000.0,
           "support_range_high": None, "pool_address": "poolA"}
    result = shadow.evaluate_exit(
        row, current_price=51.0, reserve_usd=8000.0, dex_id=None, age_minutes=5.0,
    )
    assert result["skipped"] is True


# --- advance_exit_simulation: websocket price path --------------------------

@pytest.mark.asyncio
async def test_websocket_price_used_when_feed_available_no_rest_call():
    await _insert_open_row(pool_address="poolA", entry_price=1.0, minutes_ago=10.0)
    feed = FakeWsFeed({"poolA": FakeSnapshot(available=True, price_usd=1.30, reserve_usd=8000.0, dex_id="pumpswap")})
    rest_client = FakeClient({}, reserve_by_pool={})  # no price configured -> would fail if ever called

    counts = await shadow.advance_exit_simulation(rest_client, chain=CHAIN, ws_feed=feed)
    assert counts["checked_via_websocket"] == 1
    assert counts["checked_via_polling"] == 0
    assert rest_client.calls == []  # REST never touched
    rows = await _rows()
    assert rows[0]["last_price_source"] == "websocket"
    assert rows[0]["peak_price"] == pytest.approx(1.30)


@pytest.mark.asyncio
async def test_websocket_trailing_stop_closes_position_with_source_recorded():
    await _insert_open_row(pool_address="poolA", entry_price=1.0, minutes_ago=10.0)
    # Cycle 1: price rises, sets peak via websocket.
    feed = FakeWsFeed({"poolA": FakeSnapshot(available=True, price_usd=1.20, reserve_usd=8000.0, dex_id="pumpswap")})
    await shadow.advance_exit_simulation(FakeClient({}), chain=CHAIN, ws_feed=feed)
    rows = await _rows()
    assert rows[0]["peak_price"] == pytest.approx(1.20)

    # Cycle 2: price falls below the -15% stop via websocket -> full close.
    stop_price = 1.20 * (1 - shadow.TRAILING_STOP_PCT / 100.0)
    feed2 = FakeWsFeed({"poolA": FakeSnapshot(available=True, price_usd=stop_price, reserve_usd=8000.0, dex_id="pumpswap")})
    counts = await shadow.advance_exit_simulation(FakeClient({}), chain=CHAIN, ws_feed=feed2)
    assert counts["closed_trailing_stop"] == 1
    rows = await _rows()
    assert rows[0]["exit_reason"] == "trailing_stop"
    assert rows[0]["exit_price_source"] == "websocket"
    assert rows[0]["remaining_qty"] == 0.0


# --- advance_exit_simulation: REST fallback ----------------------------------

@pytest.mark.asyncio
async def test_falls_back_to_rest_when_feed_reports_unavailable():
    await _insert_open_row(pool_address="poolA", entry_price=1.0, minutes_ago=10.0)
    feed = FakeWsFeed({})  # no snapshot at all -> always unavailable
    client = FakeClient({"poolA": 1.30}, reserve_by_pool={"poolA": 8000.0})

    counts = await shadow.advance_exit_simulation(client, chain=CHAIN, ws_feed=feed)
    assert counts["checked_via_websocket"] == 0
    assert counts["checked_via_polling"] == 1
    assert client.calls == ["poolA"]
    rows = await _rows()
    assert rows[0]["last_price_source"] == "polling"


@pytest.mark.asyncio
async def test_pure_polling_when_no_feed_given():
    await _insert_open_row(pool_address="poolA", entry_price=1.0, minutes_ago=10.0)
    client = FakeClient({"poolA": 1.05}, reserve_by_pool={"poolA": 8000.0})
    counts = await shadow.advance_exit_simulation(client, chain=CHAIN, ws_feed=None)
    assert counts["checked_via_polling"] == 1
    assert client.calls == ["poolA"]


@pytest.mark.asyncio
async def test_rest_fallback_still_advances_queue_on_total_failure():
    """Same starvation-bug fix as the original module, ported: a row whose
    price source keeps failing (neither websocket nor REST) must still be
    stamped so it doesn't permanently occupy the front of the queue."""
    stuck = await _insert_open_row(pool_address="stuck", entry_price=1.0, minutes_ago=30.0)
    behind = await _insert_open_row(pool_address="behind", entry_price=1.0, minutes_ago=20.0)
    feed = FakeWsFeed({})
    client = FakeClient({"behind": 1.0}, reserve_by_pool={"behind": 8000.0})

    await shadow.advance_exit_simulation(client, chain=CHAIN, limit=1, ws_feed=feed)
    rows_by_pool = {r["pool_address"]: r for r in await _rows()}
    assert rows_by_pool["stuck"]["last_checked_at"] is not None
    assert rows_by_pool["stuck"]["exit_reason"] is None

    client.calls.clear()
    await shadow.advance_exit_simulation(client, chain=CHAIN, limit=1, ws_feed=feed)
    assert "behind" in client.calls


@pytest.mark.asyncio
async def test_round_robin_lets_younger_position_get_checked_past_limit():
    await _insert_open_row(pool_address="older", entry_price=1.0, minutes_ago=30.0)
    await _insert_open_row(pool_address="middle", entry_price=1.0, minutes_ago=20.0)
    await _insert_open_row(pool_address="younger", entry_price=1.0, minutes_ago=10.0)
    client = FakeClient(
        price_by_pool={"older": 1.0, "middle": 1.0, "younger": 1.0},
        reserve_by_pool={"older": 8000.0, "middle": 8000.0, "younger": 8000.0},
    )

    first = await shadow.advance_exit_simulation(client, chain=CHAIN, limit=2)
    assert first["checked"] == 2
    assert sorted(client.calls) == ["middle", "older"]

    client.calls.clear()
    second = await shadow.advance_exit_simulation(client, chain=CHAIN, limit=2)
    assert second["checked"] == 2
    assert "younger" in client.calls


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


@pytest.mark.asyncio
async def test_pnl_realistic_stranded_position_counted_as_loss():
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


# --- summary -----------------------------------------------------------------

@pytest.mark.asyncio
async def test_summary_tracks_price_source_breakdown():
    async with aiosqlite.connect(shadow._db_path()) as db:
        await db.execute(
            f"INSERT INTO {shadow.TABLE} (pool_address, chain, detected_at, entry_price, "
            "exit_reason, final_multiplier, exit_price_source) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("p1", CHAIN, datetime.now(timezone.utc).isoformat(), 1.0, "trailing_stop", 0.9, "websocket"),
        )
        await db.execute(
            f"INSERT INTO {shadow.TABLE} (pool_address, chain, detected_at, entry_price, "
            "exit_reason, final_multiplier, exit_price_source) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("p2", CHAIN, datetime.now(timezone.utc).isoformat(), 1.0, "max_hold", 1.2, "polling"),
        )
        await db.commit()
    s = await shadow.summary(chain=CHAIN)
    assert s["completed"] == 2
    assert s["by_exit_price_source"] == {"websocket": 1, "polling": 1}
