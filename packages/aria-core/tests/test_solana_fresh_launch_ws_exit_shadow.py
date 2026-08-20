"""Solana fresh-launch WEBSOCKET-EXIT shadow (19/08) -- A/B counterpart to
``solana_fresh_launch_shadow.py``. Same isolated-tmp-db + injected-client/
injected-feed pattern as every other shadow test file in this dome; never a
real network call."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiosqlite
import pytest

from aria_core import shadow_candle_archive as archive
from aria_core import solana_fresh_launch_shadow as original_shadow
from aria_core import solana_fresh_launch_ws_exit_shadow as shadow
from aria_core.services.dexpaprika import Candle
from aria_core.services.geckoterminal import OHLCVResult, PoolSnapshot, TrendingPool

CHAIN = "solana"


async def _gate_clears(_mint, _pool=None):
    """20/08 -- stubs the PRE-TRADE holder gate as "cleared". Required in
    every _track_candidate_pumpportal test: the real gate is fail-closed
    and would otherwise hit the live RugCheck endpoint (never a real
    network call in this dome) and refuse every synthetic mint."""
    return shadow.HolderGateOutcome(blocked=False, top_holder_pct=50.0, latency_ms=1.0)


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
    """MAX_POOL_AGE_MINUTES/PEAK_PRICE_SANITY_MULTIPLE must be the SAME
    objects (imported), never a redefinition that could silently drift from
    the original module. MIN_LIQUIDITY_USD is DELIBERATELY decoupled as of
    20/08 (own value, see its docstring) -- real per-pocket data showed the
    two pockets' optimal liquidity bands diverge, so this constant is the one
    exception to the "same imported object" rule from here on."""
    assert shadow.MAX_POOL_AGE_MINUTES is original_shadow.MAX_POOL_AGE_MINUTES
    assert shadow.PEAK_PRICE_SANITY_MULTIPLE is original_shadow.PEAK_PRICE_SANITY_MULTIPLE
    assert shadow.MIN_LIQUIDITY_USD != original_shadow.MIN_LIQUIDITY_USD


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
    result = await shadow._track_candidate_pumpportal(
        event, holder_gate_fn=_gate_clears, resolve_fn=resolve_fn, sleep_fn=AsyncMock(),
    )
    assert result is None
    resolve_fn.assert_awaited_once()


class _FakeFeed:
    """20/08 -- spy for remove_pools()/add_pools(), see the two tests below."""

    def __init__(self):
        self.added: list = []
        self.removed: list = []

    async def add_pools(self, pairs):
        self.added.extend(pairs)
        return len(pairs)

    def remove_pools(self, addrs):
        self.removed.extend(addrs)


@pytest.mark.asyncio
async def test_pumpportal_liquidity_above_max_entry_sheds_subscription():
    """20/08, real incident: add_pools() was called unconditionally but this
    abandonment path never called remove_pools() -- see MIN_LIQUIDITY_USD's
    own 20/08 docstring for the real subscription-leak this caused."""
    from aria_core.services.pumpportal_ws import PumpPortalNewTokenEvent

    event = PumpPortalNewTokenEvent(
        mint="mintA", symbol="FRESH", name=None, pool="pump", bonding_curve_key="poolA",
        market_cap_sol=None, v_sol_in_bonding_curve=None, v_tokens_in_bonding_curve=None,
        sol_amount=None, initial_buy=None, signature=None, detected_at=__import__("time").time(),
    )
    bonding_feed = _FakeFeed()
    resolve_fn = AsyncMock(return_value=(1.0, shadow.MAX_LIQUIDITY_USD_ENTRY, None, "rest_dexpaprika"))
    result = await shadow._track_candidate_pumpportal(
        event, holder_gate_fn=_gate_clears, bonding_ws_feed=bonding_feed, resolve_fn=resolve_fn, sleep_fn=AsyncMock(),
    )
    assert result is None
    assert bonding_feed.added == [("poolA", "mintA")]
    assert bonding_feed.removed == ["poolA"]


@pytest.mark.asyncio
async def test_pumpportal_abandoned_past_age_ceiling_sheds_subscription():
    from aria_core.services.pumpportal_ws import PumpPortalNewTokenEvent

    t0 = 3_000_000.0
    event = PumpPortalNewTokenEvent(
        mint="mintA", symbol="FRESH", name=None, pool="pump", bonding_curve_key="poolA",
        market_cap_sol=None, v_sol_in_bonding_curve=None, v_tokens_in_bonding_curve=None,
        sol_amount=None, initial_buy=None, signature=None, detected_at=t0,
    )
    bonding_feed = _FakeFeed()
    fake_clock = {"t": t0}

    def fake_time():
        fake_clock["t"] = t0 + shadow.MAX_POOL_AGE_MINUTES * 60.0 + 1.0
        return fake_clock["t"]

    result = await shadow._track_candidate_pumpportal(
        event, holder_gate_fn=_gate_clears, bonding_ws_feed=bonding_feed, resolve_fn=AsyncMock(return_value=(None, None, None, None)),
        sleep_fn=AsyncMock(), time_fn=fake_time,
    )
    assert result is None
    assert bonding_feed.removed == ["poolA"]


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
    result = await shadow._track_candidate_pumpportal(
        event, holder_gate_fn=_gate_clears, resolve_fn=resolve_fn, sleep_fn=AsyncMock(),
    )
    assert result is not None
    assert result["pool_address"] == "poolA"
    assert result["reserve_usd"] == mid_liquidity


@pytest.mark.asyncio
async def test_pumpportal_candidate_in_market_cap_dead_zone_rejected_before_add_pools():
    """20/08 -- same dead zone as FAST-DISCOVERY, reused here since both
    pockets share the same PumpPortal source event (no WS-EXIT-specific
    history needed, see _track_candidate_pumpportal's own docstring)."""
    from aria_core.services.pumpportal_ws import PumpPortalNewTokenEvent
    from aria_core.solana_fresh_launch_fast_discovery_shadow import MARKET_CAP_SOL_AT_CREATION_REJECT_MIN

    event = PumpPortalNewTokenEvent(
        mint="mintA", symbol="FRESH", name=None, pool="pump", bonding_curve_key="poolA",
        market_cap_sol=MARKET_CAP_SOL_AT_CREATION_REJECT_MIN, v_sol_in_bonding_curve=None,
        v_tokens_in_bonding_curve=None, sol_amount=None, initial_buy=None, signature=None,
        detected_at=__import__("time").time(),
    )
    bonding_feed = _FakeFeed()
    resolve_fn = AsyncMock()
    result = await shadow._track_candidate_pumpportal(
        event, holder_gate_fn=_gate_clears, bonding_ws_feed=bonding_feed, resolve_fn=resolve_fn, sleep_fn=AsyncMock(),
    )
    assert result is None
    resolve_fn.assert_not_awaited()
    assert bonding_feed.added == []


@pytest.mark.asyncio
async def test_pumpportal_candidate_at_market_cap_dead_zone_upper_boundary_accepted():
    from aria_core.services.pumpportal_ws import PumpPortalNewTokenEvent
    from aria_core.solana_fresh_launch_fast_discovery_shadow import MARKET_CAP_SOL_AT_CREATION_REJECT_MAX

    event = PumpPortalNewTokenEvent(
        mint="mintA", symbol="FRESH", name=None, pool="pump", bonding_curve_key="poolA",
        market_cap_sol=MARKET_CAP_SOL_AT_CREATION_REJECT_MAX, v_sol_in_bonding_curve=None,
        v_tokens_in_bonding_curve=None, sol_amount=None, initial_buy=None, signature=None,
        detected_at=__import__("time").time(),
    )
    mid_liquidity = (shadow.MIN_LIQUIDITY_USD + shadow.MAX_LIQUIDITY_USD_ENTRY) / 2
    resolve_fn = AsyncMock(return_value=(1.0, mid_liquidity, None, "rest_dexpaprika"))
    result = await shadow._track_candidate_pumpportal(
        event, holder_gate_fn=_gate_clears, resolve_fn=resolve_fn, sleep_fn=AsyncMock(),
    )
    assert result is not None


@pytest.mark.asyncio
async def test_track_and_maybe_insert_pumpportal_skips_a_key_already_in_flight(monkeypatch):
    """20/08 -- mirrors FAST-DISCOVERY's own fix: a candidate that never
    confirms has no DB row, so the DB-only dedup can never catch a
    re-broadcast of the same key. `in_flight` closes that gap."""
    from aria_core.services.pumpportal_ws import PumpPortalNewTokenEvent

    track_mock = AsyncMock()
    monkeypatch.setattr(shadow, "_track_candidate_pumpportal", track_mock)
    event = PumpPortalNewTokenEvent(
        mint="mintA", symbol="FRESH", name=None, pool="pump", bonding_curve_key="curveStuck",
        market_cap_sol=None, v_sol_in_bonding_curve=None, v_tokens_in_bonding_curve=None,
        sol_amount=None, initial_buy=None, signature=None, detected_at=__import__("time").time(),
    )
    stats: dict = {}
    in_flight = {"curveStuck"}
    await shadow._track_and_maybe_insert_pumpportal(
        event, chain=CHAIN, ws_feed=None, semaphore=asyncio.Semaphore(1), stats=stats, in_flight=in_flight,
    )
    assert stats.get("deduped_in_flight") == 1
    track_mock.assert_not_called()
    assert in_flight == {"curveStuck"}


@pytest.mark.asyncio
async def test_track_and_maybe_insert_pumpportal_removes_from_in_flight_when_done(monkeypatch):
    from aria_core.services.pumpportal_ws import PumpPortalNewTokenEvent

    monkeypatch.setattr(shadow, "_track_candidate_pumpportal", AsyncMock(return_value=None))
    event = PumpPortalNewTokenEvent(
        mint="mintA", symbol="FRESH", name=None, pool="pump", bonding_curve_key="curveTransient",
        market_cap_sol=None, v_sol_in_bonding_curve=None, v_tokens_in_bonding_curve=None,
        sol_amount=None, initial_buy=None, signature=None, detected_at=__import__("time").time(),
    )
    stats: dict = {}
    in_flight: set = set()
    await shadow._track_and_maybe_insert_pumpportal(
        event, chain=CHAIN, ws_feed=None, semaphore=asyncio.Semaphore(1), stats=stats, in_flight=in_flight,
    )
    assert stats.get("abandoned") == 1
    assert in_flight == set()


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

    # reserve clears BOTH pockets' now-decoupled floors (original=6000, ws=3000).
    result_original = await original_shadow.record_signals([_pool(reserve=10000.0)], chain=CHAIN)
    result_ws = await shadow.record_signals([_pool(reserve=10000.0)], chain=CHAIN)
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
async def test_websocket_price_archives_snapshot(monkeypatch):
    """20/08 -- real gap found investigating late liquidity_collapse catches:
    a websocket-priced check used to archive nothing to
    shadow_snapshot_archive, unlike the REST branch below."""
    from aria_core import shadow_snapshot_archive

    store = AsyncMock(return_value=True)
    monkeypatch.setattr(shadow_snapshot_archive, "store_snapshot", store)
    await _insert_open_row(pool_address="poolA", entry_price=1.0, minutes_ago=10.0)
    feed = FakeWsFeed({"poolA": FakeSnapshot(available=True, price_usd=1.30, reserve_usd=8000.0, dex_id="pumpswap")})

    await shadow.advance_exit_simulation(FakeClient({}), chain=CHAIN, ws_feed=feed)
    store.assert_awaited_once()
    assert store.await_args.kwargs["module"] == "solana_fresh_launch_ws_exit"
    assert store.await_args.kwargs["pool_address"] == "poolA"
    assert store.await_args.kwargs["reserve_usd"] == pytest.approx(8000.0)


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


# --- 20/08, holder-concentration reject ported over from FAST-DISCOVERY ----
# The A/B that deliberately kept this pocket WITHOUT the filter returned its
# verdict on 1003 real closures (see HOLDER_CONCENTRATION_REJECT_PCT's own
# docstring). Note this logic shipped on FAST-DISCOVERY with ZERO test
# coverage despite closing 651 real positions -- covered on both pockets now.

class _RugcheckReport:
    def __init__(self, top_holder_pct, available=True):
        self.available = available
        self.top_holder_pct = top_holder_pct
        self.score_normalised = 50.0
        self.risks = None
        self.creator = "devA"


async def _exit_reason_of(row_id: int):
    async with aiosqlite.connect(shadow._db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(f"SELECT * FROM {shadow.TABLE} WHERE id = ?", (row_id,))
        return dict(await cur.fetchone())


@pytest.mark.asyncio
async def test_rugcheck_backfill_closes_a_row_above_the_holder_threshold(monkeypatch):
    row_id = await _insert_open_row(pool_address="poolA", entry_price=1.0, reserve_usd=4000.0)
    monkeypatch.setattr(
        shadow.rugcheck, "get_token_report",
        AsyncMock(return_value=_RugcheckReport(shadow.HOLDER_CONCENTRATION_REJECT_PCT + 0.5)),
    )
    monkeypatch.setattr(
        shadow, "_snapshot_with_fallback",
        AsyncMock(return_value=SimpleNamespace(
            available=True, price_usd=0.5, reserve_usd=4000.0, dex_id="rest_dexpaprika",
        )),
    )
    feed = _FakeFeed()
    await shadow._enrich_with_rugcheck_pumpportal(row_id, "mintA", bonding_ws_feed=feed)

    row = await _exit_reason_of(row_id)
    assert row["exit_reason"] == "holder_concentration_reject"
    assert row["remaining_qty"] == 0.0
    assert row["final_multiplier"] == pytest.approx(0.5)
    assert feed.removed == ["poolA"]  # subscription shed, never leaked


@pytest.mark.asyncio
async def test_rugcheck_backfill_leaves_a_row_below_the_holder_threshold_open(monkeypatch):
    row_id = await _insert_open_row(pool_address="poolA", entry_price=1.0)
    monkeypatch.setattr(
        shadow.rugcheck, "get_token_report",
        AsyncMock(return_value=_RugcheckReport(shadow.HOLDER_CONCENTRATION_REJECT_PCT - 0.5)),
    )
    snapshot_mock = AsyncMock()
    monkeypatch.setattr(shadow, "_snapshot_with_fallback", snapshot_mock)

    await shadow._enrich_with_rugcheck_pumpportal(row_id, "mintA")

    row = await _exit_reason_of(row_id)
    assert row["exit_reason"] is None
    assert row["rugcheck_top_holder_pct"] == pytest.approx(shadow.HOLDER_CONCENTRATION_REJECT_PCT - 0.5)
    snapshot_mock.assert_not_awaited()  # no needless network call on the accepted path


@pytest.mark.asyncio
async def test_holder_reject_never_reopens_an_already_closed_row(monkeypatch):
    """`WHERE exit_reason IS NULL` guards the race against the normal exit
    loop closing the same row first -- whichever writes first wins."""
    row_id = await _insert_open_row(pool_address="poolA", entry_price=1.0)
    async with aiosqlite.connect(shadow._db_path()) as db:
        await db.execute(
            f"UPDATE {shadow.TABLE} SET exit_reason='trailing_stop', final_multiplier=1.4 WHERE id=?",
            (row_id,),
        )
        await db.commit()
    monkeypatch.setattr(
        shadow, "_snapshot_with_fallback",
        AsyncMock(return_value=SimpleNamespace(
            available=True, price_usd=0.5, reserve_usd=4000.0, dex_id="rest_dexpaprika",
        )),
    )
    await shadow._reject_on_holder_concentration(row_id)

    row = await _exit_reason_of(row_id)
    assert row["exit_reason"] == "trailing_stop"
    assert row["final_multiplier"] == pytest.approx(1.4)


@pytest.mark.asyncio
async def test_holder_reject_never_raises_when_the_snapshot_fails(monkeypatch):
    row_id = await _insert_open_row(pool_address="poolA", entry_price=1.0)
    monkeypatch.setattr(shadow, "_snapshot_with_fallback", AsyncMock(side_effect=RuntimeError("boom")))

    await shadow._reject_on_holder_concentration(row_id)  # must not raise

    row = await _exit_reason_of(row_id)
    assert row["exit_reason"] is None  # left open, never closed on a guessed price


# --- 20/08, PRE-TRADE holder gate (operator-directed) ---------------------
# Blocks the order BEFORE it is sent, rather than closing the position after
# RugCheck's async backfill lands. Measured live first: of the first 4 real
# entries at top_holder>=92%, the post-entry reject closed ZERO of them.

def _pp_event(mint="mintA", bonding_curve_key="poolA"):
    from aria_core.services.pumpportal_ws import PumpPortalNewTokenEvent
    import time as _t
    return PumpPortalNewTokenEvent(
        mint=mint, symbol="FRESH", name=None, pool="pump", bonding_curve_key=bonding_curve_key,
        market_cap_sol=None, v_sol_in_bonding_curve=None, v_tokens_in_bonding_curve=None,
        sol_amount=None, initial_buy=None, signature=None, detected_at=_t.time(),
    )


class _Report:
    def __init__(self, top_holder_pct, available=True, top_holders=None):
        self.available = available
        self.top_holder_pct = top_holder_pct
        # (pct, owner) pairs -- defaults to "the pool holds it all", the real
        # shape on a Solana fresh launch (verified live on 6 tokens).
        self.top_holders = top_holders if top_holders is not None else (
            [(top_holder_pct, "poolA"), (0.3, "walletB")] if top_holder_pct is not None else []
        )


@pytest.mark.asyncio
async def test_gate_blocks_a_token_at_or_above_the_threshold(monkeypatch):
    monkeypatch.setattr(
        shadow.rugcheck, "get_token_report",
        AsyncMock(return_value=_Report(shadow.HOLDER_CONCENTRATION_REJECT_PCT)),
    )
    gate = await shadow._holder_concentration_gate("mintA")
    assert gate.blocked is True
    assert gate.reason.startswith("blocked_holder_concentration")
    assert gate.top_holder_pct == pytest.approx(shadow.HOLDER_CONCENTRATION_REJECT_PCT)
    assert gate.latency_ms is not None  # latency captured even on the reject path


@pytest.mark.asyncio
async def test_gate_clears_a_token_below_the_threshold(monkeypatch):
    monkeypatch.setattr(
        shadow.rugcheck, "get_token_report",
        AsyncMock(return_value=_Report(shadow.HOLDER_CONCENTRATION_REJECT_PCT - 0.1)),
    )
    gate = await shadow._holder_concentration_gate("mintA")
    assert gate.blocked is False
    # The pct is carried on the ACCEPTED path too, otherwise only rejections
    # would ever have holder data and the filter could not be judged.
    assert gate.top_holder_pct is not None


@pytest.mark.asyncio
async def test_gate_fails_closed_on_a_timeout(monkeypatch):
    async def _hang(_mint):
        await asyncio.sleep(10)
    monkeypatch.setattr(shadow.rugcheck, "get_token_report", _hang)
    monkeypatch.setattr(shadow, "HOLDER_GATE_TIMEOUT_S", 0.01)

    gate = await shadow._holder_concentration_gate("mintA")

    assert gate.blocked is True
    assert gate.reason.startswith("blocked_holder_gate_unavailable")


@pytest.mark.asyncio
async def test_gate_fails_closed_on_a_provider_error(monkeypatch):
    monkeypatch.setattr(shadow.rugcheck, "get_token_report", AsyncMock(side_effect=RuntimeError("boom")))
    gate = await shadow._holder_concentration_gate("mintA")
    assert gate.blocked is True
    assert gate.reason.startswith("blocked_holder_gate_unavailable")


@pytest.mark.asyncio
async def test_gate_fails_closed_when_the_report_carries_no_holder_data(monkeypatch):
    """An unenriched answer is NOT evidence the token is clean -- treated the
    same as an outage, never as an implicit pass."""
    monkeypatch.setattr(shadow.rugcheck, "get_token_report", AsyncMock(return_value=_Report(None)))
    gate = await shadow._holder_concentration_gate("mintA")
    assert gate.blocked is True
    assert gate.reason.startswith("blocked_holder_gate_unavailable")


@pytest.mark.asyncio
async def test_a_blocked_candidate_never_becomes_a_position():
    """The whole point: no row, so no entry fee and no loss -- unlike the
    post-entry reject, which only ever closed an already-open position."""
    async def _blocks(_mint, _pool=None):
        return shadow.HolderGateOutcome(
            blocked=True, reason="blocked_holder_concentration: top_holder=99.0%",
            top_holder_pct=99.0, latency_ms=2.0,
        )

    stats: dict = {}
    result = await shadow._track_candidate_pumpportal(
        _pp_event(),
        resolve_fn=AsyncMock(return_value=(1.0, shadow.MIN_LIQUIDITY_USD + 1.0, None, "rest_dexpaprika")),
        sleep_fn=AsyncMock(), holder_gate_fn=_blocks, stats=stats,
    )

    assert result is None
    assert stats.get("blocked_holder_concentration") == 1
    assert await _rows() == []


@pytest.mark.asyncio
async def test_an_outage_is_counted_separately_from_a_real_reject():
    """Fail-closed must stay VISIBLE: a RugCheck outage must never hide inside
    the normal reject count, or a silent provider failure would read as "the
    filter is working well" while the pocket is simply not trading at all."""
    async def _unavailable(_mint, _pool=None):
        return shadow.HolderGateOutcome(
            blocked=True, reason="blocked_holder_gate_unavailable: timeout", latency_ms=12000.0,
        )

    stats: dict = {}
    await shadow._track_candidate_pumpportal(
        _pp_event(),
        resolve_fn=AsyncMock(return_value=(1.0, shadow.MIN_LIQUIDITY_USD + 1.0, None, "rest_dexpaprika")),
        sleep_fn=AsyncMock(), holder_gate_fn=_unavailable, stats=stats,
    )

    assert stats.get("blocked_holder_gate_unavailable") == 1
    assert "blocked_holder_concentration" not in stats


@pytest.mark.asyncio
async def test_a_blocked_candidate_sheds_its_websocket_subscription():
    """A blocked candidate must not leak the subscription add_pools() took --
    the exact failure that once exceeded the RPC's accountSubscribe ceiling."""
    async def _blocks(_mint, _pool=None):
        return shadow.HolderGateOutcome(
            blocked=True, reason="blocked_holder_concentration: top_holder=99.0%",
            top_holder_pct=99.0, latency_ms=2.0,
        )

    feed = _FakeFeed()
    await shadow._track_candidate_pumpportal(
        _pp_event(), bonding_ws_feed=feed,
        resolve_fn=AsyncMock(return_value=(1.0, shadow.MIN_LIQUIDITY_USD + 1.0, None, "rest_dexpaprika")),
        sleep_fn=AsyncMock(), holder_gate_fn=_blocks,
    )

    assert feed.removed == ["poolA"]


@pytest.mark.asyncio
async def test_the_gate_runs_only_after_the_liquidity_filter_clears():
    """Budget guarantee: RugCheck's shared throttle allows ~13/min. The gate
    must see only confirmed candidates (~1.2/min on this pocket), never the
    ~40/min of the raw PumpPortal feed -- otherwise it would queue forever."""
    calls = []

    async def _counting_gate(mint, _pool=None):
        calls.append(mint)
        return shadow.HolderGateOutcome(blocked=False)

    await shadow._track_candidate_pumpportal(
        _pp_event(),
        resolve_fn=AsyncMock(return_value=(1.0, shadow.MIN_LIQUIDITY_USD - 1.0, None, "rest_dexpaprika")),
        sleep_fn=AsyncMock(), holder_gate_fn=_counting_gate,
        max_pool_age_minutes=0.0,  # ages out immediately, never confirms
    )

    assert calls == []  # liquidity never cleared, so no RugCheck call was spent


# --- 20/08, the pool-excluded holder pct ---------------------------------
# Real finding, verified live on 6 tokens: topHolders[0] is the POOL itself in
# 5 of 6 cases (second real holder at 0.01-0.39%). So the live threshold is a
# TRACTION filter, not the holder-concentration guardrail its name implies.
# Both are measured side by side; neither threshold is changed on this alone.

def test_pool_is_excluded_so_the_real_wallet_concentration_surfaces():
    holders = [(99.3, "poolA"), (0.24, "walletB"), (0.1, "walletC")]
    assert shadow._holder_pct_excluding_pool(holders, "poolA") == pytest.approx(0.24)


def test_the_top_holder_is_kept_when_it_is_not_the_pool():
    holders = [(44.9, "walletB"), (30.0, "poolA")]
    assert shadow._holder_pct_excluding_pool(holders, "poolA") == pytest.approx(44.9)


def test_missing_holder_data_is_none_never_a_flattering_zero():
    """A 0.0 fallback would read as "no concentration at all" and silently
    flatter a token whose data is simply missing."""
    assert shadow._holder_pct_excluding_pool([], "poolA") is None
    assert shadow._holder_pct_excluding_pool([(99.0, "poolA")], "poolA") is None


def test_an_unknown_pool_address_never_silently_drops_the_top_holder():
    holders = [(99.3, "poolA"), (0.24, "walletB")]
    assert shadow._holder_pct_excluding_pool(holders, None) == pytest.approx(99.3)


@pytest.mark.asyncio
async def test_both_signals_are_carried_out_of_the_gate(monkeypatch):
    monkeypatch.setattr(
        shadow.rugcheck, "get_token_report",
        AsyncMock(return_value=_Report(99.3, top_holders=[(99.3, "poolA"), (0.24, "walletB")])),
    )
    gate = await shadow._holder_concentration_gate("mintA", "poolA")

    assert gate.top_holder_pct == pytest.approx(99.3)          # the traction signal, acted on
    assert gate.top_holder_excluding_pool_pct == pytest.approx(0.24)  # the real one, measured only


@pytest.mark.asyncio
async def test_a_single_wallet_over_the_threshold_is_blocked_even_on_a_clean_pool_share(monkeypatch):
    """The 20:14:18 case: 52.6% pool share reads perfectly "clean" against the
    traction threshold while one non-pool wallet holds enough to dump the pool.
    The wallet guardrail must fire independently of the traction one."""
    monkeypatch.setattr(
        shadow.rugcheck, "get_token_report",
        AsyncMock(return_value=_Report(52.6, top_holders=[(52.6, "poolA"), (25.0, "whaleB")])),
    )
    gate = await shadow._holder_concentration_gate("mintA", "poolA")

    assert gate.blocked is True
    assert gate.reason.startswith("blocked_wallet_concentration")
    assert gate.top_holder_excluding_pool_pct == pytest.approx(25.0)


@pytest.mark.asyncio
async def test_the_real_20h14_case_still_passes_below_the_threshold(monkeypatch):
    """Deliberately loose at 20%: the actual observed token (16.97%) must NOT
    be cut. The threshold is provisional market logic on n=1, so it may only
    catch flagrant cases until real data can recalibrate it."""
    monkeypatch.setattr(
        shadow.rugcheck, "get_token_report",
        AsyncMock(return_value=_Report(52.6, top_holders=[(52.6, "poolA"), (16.97, "walletB")])),
    )
    gate = await shadow._holder_concentration_gate("mintA", "poolA")

    assert gate.blocked is False
    assert gate.top_holder_excluding_pool_pct == pytest.approx(16.97)


@pytest.mark.asyncio
async def test_unknown_wallet_concentration_never_blocks_on_its_own(monkeypatch):
    """Missing pool-excluded data must not become a second silent fail-closed:
    the traction threshold still applies, but an unknown wallet share alone is
    not grounds to reject -- that would double the fail-closed surface on a
    threshold calibrated on n=1."""
    monkeypatch.setattr(
        shadow.rugcheck, "get_token_report",
        AsyncMock(return_value=_Report(52.6, top_holders=[(52.6, "poolA")])),
    )
    gate = await shadow._holder_concentration_gate("mintA", "poolA")

    assert gate.blocked is False
    assert gate.top_holder_excluding_pool_pct is None


@pytest.mark.asyncio
async def test_an_unknown_pool_address_never_triggers_the_wallet_reject(monkeypatch):
    """Without the pool address the pool cannot be told apart from a wallet,
    and since it holds 92-100% of a fresh launch it would trip the 20%
    threshold itself -- blocking essentially every token. The value is still
    RECORDED for measurement, just never acted on."""
    monkeypatch.setattr(
        shadow.rugcheck, "get_token_report",
        AsyncMock(return_value=_Report(99.0, top_holders=[(99.0, "poolA"), (0.2, "walletB")])),
    )
    gate = await shadow._holder_concentration_gate("mintA", None)

    # Blocked by the TRACTION threshold (99 >= 92), never by the wallet one.
    assert gate.reason.startswith("blocked_holder_concentration")
    assert gate.top_holder_excluding_pool_pct == pytest.approx(99.0)  # recorded, not acted on


# --- 20/08, never-checked positions get the REST budget first --------------
# Root cause of the liquidity_collapse late catches: a brand-new position was
# ranked on its (recent) detected_at and sorted to the BACK of the queue, so
# its first check landed 32-116s after entry despite a 10s cadence -- and on
# 5 of 16 real closures the reserve was already 97-100% gone by then.

@pytest.mark.asyncio
async def test_a_never_checked_position_is_served_before_an_older_recently_checked_one():
    """The exact inversion found in prod: 2h-old-but-just-checked used to win
    over 5s-old-and-never-checked."""
    old_id = await _insert_open_row(pool_address="oldPool", entry_price=1.0, minutes_ago=120.0)
    async with aiosqlite.connect(shadow._db_path()) as db:
        # Checked 40 seconds ago -- very recent.
        recent = (datetime.now(timezone.utc) - timedelta(seconds=40)).isoformat()
        await db.execute(
            f"UPDATE {shadow.TABLE} SET last_checked_at = ? WHERE id = ?", (recent, old_id),
        )
        await db.commit()
    fresh_id = await _insert_open_row(pool_address="freshPool", entry_price=1.0, minutes_ago=0.1)

    async with aiosqlite.connect(shadow._db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            f"SELECT id FROM {shadow.TABLE} WHERE chain = ? AND exit_reason IS NULL "
            f"ORDER BY (last_checked_at IS NOT NULL) ASC, "
            f"COALESCE(last_checked_at, detected_at) ASC",
            (CHAIN,),
        )
        order = [r["id"] for r in await cur.fetchall()]

    assert order[0] == fresh_id, "a never-checked position must get the capped REST budget first"
    assert order[1] == old_id


@pytest.mark.asyncio
async def test_round_robin_still_holds_among_already_checked_positions():
    """The fix must not break the round-robin it sits on top of: among rows
    that HAVE been checked, least-recently-checked still wins."""
    a_id = await _insert_open_row(pool_address="poolA", entry_price=1.0, minutes_ago=30.0)
    b_id = await _insert_open_row(pool_address="poolB", entry_price=1.0, minutes_ago=30.0)
    now = datetime.now(timezone.utc)
    async with aiosqlite.connect(shadow._db_path()) as db:
        await db.execute(
            f"UPDATE {shadow.TABLE} SET last_checked_at = ? WHERE id = ?",
            ((now - timedelta(seconds=5)).isoformat(), a_id),
        )
        await db.execute(
            f"UPDATE {shadow.TABLE} SET last_checked_at = ? WHERE id = ?",
            ((now - timedelta(seconds=300)).isoformat(), b_id),
        )
        await db.commit()

    async with aiosqlite.connect(shadow._db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            f"SELECT id FROM {shadow.TABLE} WHERE chain = ? AND exit_reason IS NULL "
            f"ORDER BY (last_checked_at IS NOT NULL) ASC, "
            f"COALESCE(last_checked_at, detected_at) ASC",
            (CHAIN,),
        )
        order = [r["id"] for r in await cur.fetchall()]

    assert order == [b_id, a_id]  # 300s-stale before 5s-fresh


# --- 20/08, coverage gap closed: run_forever_pumpportal ------------------
# The discovery loop that RUNS THIS POCKET IN PRODUCTION had zero direct test
# coverage -- found by auditing which functions carry no test at all while a
# fix was accumulating data. Everything it does (dedup, in_flight, stats,
# insertion) was only ever exercised one layer down.

class _FakeQueueFeed:
    """Drains a fixed list of events then returns None, like the real feed's
    `next_event(timeout=...)` does when its queue is empty."""

    def __init__(self, events):
        self._events = list(events)
        self.started = False

    async def start(self):
        self.started = True

    async def next_event(self, timeout=None):
        return self._events.pop(0) if self._events else None


@pytest.mark.asyncio
async def test_run_forever_pumpportal_confirms_and_inserts_a_real_candidate(monkeypatch):
    feed = _FakeQueueFeed([_pp_event(mint="mintA", bonding_curve_key="curveA")])
    monkeypatch.setattr(
        shadow, "_track_candidate_pumpportal",
        AsyncMock(return_value={
            "pool_address": "curveA", "token_address": "mintA", "chain": CHAIN, "symbol": "FRESH",
            "detected_at": datetime.now(timezone.utc).isoformat(), "entry_price": 1.0,
            "reserve_usd": 4000.0, "pool_created_at": None, "peak_price": 1.0,
            "realistic_entry_price": 0.99, "dex_id": "rest", "market_cap_sol_at_creation": None,
        }),
    )
    monkeypatch.setattr(shadow, "_enrich_with_rugcheck_pumpportal", AsyncMock())

    stats = await shadow.run_forever_pumpportal(feed, chain=CHAIN, max_events=1)

    assert feed.started is True
    assert stats["confirmed"] == 1
    rows = await _rows()
    assert len(rows) == 1 and rows[0]["pool_address"] == "curveA"


@pytest.mark.asyncio
async def test_run_forever_pumpportal_dedups_the_same_key_within_one_run(monkeypatch):
    """The in_flight guard, exercised through the real loop rather than one
    layer down: the same key broadcast twice must only be tracked once."""
    event = _pp_event(mint="mintA", bonding_curve_key="curveDup")
    feed = _FakeQueueFeed([event, event])
    started = asyncio.Event()

    async def _slow_track(*_a, **_kw):
        started.set()
        await asyncio.sleep(0.05)  # still in flight when the duplicate arrives
        return None

    monkeypatch.setattr(shadow, "_track_candidate_pumpportal", _slow_track)

    stats = await shadow.run_forever_pumpportal(feed, chain=CHAIN, max_events=2)

    assert stats.get("deduped_in_flight") == 1
    assert stats.get("abandoned") == 1


@pytest.mark.asyncio
async def test_run_forever_pumpportal_counts_an_abandoned_candidate_without_inserting(monkeypatch):
    feed = _FakeQueueFeed([_pp_event(bonding_curve_key="curveX")])
    monkeypatch.setattr(shadow, "_track_candidate_pumpportal", AsyncMock(return_value=None))

    stats = await shadow.run_forever_pumpportal(feed, chain=CHAIN, max_events=1)

    assert stats["abandoned"] == 1
    assert await _rows() == []


@pytest.mark.asyncio
async def test_run_forever_pumpportal_survives_a_tracking_error(monkeypatch):
    """One bad candidate must never kill the loop that runs the whole pocket."""
    feed = _FakeQueueFeed([_pp_event(bonding_curve_key="curveBoom")])
    monkeypatch.setattr(shadow, "_track_candidate_pumpportal", AsyncMock(side_effect=RuntimeError("boom")))

    stats = await shadow.run_forever_pumpportal(feed, chain=CHAIN, max_events=1)

    assert stats["errors"] == 1


@pytest.mark.asyncio
async def test_run_forever_pumpportal_stops_on_the_stop_event(monkeypatch):
    feed = _FakeQueueFeed([_pp_event() for _ in range(5)])
    stop = asyncio.Event()
    stop.set()

    stats = await shadow.run_forever_pumpportal(feed, chain=CHAIN, stop_event=stop)

    assert stats.get("confirmed", 0) == 0  # stopped before draining anything
