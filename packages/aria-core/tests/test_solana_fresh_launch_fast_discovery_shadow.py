"""Solana fresh-launch FAST-DISCOVERY shadow (19/08) -- real-time PumpPortal
discovery + liquidity-confirmation A/B counterpart to
``solana_fresh_launch_shadow.py``/``solana_fresh_launch_ws_exit_shadow.py``.
Same isolated-tmp-db + injected-client/injected-feed pattern as every other
shadow test file in this dome; never a real network call."""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import aiosqlite
import pytest

from aria_core import solana_fresh_launch_fast_discovery_shadow as shadow
from aria_core import solana_fresh_launch_shadow as original_shadow
from aria_core import solana_fresh_launch_ws_exit_shadow as ws_exit_shadow
from aria_core.services.geckoterminal import OHLCVResult, PoolSnapshot
from aria_core.services.pumpportal_ws import PumpPortalNewTokenEvent
from aria_core.services.rugcheck import RugCheckReport

CHAIN = "solana"


@pytest.fixture(autouse=True)
async def _tmp_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "shadow.db")
    monkeypatch.setattr(shadow, "DB_PATH", db_path)
    shadow._ensured_db_paths.clear()
    await shadow._ensure_table()
    yield
    shadow._ensured_db_paths.clear()


@pytest.fixture(autouse=True)
def _no_real_rugcheck(monkeypatch):
    """Every confirm-path test reaches the rugcheck enrichment call (unlike
    the siblings' optional token_address, this module's PumpPortalNewTokenEvent.mint
    is always set) -- never a real network attempt in a test."""
    monkeypatch.setattr(shadow.rugcheck, "get_token_report", AsyncMock(return_value=RugCheckReport(available=False)))


def _event(
    *, mint="mintAAA", bonding_curve_key="curveAAA", symbol="FRESH", name="Fresh Coin",
    detected_at=None, market_cap_sol=20.0, v_sol_in_bonding_curve=35.0,
) -> PumpPortalNewTokenEvent:
    return PumpPortalNewTokenEvent(
        mint=mint, symbol=symbol, name=name, pool="pump", bonding_curve_key=bonding_curve_key,
        market_cap_sol=market_cap_sol, v_sol_in_bonding_curve=v_sol_in_bonding_curve,
        v_tokens_in_bonding_curve=900_000_000.0, sol_amount=5.0, initial_buy=100_000_000.0,
        signature="sigAAA", detected_at=detected_at if detected_at is not None else time.time(),
    )


async def _rows() -> list[dict]:
    async with aiosqlite.connect(shadow._db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(f"SELECT * FROM {shadow.TABLE}")
        return [dict(r) for r in await cur.fetchall()]


# --- entry thresholds/exit rule: imported, never redefined -----------------

def test_entry_thresholds_are_the_same_imported_objects_as_original_module():
    """MIN_LIQUIDITY_USD is DELIBERATELY decoupled as of 20/08 (own value,
    see its docstring) -- real per-pocket data showed this pocket's optimal
    liquidity band diverges from the original module's."""
    assert shadow.MAX_POOL_AGE_MINUTES is original_shadow.MAX_POOL_AGE_MINUTES
    assert shadow.PEAK_PRICE_SANITY_MULTIPLE is original_shadow.PEAK_PRICE_SANITY_MULTIPLE
    assert shadow.MIN_LIQUIDITY_USD != original_shadow.MIN_LIQUIDITY_USD


def test_exit_rule_is_the_same_imported_function_as_ws_exit_sibling():
    assert shadow.evaluate_exit is ws_exit_shadow.evaluate_exit
    assert shadow.TRAILING_STOP_PCT == ws_exit_shadow.TRAILING_STOP_PCT


# --- _fetch_pool_snapshot_rest: parses the real DexPaprika detail shape ----
# 19/08: briefly removed entirely (operator-directed, "il sert a rien"),
# reinstated minutes later after a real measured regression (zero candidates
# confirmed for 10+ minutes during a bonding_ws_feed reconnect storm, with no
# fallback left) -- see _resolve_liquidity_snapshot's own docstring.

@pytest.mark.asyncio
async def test_fetch_pool_snapshot_rest_parses_real_shaped_response(monkeypatch):
    # Verbatim field subset from the real live call made 19/08 against
    # api.dexpaprika.com/networks/solana/pools/{bonding_curve_key} (see
    # module docstring) -- never a guessed shape.
    payload = {
        "id": "ErUB84BnC1pHoxQbjYZKFvTHbWz1uAAqJFxwKyr2Pq2f",
        "dex_id": "pumpfun",
        "created_at": "2026-08-19T14:32:17Z",
        "liquidity_usd": 635.3094642697921,
        "last_price_usd": 0.0000000826,
    }
    monkeypatch.setattr(shadow.dexpaprika, "_get_json", AsyncMock(return_value=(payload, None)))
    price, reserve, created_at = await shadow._fetch_pool_snapshot_rest("curveAAA", CHAIN)
    assert price == pytest.approx(0.0000000826)
    assert reserve == pytest.approx(635.3094642697921)
    assert created_at == datetime(2026, 8, 19, 14, 32, 17, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_fetch_pool_snapshot_rest_never_fabricates_on_error(monkeypatch):
    monkeypatch.setattr(shadow.dexpaprika, "_get_json", AsyncMock(return_value=(None, "unavailable")))
    price, reserve, created_at = await shadow._fetch_pool_snapshot_rest("curveAAA", CHAIN)
    assert (price, reserve, created_at) == (None, None, None)


@pytest.mark.asyncio
async def test_fetch_pool_snapshot_rest_handles_unparseable_created_at(monkeypatch):
    payload = {"liquidity_usd": 1000.0, "last_price_usd": 0.01, "created_at": "not-a-date"}
    monkeypatch.setattr(shadow.dexpaprika, "_get_json", AsyncMock(return_value=(payload, None)))
    price, reserve, created_at = await shadow._fetch_pool_snapshot_rest("curveAAA", CHAIN)
    assert price == pytest.approx(0.01)
    assert reserve == pytest.approx(1000.0)
    assert created_at is None  # never fabricated


# --- _resolve_liquidity_snapshot: bonding_ws_feed then pumpswap_ws, REST as
# a last-resort safety net (see module docstring for why REST can't be
# dropped) ------------------------------------------------------------------

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


@pytest.mark.asyncio
async def test_resolve_liquidity_snapshot_uses_pumpswap_ws_when_available(monkeypatch):
    ws_feed = FakeWsFeed({"curveAAA": FakeSnapshot(available=True, price_usd=0.05, reserve_usd=9000.0)})
    rest_mock = AsyncMock()
    monkeypatch.setattr(shadow, "_fetch_pool_snapshot_rest", rest_mock)
    price, reserve, created_at, source = await shadow._resolve_liquidity_snapshot("curveAAA", chain=CHAIN, ws_feed=ws_feed)
    assert (price, reserve, source) == (0.05, 9000.0, "pumpswap_ws")
    assert created_at is None  # never known via this tier
    rest_mock.assert_not_called()  # REST never consulted once a websocket tier already answered


@pytest.mark.asyncio
async def test_resolve_liquidity_snapshot_falls_back_to_rest_when_both_websockets_fail(monkeypatch):
    """Mission-required coverage: the real bug this restores REST for --
    both websocket tiers unavailable (e.g. a reconnect storm) must still
    fall back cleanly to the REST DexPaprika tier rather than silently
    stalling every candidate."""
    ws_feed = FakeWsFeed({"curveAAA": FakeSnapshot(available=False)})
    monkeypatch.setattr(
        shadow, "_fetch_pool_snapshot_rest",
        AsyncMock(return_value=(0.02, 2500.0, datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc))),
    )
    price, reserve, created_at, source = await shadow._resolve_liquidity_snapshot("curveAAA", chain=CHAIN, ws_feed=ws_feed)
    assert ws_feed.calls == ["curveAAA"]  # the real-time tier was genuinely attempted first
    assert (price, reserve, source) == (0.02, 2500.0, "rest_dexpaprika")
    assert created_at == datetime(2026, 8, 19, 12, 0, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_resolve_liquidity_snapshot_falls_back_to_rest_when_no_ws_feed_given(monkeypatch):
    monkeypatch.setattr(shadow, "_fetch_pool_snapshot_rest", AsyncMock(return_value=(0.03, 5000.0, None)))
    price, reserve, created_at, source = await shadow._resolve_liquidity_snapshot("curveAAA", chain=CHAIN, ws_feed=None)
    assert (price, reserve, source) == (0.03, 5000.0, "rest_dexpaprika")


@pytest.mark.asyncio
async def test_resolve_liquidity_snapshot_both_tiers_empty_returns_all_none(monkeypatch):
    ws_feed = FakeWsFeed({})
    monkeypatch.setattr(shadow, "_fetch_pool_snapshot_rest", AsyncMock(return_value=(None, None, None)))
    result = await shadow._resolve_liquidity_snapshot("curveAAA", chain=CHAIN, ws_feed=ws_feed)
    assert result == (None, None, None, None)


# --- _track_candidate: the core discovery+confirm loop ----------------------

@pytest.mark.asyncio
async def test_candidate_reaching_liquidity_in_time_is_confirmed_with_real_age(monkeypatch):
    """Mission-required coverage: a token that crosses MIN_LIQUIDITY_USD
    before the age ceiling is logged with its REAL age at confirmation."""
    t0 = 1_000_000.0
    event = _event(detected_at=t0)
    created_at = datetime.fromtimestamp(t0 - 12.0, tz=timezone.utc)  # real on-chain creation, 12s before we saw it

    calls = {"n": 0}

    async def fake_resolve(pool_address, *, chain, ws_feed, bonding_ws_feed=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return (0.001, shadow.MIN_LIQUIDITY_USD - 500.0, None, "rest_dexpaprika")  # below floor
        return (0.001, shadow.MIN_LIQUIDITY_USD + 100.0, created_at, "rest_dexpaprika")  # crosses floor

    fake_clock = {"t": t0}

    def fake_time():
        fake_clock["t"] += 7.0  # each check/confirm advances the wall clock by 7s
        return fake_clock["t"]

    sleeps: list[float] = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    row = await shadow._track_candidate(
        event, chain=CHAIN, resolve_fn=fake_resolve, sleep_fn=fake_sleep, time_fn=fake_time,
    )

    assert row is not None
    assert row["liquidity_confirmed_via"] == "rest_dexpaprika"
    assert row["pool_created_at"] == created_at.isoformat()
    # 3 fake_time() calls happen before confirmation: the age check on loop 1
    # (t0+7), the age check on loop 2 (t0+14), then entry_time itself
    # (t0+21) -- minus the real on-chain created_at (t0-12) = 33s.
    assert row["age_at_entry_seconds"] == pytest.approx(33.0)
    assert row["entry_price"] == pytest.approx(0.001)
    assert row["reserve_usd"] == pytest.approx(shadow.MIN_LIQUIDITY_USD + 100.0)
    assert sleeps == [shadow.FAST_DISCOVERY_POLL_INTERVAL_SECONDS]  # slept once, between the 2 checks


@pytest.mark.asyncio
async def test_candidate_confirmed_without_rest_created_at_falls_back_to_detected_at(monkeypatch):
    """When the pumpswap_ws tier confirms a candidate (never carries
    pool_created_at), age_at_entry_seconds must fall back to
    entry_time - event.detected_at rather than being left unresolved."""
    t0 = 2_000_000.0
    event = _event(detected_at=t0)

    async def fake_resolve(pool_address, *, chain, ws_feed, bonding_ws_feed=None):
        return (0.002, shadow.MIN_LIQUIDITY_USD + 1.0, None, "pumpswap_ws")

    def fake_time():
        return t0 + 22.5

    row = await shadow._track_candidate(
        event, chain=CHAIN, resolve_fn=fake_resolve, sleep_fn=AsyncMock(), time_fn=fake_time,
    )
    assert row is not None
    assert row["pool_created_at"] is None
    assert row["age_at_entry_seconds"] == pytest.approx(22.5)
    assert row["liquidity_confirmed_via"] == "pumpswap_ws"


@pytest.mark.asyncio
async def test_candidate_never_reaching_liquidity_is_abandoned_past_age_ceiling(monkeypatch):
    """Mission-required coverage: a token past MAX_POOL_AGE_MINUTES without
    crossing the liquidity floor must be abandoned -- never logged."""
    t0 = 3_000_000.0
    event = _event(detected_at=t0)

    async def fake_resolve(pool_address, *, chain, ws_feed, bonding_ws_feed=None):
        return (0.0005, 100.0, None, "rest_dexpaprika")  # always well below the floor

    fake_clock = {"t": t0}

    def fake_time():
        # Jumps straight past the ceiling on the very first check.
        fake_clock["t"] = t0 + shadow.MAX_POOL_AGE_MINUTES * 60.0 + 1.0
        return fake_clock["t"]

    row = await shadow._track_candidate(
        event, chain=CHAIN, resolve_fn=fake_resolve, sleep_fn=AsyncMock(), time_fn=fake_time,
    )
    assert row is None


@pytest.mark.asyncio
async def test_candidate_without_bonding_curve_key_is_never_tracked():
    event = _event(bonding_curve_key=None)
    row = await shadow._track_candidate(event, chain=CHAIN, resolve_fn=AsyncMock())
    assert row is None


@pytest.mark.asyncio
async def test_candidate_in_market_cap_dead_zone_rejected_before_add_pools():
    """20/08 -- see MARKET_CAP_SOL_AT_CREATION_REJECT_MIN/MAX's own docstring:
    30-50 SOL at creation is a confirmed dead zone (n=1024, 3.5-7.9% winrate).
    Rejected immediately, never even reaches add_pools()."""
    event = _event(market_cap_sol=40.0)  # inside [30, 50)

    class _FakeFeed:
        def __init__(self):
            self.added = []

        async def add_pools(self, pairs):
            self.added.extend(pairs)
            return len(pairs)

    bonding_feed = _FakeFeed()
    row = await shadow._track_candidate(
        event, chain=CHAIN, bonding_ws_feed=bonding_feed, resolve_fn=AsyncMock(),
    )
    assert row is None
    assert bonding_feed.added == []


@pytest.mark.asyncio
async def test_candidate_at_market_cap_dead_zone_boundaries_accepted():
    """Boundaries are exclusive/inclusive as documented: 30.0 rejects (band
    floor, inclusive), 50.0 does not (band ceiling, exclusive)."""
    event_at_ceiling = _event(market_cap_sol=50.0)
    resolve_fn = AsyncMock(return_value=(1.0, shadow.MIN_LIQUIDITY_USD, None, "rest_dexpaprika"))
    row = await shadow._track_candidate(event_at_ceiling, chain=CHAIN, resolve_fn=resolve_fn)
    assert row is not None


@pytest.mark.asyncio
async def test_abandoned_candidate_sheds_its_websocket_subscription(monkeypatch):
    """20/08, real incident: add_pools() was called unconditionally but this
    abandonment path never called remove_pools() -- most PumpPortal
    candidates never confirm liquidity, so nearly every add_pools() call
    leaked a permanent subscription, silently exceeding the Solana RPC's
    real accountSubscribe ceiling (1000/connection, confirmed live) within
    ~40 minutes of runtime."""
    t0 = 3_000_000.0
    event = _event(detected_at=t0)

    class _FakeFeed:
        def __init__(self):
            self.added: list = []
            self.removed: list = []

        async def add_pools(self, pairs):
            self.added.extend(pairs)
            return len(pairs)

        def remove_pools(self, addrs):
            self.removed.extend(addrs)

    bonding_feed = _FakeFeed()

    async def fake_resolve(pool_address, *, chain, ws_feed, bonding_ws_feed=None):
        return (0.0005, 100.0, None, "rest_dexpaprika")  # always well below the floor

    fake_clock = {"t": t0}

    def fake_time():
        fake_clock["t"] = t0 + shadow.MAX_POOL_AGE_MINUTES * 60.0 + 1.0
        return fake_clock["t"]

    row = await shadow._track_candidate(
        event, chain=CHAIN, bonding_ws_feed=bonding_feed,
        resolve_fn=fake_resolve, sleep_fn=AsyncMock(), time_fn=fake_time,
    )
    assert row is None
    assert bonding_feed.added == [(event.bonding_curve_key, event.mint)]
    assert bonding_feed.removed == [event.bonding_curve_key]


@pytest.mark.asyncio
async def test_track_candidate_respects_the_poll_interval_between_checks(monkeypatch):
    """'le débit respecte une limite raisonnable' -- never a busy loop: a
    candidate not yet confirmed must sleep FAST_DISCOVERY_POLL_INTERVAL_SECONDS
    between successive liquidity checks."""
    t0 = 4_000_000.0
    event = _event(detected_at=t0)
    call_count = {"n": 0}

    async def fake_resolve(pool_address, *, chain, ws_feed, bonding_ws_feed=None):
        call_count["n"] += 1
        if call_count["n"] >= 3:
            return (0.01, shadow.MIN_LIQUIDITY_USD, None, "rest_dexpaprika")
        return (0.01, 1.0, None, "rest_dexpaprika")

    sleeps: list[float] = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    await shadow._track_candidate(
        event, chain=CHAIN, resolve_fn=fake_resolve, sleep_fn=fake_sleep,
        time_fn=lambda: t0 + 1.0,
    )
    assert sleeps == [shadow.FAST_DISCOVERY_POLL_INTERVAL_SECONDS, shadow.FAST_DISCOVERY_POLL_INTERVAL_SECONDS]


def test_poll_interval_is_a_sane_reasonable_value():
    # Never a busy-loop constant (0 or near-0), never absurdly slow either --
    # matches the cadence already used live in the originating probe.
    assert 1.0 <= shadow.FAST_DISCOVERY_POLL_INTERVAL_SECONDS <= 30.0


# --- insert + dedup ----------------------------------------------------------

@pytest.mark.asyncio
async def test_insert_confirmed_row_then_dedup_suppresses_a_second_signal():
    row = {
        "pool_address": "curveAAA", "token_address": "mintAAA", "chain": CHAIN, "symbol": "FRESH",
        "name": "Fresh Coin", "discovery_source": "pumpportal",
        "first_seen_at": datetime.now(timezone.utc).isoformat(), "detected_at": datetime.now(timezone.utc).isoformat(),
        "pool_created_at": None, "age_at_entry_seconds": 30.0, "liquidity_confirmed_via": "rest_dexpaprika",
        "entry_price": 0.001, "reserve_usd": shadow.MIN_LIQUIDITY_USD, "peak_price": 0.001,
        "realistic_entry_price": 0.001, "market_cap_sol_at_creation": 40.0,
        "v_sol_in_bonding_curve_at_creation": 35.0, "rugcheck_score": None, "rugcheck_risks": None,
        "rugcheck_top_holder_pct": None, "rugcheck_creator": None,
    }
    new_id = await shadow._insert_confirmed_row(row)
    assert new_id > 0
    rows = await _rows()
    assert len(rows) == 1
    assert rows[0]["remaining_qty"] == 1.0
    assert rows[0]["realized_proceeds"] == 0.0

    async with aiosqlite.connect(shadow._db_path()) as db:
        assert await shadow._has_open_or_recent_signal(db, "curveAAA", CHAIN) is True
        assert await shadow._has_open_or_recent_signal(db, "curveBBB", CHAIN) is False


@pytest.mark.asyncio
async def test_track_and_maybe_insert_skips_a_deduped_candidate(monkeypatch):
    await shadow._insert_confirmed_row({
        "pool_address": "curveAAA", "token_address": "mintAAA", "chain": CHAIN, "symbol": "FRESH",
        "name": "Fresh Coin", "discovery_source": "pumpportal",
        "first_seen_at": datetime.now(timezone.utc).isoformat(), "detected_at": datetime.now(timezone.utc).isoformat(),
        "pool_created_at": None, "age_at_entry_seconds": 30.0, "liquidity_confirmed_via": "rest_dexpaprika",
        "entry_price": 0.001, "reserve_usd": shadow.MIN_LIQUIDITY_USD, "peak_price": 0.001,
        "realistic_entry_price": 0.001, "market_cap_sol_at_creation": 40.0,
        "v_sol_in_bonding_curve_at_creation": 35.0, "rugcheck_score": None, "rugcheck_risks": None,
        "rugcheck_top_holder_pct": None, "rugcheck_creator": None,
    })
    track_mock = AsyncMock()
    monkeypatch.setattr(shadow, "_track_candidate", track_mock)
    stats: dict = {}
    await shadow._track_and_maybe_insert(
        _event(bonding_curve_key="curveAAA"), chain=CHAIN, ws_feed=None,
        semaphore=asyncio.Semaphore(1), stats=stats,
    )
    assert stats.get("deduped") == 1
    track_mock.assert_not_called()


@pytest.mark.asyncio
async def test_track_and_maybe_insert_records_confirmed_and_abandoned(monkeypatch):
    monkeypatch.setattr(shadow, "_track_candidate", AsyncMock(return_value=None))
    stats: dict = {}
    await shadow._track_and_maybe_insert(
        _event(bonding_curve_key="curveNever"), chain=CHAIN, ws_feed=None,
        semaphore=asyncio.Semaphore(1), stats=stats,
    )
    assert stats.get("abandoned") == 1
    assert await _rows() == []


@pytest.mark.asyncio
async def test_track_and_maybe_insert_skips_a_key_already_in_flight(monkeypatch):
    """20/08 -- real incident: a candidate that never confirms has no DB row,
    so the DB-only dedup above can never catch a re-broadcast of the same
    key. `in_flight` closes that gap -- checked BEFORE the DB dedup, before
    even the semaphore."""
    track_mock = AsyncMock()
    monkeypatch.setattr(shadow, "_track_candidate", track_mock)
    stats: dict = {}
    in_flight = {"curveStuck"}
    await shadow._track_and_maybe_insert(
        _event(bonding_curve_key="curveStuck"), chain=CHAIN, ws_feed=None,
        semaphore=asyncio.Semaphore(1), stats=stats, in_flight=in_flight,
    )
    assert stats.get("deduped_in_flight") == 1
    track_mock.assert_not_called()
    assert in_flight == {"curveStuck"}  # untouched -- the ALREADY-in-flight task owns removal


@pytest.mark.asyncio
async def test_track_and_maybe_insert_adds_then_removes_from_in_flight(monkeypatch):
    monkeypatch.setattr(shadow, "_track_candidate", AsyncMock(return_value=None))
    stats: dict = {}
    in_flight: set = set()
    await shadow._track_and_maybe_insert(
        _event(bonding_curve_key="curveTransient"), chain=CHAIN, ws_feed=None,
        semaphore=asyncio.Semaphore(1), stats=stats, in_flight=in_flight,
    )
    assert stats.get("abandoned") == 1
    assert in_flight == set()  # removed once the task finished, never left dangling


@pytest.mark.asyncio
async def test_track_and_maybe_insert_removes_from_in_flight_even_on_error(monkeypatch):
    monkeypatch.setattr(shadow, "_track_candidate", AsyncMock(side_effect=RuntimeError("boom")))
    stats: dict = {}
    in_flight: set = set()
    await shadow._track_and_maybe_insert(
        _event(bonding_curve_key="curveBoom"), chain=CHAIN, ws_feed=None,
        semaphore=asyncio.Semaphore(1), stats=stats, in_flight=in_flight,
    )
    assert stats.get("errors") == 1
    assert in_flight == set()


# --- run_forever: wiring + bounded concurrency -------------------------------

@pytest.mark.asyncio
async def test_run_forever_processes_queued_events_up_to_max_events(monkeypatch):
    class FakeFeed:
        def __init__(self, events):
            self._events = list(events)
            self.started = False

        async def start(self):
            self.started = True

        async def next_event(self, timeout=None):
            if self._events:
                return self._events.pop(0)
            await asyncio.sleep(0)
            return None

    events = [_event(mint=f"mint{i}", bonding_curve_key=f"curve{i}") for i in range(3)]
    monkeypatch.setattr(
        shadow, "_track_candidate",
        AsyncMock(side_effect=lambda event, **kw: {
            "pool_address": event.bonding_curve_key, "token_address": event.mint, "chain": CHAIN,
            "symbol": "X", "name": "X", "discovery_source": "pumpportal",
            "first_seen_at": datetime.now(timezone.utc).isoformat(), "detected_at": datetime.now(timezone.utc).isoformat(),
            "pool_created_at": None, "age_at_entry_seconds": 10.0, "liquidity_confirmed_via": "rest_dexpaprika",
            "entry_price": 0.01, "reserve_usd": shadow.MIN_LIQUIDITY_USD, "peak_price": 0.01,
            "realistic_entry_price": 0.01, "market_cap_sol_at_creation": 40.0,
            "v_sol_in_bonding_curve_at_creation": 35.0, "rugcheck_score": None, "rugcheck_risks": None,
            "rugcheck_top_holder_pct": None, "rugcheck_creator": None,
        }),
    )
    feed = FakeFeed(events)
    stats = await shadow.run_forever(feed, chain=CHAIN, max_events=3)
    assert feed.started is True
    assert stats.get("confirmed") == 3
    assert len(await _rows()) == 3


@pytest.mark.asyncio
async def test_run_forever_respects_max_concurrent_tracked_candidates(monkeypatch):
    class FakeFeed:
        def __init__(self, events):
            self._events = list(events)

        async def start(self):
            pass

        async def next_event(self, timeout=None):
            if self._events:
                return self._events.pop(0)
            await asyncio.sleep(0)
            return None

    events = [_event(mint=f"mint{i}", bonding_curve_key=f"curve{i}") for i in range(8)]
    in_flight = {"current": 0, "max_seen": 0}
    release = asyncio.Event()

    async def fake_track(event, **kw):
        in_flight["current"] += 1
        in_flight["max_seen"] = max(in_flight["max_seen"], in_flight["current"])
        await release.wait()
        in_flight["current"] -= 1
        return None

    monkeypatch.setattr(shadow, "_track_candidate", fake_track)

    async def _runner():
        return await shadow.run_forever(FakeFeed(events), chain=CHAIN, max_concurrent=3, max_events=8)

    task = asyncio.create_task(_runner())
    for _ in range(100):
        if in_flight["current"] >= 3 or task.done():
            break
        await asyncio.sleep(0.01)
    assert in_flight["max_seen"] <= 3
    release.set()
    await task


# --- chain_pnl_summary_realistic / summary -----------------------------------

_AUTO = object()


async def _insert_row(
    *, pool_address="curveA", entry_price=1.0, realistic_entry_price=_AUTO, exit_reason=None,
    realistic_final_multiplier=None, realistic_realized_proceeds=0.0, remaining_qty=1.0,
    last_price=None, age_at_entry_seconds=30.0, liquidity_confirmed_via="rest_dexpaprika",
) -> int:
    if realistic_entry_price is _AUTO:
        realistic_entry_price = entry_price
    # summary() reads the non-realistic `final_multiplier` column (mirrors
    # both siblings' own summary() convention) -- kept equal to
    # realistic_final_multiplier here since these tests never exercise the
    # realistic-vs-ideal divergence itself.
    final_multiplier = realistic_final_multiplier
    async with aiosqlite.connect(shadow._db_path()) as db:
        cur = await db.execute(
            f"""
            INSERT INTO {shadow.TABLE}
                (pool_address, chain, first_seen_at, detected_at, entry_price, reserve_usd,
                 age_at_entry_seconds, liquidity_confirmed_via, remaining_qty, realized_proceeds,
                 peak_price, realistic_entry_price, exit_reason, final_multiplier, realistic_final_multiplier,
                 realistic_realized_proceeds, last_price)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0.0, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pool_address, CHAIN, datetime.now(timezone.utc).isoformat(), datetime.now(timezone.utc).isoformat(),
                entry_price, 5000.0, age_at_entry_seconds, liquidity_confirmed_via, remaining_qty, entry_price,
                realistic_entry_price, exit_reason, final_multiplier, realistic_final_multiplier,
                realistic_realized_proceeds, last_price,
            ),
        )
        await db.commit()
        return cur.lastrowid


@pytest.mark.asyncio
async def test_chain_pnl_summary_categorizes_closed_and_open_and_unreachable():
    await _insert_row(pool_address="curveClosed", entry_price=1.0, realistic_final_multiplier=1.5, exit_reason="trailing_stop")
    await _insert_row(pool_address="curveOpen", entry_price=1.0, last_price=1.2)
    await _insert_row(pool_address="curvePending", entry_price=1.0)
    await _insert_row(pool_address="curveNever", entry_price=1.0, realistic_entry_price=None)

    pnl = await shadow.chain_pnl_summary_realistic(CHAIN)
    assert pnl["closed"] == 1
    assert pnl["open_valued"] == 1
    assert pnl["pending_price"] == 1
    assert pnl["unreachable_liquidity"] == 1
    assert pnl["total_pnl_units"] == pytest.approx(0.5 + 0.2)


@pytest.mark.asyncio
async def test_summary_aggregates_win_rate_and_avg_age():
    await _insert_row(
        pool_address="curveA", entry_price=1.0, realistic_final_multiplier=2.0, exit_reason="trailing_stop",
        age_at_entry_seconds=25.0, liquidity_confirmed_via="rest_dexpaprika",
    )
    await _insert_row(
        pool_address="curveB", entry_price=1.0, realistic_final_multiplier=0.5, exit_reason="max_hold",
        age_at_entry_seconds=45.0, liquidity_confirmed_via="pumpswap_ws",
    )
    result = await shadow.summary(chain=CHAIN)
    assert result["completed"] == 2
    assert result["wins"] == 1
    assert result["win_rate"] == pytest.approx(0.5)
    assert result["avg_age_at_entry_seconds"] == pytest.approx(35.0)
    assert result["by_exit_reason"] == {"trailing_stop": 1, "max_hold": 1}
    assert result["by_liquidity_confirmed_via"] == {"rest_dexpaprika": 1, "pumpswap_ws": 1}


# --- advance_exit_simulation: REST + websocket orchestration ----------------

class FakeClient:
    def __init__(self, price_by_pool, reserve_by_pool=None, dex_id_by_pool=None):
        self._prices = price_by_pool
        self._reserves = dict(reserve_by_pool or {})
        self._dex_ids = dict(dex_id_by_pool or {})
        self.calls: list[str] = []

    async def get_pool_snapshot(self, pool_address, *, network="solana"):
        self.calls.append(pool_address)
        price = self._prices.get(pool_address)
        if price is None:
            return PoolSnapshot(pool_address=pool_address, available=False, error="unavailable")
        return PoolSnapshot(
            pool_address=pool_address, price_usd=price, available=True,
            reserve_usd=self._reserves.get(pool_address, 1000.0), dex_id=self._dex_ids.get(pool_address),
        )

    async def get_ohlcv(self, pool_address, *, network="solana", mode="standard", **_kwargs):
        return OHLCVResult(candles=[], available=False, error="unavailable")


async def _insert_open_row(
    *, pool_address="curveA", entry_price=1.0, minutes_ago=1.0, reserve_usd=8000.0, realistic_entry_price=None,
) -> int:
    if realistic_entry_price is None:
        realistic_entry_price = entry_price
    detected_at = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
    async with aiosqlite.connect(shadow._db_path()) as db:
        cur = await db.execute(
            f"""
            INSERT INTO {shadow.TABLE}
                (pool_address, chain, first_seen_at, detected_at, entry_price, reserve_usd,
                 remaining_qty, realized_proceeds, peak_price, realistic_entry_price)
            VALUES (?, ?, ?, ?, ?, ?, 1.0, 0.0, ?, ?)
            """,
            (pool_address, CHAIN, detected_at, detected_at, entry_price, reserve_usd, entry_price, realistic_entry_price),
        )
        await db.commit()
        return cur.lastrowid


@pytest.mark.asyncio
async def test_advance_exit_simulation_via_rest_closes_on_trailing_stop():
    await _insert_open_row(pool_address="curveA", entry_price=1.0, reserve_usd=8000.0)
    stop_price = 1.0 * (1 - shadow.TRAILING_STOP_PCT / 100.0)
    client = FakeClient({"curveA": stop_price}, reserve_by_pool={"curveA": 8000.0})
    counts = await shadow.advance_exit_simulation(client, chain=CHAIN)
    assert counts["closed_trailing_stop"] == 1
    assert counts["checked_via_polling"] == 1
    rows = await _rows()
    assert rows[0]["exit_reason"] == "trailing_stop"
    assert rows[0]["exit_price_source"] == "polling"


@pytest.mark.asyncio
async def test_advance_exit_simulation_prefers_websocket_price_when_available():
    await _insert_open_row(pool_address="curveA", entry_price=1.0, reserve_usd=8000.0)
    ws_feed = FakeWsFeed({"curveA": FakeSnapshot(available=True, price_usd=1.0, reserve_usd=3000.0, dex_id="raydium")})
    client = FakeClient({})  # REST must never be consulted once the ws tier answered
    counts = await shadow.advance_exit_simulation(client, chain=CHAIN, ws_feed=ws_feed)
    assert counts["checked_via_websocket"] == 1
    assert counts["closed_liquidity_collapse"] == 1  # 3000 < 8000 * (1 - 50%)
    assert client.calls == []
    rows = await _rows()
    assert rows[0]["exit_price_source"] == "websocket"


@pytest.mark.asyncio
async def test_advance_exit_simulation_archives_snapshot_on_websocket_price(monkeypatch):
    """20/08 -- this pocket never archived a single exit-check snapshot on
    either path before this fix (see solana_fresh_launch_ws_exit_shadow's
    own version of this test for the real gap this closes)."""
    from aria_core import shadow_snapshot_archive

    store = AsyncMock(return_value=True)
    monkeypatch.setattr(shadow_snapshot_archive, "store_snapshot", store)
    await _insert_open_row(pool_address="curveA", entry_price=1.0, reserve_usd=8000.0)
    ws_feed = FakeWsFeed({"curveA": FakeSnapshot(available=True, price_usd=1.0, reserve_usd=6000.0, dex_id="raydium")})
    await shadow.advance_exit_simulation(FakeClient({}), chain=CHAIN, ws_feed=ws_feed)
    store.assert_awaited_once()
    assert store.await_args.kwargs["module"] == "solana_fresh_launch_fast_discovery"
    assert store.await_args.kwargs["reserve_usd"] == pytest.approx(6000.0)


@pytest.mark.asyncio
async def test_advance_exit_simulation_never_raises_on_a_single_pool_failure():
    await _insert_open_row(pool_address="curveA", entry_price=1.0)

    class BoomClient:
        async def get_pool_snapshot(self, *a, **kw):
            raise RuntimeError("simulated network failure")

        async def get_ohlcv(self, *a, **kw):
            raise RuntimeError("simulated network failure")

    counts = await shadow.advance_exit_simulation(BoomClient(), chain=CHAIN)
    assert counts["checked"] == 0
    rows = await _rows()
    assert rows[0]["last_checked_at"] is not None  # starvation-fix stamp still applied
