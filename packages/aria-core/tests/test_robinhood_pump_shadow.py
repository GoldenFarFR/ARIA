"""Robinhood Chain "take the train" shadow (16/08) -- pure read+log, never a
trigger. Mirrors test_solana_pump_shadow.py's isolated-tmp-db + detect/measure
state-machine test pattern (the GeckoTerminal client is always injected/
mocked, never a real network call, same doctrine as
test_geckoterminal_client.py), plus a dedicated RWA-exclusion test suite for
the Robinhood-specific Stock Token filter that has no Solana equivalent."""
from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone

import aiosqlite
import pytest

from aria_core import robinhood_pump_shadow as shadow
from aria_core.services.dexscreener import PairSnapshot
from aria_core.services.geckoterminal import OHLCVResult, PoolSnapshot, TrendingPool
from aria_core.skills.ta_levels import Candle

CHAIN = "robinhood"
_SENTINEL_USE_ENTRY_PRICE = object()


@pytest.fixture(autouse=True)
async def _tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(shadow, "DB_PATH", str(tmp_path / "shadow.db"))
    shadow._ensured_db_paths.clear()
    await shadow._ensure_table()
    yield
    shadow._ensured_db_paths.clear()


@pytest.fixture(autouse=True)
def _stock_token_registry_never_blocks_by_default(monkeypatch):
    """Every test other than the dedicated RWA-exclusion suite below should
    behave exactly like the Solana module's tests -- never silently blocked
    by a real network call to the Stock Token registry. Individual RWA tests
    override this back to a real/controlled stand-in."""
    async def _never_a_stock_token(contract, chain):
        return False

    monkeypatch.setattr(shadow, "is_stock_token", _never_a_stock_token)


async def _rows():
    async with aiosqlite.connect(shadow._db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM robinhood_pump_shadow_log")
        return [dict(r) for r in await cur.fetchall()]


def _pool(
    *, pool_address="poolA", token_address="tokA", symbol="PUMP",
    price_usd=1.0, m5=30.0, buyers=20, sellers=5, volume_m15=5000.0, reserve=100000.0,
    pool_created_at=None,
) -> TrendingPool:
    return TrendingPool(
        pool_address=pool_address, token_address=token_address, symbol=symbol,
        price_usd=price_usd,
        price_change_pct={"m5": m5, "m15": m5 + 5, "m30": m5 + 10, "h1": m5 + 15, "h6": m5 + 25, "h24": m5 + 35},
        transactions_m15={"buys": 40, "sells": 10, "buyers": buyers, "sellers": sellers},
        volume_usd_m15=volume_m15,
        reserve_usd=reserve,
        # 16/08 MAX_POOL_AGE_MINUTES fail-CLOSED protection -- defaults to
        # "just created" so existing tests keep passing a signal through
        # unless they explicitly opt into testing the age filter itself.
        pool_created_at=pool_created_at if pool_created_at is not None else datetime.now(timezone.utc),
    )


class FakeClient:
    """Injected in place of GeckoTerminalClient -- get_pool_snapshot is
    exercised by evaluate_open_signals/advance_exit_simulation, get_ohlcv
    by advance_exit_simulation's 16/08 window-based threshold check (see
    that function's docstring). ``ohlcv_by_pool`` defaults to nothing
    configured -> ``available=False`` for every pool, exercising the
    documented fall-back-to-point-sample path unless a test opts in."""

    def __init__(
        self, price_by_pool: dict[str, float | None],
        ohlcv_by_pool: dict[str, OHLCVResult] | None = None,
    ):
        self._prices = price_by_pool
        self._ohlcv = dict(ohlcv_by_pool or {})
        self.calls: list[str] = []
        self.ohlcv_calls: list[str] = []

    async def get_pool_snapshot(self, pool_address, *, network="robinhood"):
        self.calls.append(pool_address)
        price = self._prices.get(pool_address)
        if price is None:
            return PoolSnapshot(pool_address=pool_address, available=False, error="unavailable")
        return PoolSnapshot(pool_address=pool_address, price_usd=price, reserve_usd=1000.0, available=True)

    async def get_ohlcv(self, pool_address, *, network="robinhood", mode="standard", **_kwargs):
        self.ohlcv_calls.append(pool_address)
        result = self._ohlcv.get(pool_address)
        if result is None:
            return OHLCVResult(candles=[], available=False, error="unavailable")
        return result


# --- record_signals ------------------------------------------------------

@pytest.mark.asyncio
async def test_record_signals_logs_pool_above_threshold():
    logged = await shadow.record_signals([_pool(m5=30.0)], chain=CHAIN)
    assert logged == 1
    rows = await _rows()
    assert len(rows) == 1
    assert rows[0]["pool_address"] == "poolA"
    assert rows[0]["status"] == "open"
    assert rows[0]["m5_pct"] == 30.0
    assert rows[0]["entry_price"] == 1.0
    assert rows[0]["buyers_m15"] == 20
    assert rows[0]["sellers_m15"] == 5
    assert rows[0]["volume_usd_m15"] == 5000.0
    assert rows[0]["reserve_usd"] == 100000.0
    assert rows[0]["symbol"] == "PUMP"
    assert rows[0]["chain"] == "robinhood"


@pytest.mark.asyncio
async def test_record_signals_ignores_pool_below_threshold():
    logged = await shadow.record_signals([_pool(m5=24.9)], chain=CHAIN)
    assert logged == 0
    assert await _rows() == []


@pytest.mark.asyncio
async def test_record_signals_exactly_at_threshold_counts():
    logged = await shadow.record_signals([_pool(m5=shadow.M5_SURGE_THRESHOLD_PCT)], chain=CHAIN)
    assert logged == 1


@pytest.mark.asyncio
async def test_record_signals_never_fabricates_entry_price():
    logged = await shadow.record_signals([_pool(m5=40.0, price_usd=None)], chain=CHAIN)
    assert logged == 0
    assert await _rows() == []


@pytest.mark.asyncio
async def test_record_signals_rejects_pool_older_than_max_age():
    old = datetime.now(timezone.utc) - timedelta(minutes=shadow.MAX_POOL_AGE_MINUTES + 1)
    logged = await shadow.record_signals([_pool(m5=40.0, pool_created_at=old)], chain=CHAIN)
    assert logged == 0
    assert await _rows() == []


@pytest.mark.asyncio
async def test_record_signals_accepts_pool_within_max_age():
    fresh = datetime.now(timezone.utc) - timedelta(minutes=shadow.MAX_POOL_AGE_MINUTES - 1)
    logged = await shadow.record_signals([_pool(m5=40.0, pool_created_at=fresh)], chain=CHAIN)
    assert logged == 1


@pytest.mark.asyncio
async def test_record_signals_rejects_pool_with_unknown_age():
    pool = _pool(m5=40.0)
    pool = dataclasses.replace(pool, pool_created_at=None)
    logged = await shadow.record_signals([pool], chain=CHAIN)
    assert logged == 0  # fail-CLOSED: an unknown age is never assumed safe
    assert await _rows() == []


@pytest.mark.asyncio
async def test_record_signals_dedupes_while_already_open():
    await shadow.record_signals([_pool(m5=30.0, price_usd=1.0)], chain=CHAIN)
    logged_again = await shadow.record_signals([_pool(m5=45.0, price_usd=1.4)], chain=CHAIN)
    assert logged_again == 0
    rows = await _rows()
    assert len(rows) == 1
    assert rows[0]["entry_price"] == 1.0  # first signal wins, never overwritten


@pytest.mark.asyncio
async def test_record_signals_relogs_after_previous_signal_closed():
    await shadow.record_signals([_pool(m5=30.0)], chain=CHAIN)
    async with aiosqlite.connect(shadow._db_path()) as db:
        await db.execute("UPDATE robinhood_pump_shadow_log SET status = 'closed'")
        await db.commit()
    logged_again = await shadow.record_signals([_pool(m5=30.0)], chain=CHAIN)
    assert logged_again == 1
    rows = await _rows()
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_record_signals_multiple_pools_independent():
    pools = [_pool(pool_address="poolA", m5=30.0), _pool(pool_address="poolB", m5=26.0)]
    logged = await shadow.record_signals(pools, chain=CHAIN)
    assert logged == 2
    assert {r["pool_address"] for r in await _rows()} == {"poolA", "poolB"}


@pytest.mark.asyncio
async def test_record_signals_never_raises_on_db_failure(monkeypatch):
    monkeypatch.setattr(shadow, "DB_PATH", "/nonexistent/dir/shadow.db")
    shadow._ensured_db_paths.clear()
    logged = await shadow.record_signals([_pool(m5=30.0)], chain=CHAIN)
    assert logged == 0  # fails closed, never raises into the caller


# --- _snapshot_with_fallback (16/08 API cascade) ---------------------------

@pytest.mark.asyncio
async def test_snapshot_fallback_uses_dexscreener_when_available(monkeypatch):
    async def fake_fetch_token_pairs(contract, *, chain="robinhood"):
        return [PairSnapshot(base_address=contract, price_usd=3.5, liquidity_usd=10000.0)]

    monkeypatch.setattr(shadow.dexscreener, "fetch_token_pairs", fake_fetch_token_pairs)
    client = FakeClient({"poolA": 99.0})  # would prove wrong if GeckoTerminal got used instead
    snapshot = await shadow._snapshot_with_fallback(client, "poolA", "tokA", chain=CHAIN)
    assert snapshot.available is True
    assert snapshot.price_usd == 3.5
    assert snapshot.reserve_usd == 10000.0
    assert client.calls == []  # GeckoTerminal never called -- DexScreener answered first


@pytest.mark.asyncio
async def test_snapshot_fallback_falls_back_to_geckoterminal_when_dexscreener_empty(monkeypatch):
    async def fake_fetch_token_pairs(contract, *, chain="robinhood"):
        return []

    monkeypatch.setattr(shadow.dexscreener, "fetch_token_pairs", fake_fetch_token_pairs)
    client = FakeClient({"poolA": 2.0})
    snapshot = await shadow._snapshot_with_fallback(client, "poolA", "tokA", chain=CHAIN)
    assert snapshot.available is True
    assert snapshot.price_usd == 2.0
    assert client.calls == ["poolA"]  # GeckoTerminal used as the real fallback


@pytest.mark.asyncio
async def test_snapshot_fallback_falls_back_on_dexscreener_exception(monkeypatch):
    async def broken_fetch_token_pairs(contract, *, chain="robinhood"):
        raise RuntimeError("dexscreener unreachable")

    monkeypatch.setattr(shadow.dexscreener, "fetch_token_pairs", broken_fetch_token_pairs)
    client = FakeClient({"poolA": 2.0})
    snapshot = await shadow._snapshot_with_fallback(client, "poolA", "tokA", chain=CHAIN)
    assert snapshot.available is True
    assert snapshot.price_usd == 2.0


@pytest.mark.asyncio
async def test_snapshot_fallback_unavailable_when_both_sources_fail(monkeypatch):
    async def fake_fetch_token_pairs(contract, *, chain="robinhood"):
        return []

    monkeypatch.setattr(shadow.dexscreener, "fetch_token_pairs", fake_fetch_token_pairs)
    client = FakeClient({"poolA": None})  # GeckoTerminal also has nothing
    snapshot = await shadow._snapshot_with_fallback(client, "poolA", "tokA", chain=CHAIN)
    assert snapshot.available is False  # never fabricated, both sources genuinely empty


@pytest.mark.asyncio
async def test_snapshot_fallback_skips_dexscreener_without_a_token_address(monkeypatch):
    async def unexpected_call(contract, *, chain="robinhood"):
        raise AssertionError("dexscreener should never be called without a token_address")

    monkeypatch.setattr(shadow.dexscreener, "fetch_token_pairs", unexpected_call)
    client = FakeClient({"poolA": 4.0})
    snapshot = await shadow._snapshot_with_fallback(client, "poolA", None, chain=CHAIN)
    assert snapshot.available is True
    assert snapshot.price_usd == 4.0


# --- record_signals: Robinhood-specific Stock Token (RWA) exclusion -------

@pytest.mark.asyncio
async def test_record_signals_excludes_known_stock_token(monkeypatch):
    """A pool whose base token is a registered Robinhood Chain Stock Token
    (tokenized equity, e.g. NVDA) must never be logged by this memecoin-
    calibrated shadow, even though its m15 change clears the threshold."""
    async def _is_nvda_stock_token(contract, chain):
        return contract == "nvda_token_address" and chain == CHAIN

    monkeypatch.setattr(shadow, "is_stock_token", _is_nvda_stock_token)
    logged = await shadow.record_signals(
        [_pool(pool_address="poolNVDA", token_address="nvda_token_address", m5=30.0)], chain=CHAIN,
    )
    assert logged == 0
    assert await _rows() == []


@pytest.mark.asyncio
async def test_record_signals_stock_token_filter_does_not_block_other_pools(monkeypatch):
    """The exclusion is per-pool, not a blanket stop -- a genuine memecoin
    pool in the same batch as an excluded Stock Token still gets logged."""
    async def _is_nvda_stock_token(contract, chain):
        return contract == "nvda_token_address"

    monkeypatch.setattr(shadow, "is_stock_token", _is_nvda_stock_token)
    pools = [
        _pool(pool_address="poolNVDA", token_address="nvda_token_address", m5=30.0),
        _pool(pool_address="poolPUMP", token_address="pump_token_address", m5=35.0),
    ]
    logged = await shadow.record_signals(pools, chain=CHAIN)
    assert logged == 1
    rows = await _rows()
    assert {r["pool_address"] for r in rows} == {"poolPUMP"}


@pytest.mark.asyncio
async def test_record_signals_stock_token_registry_failure_never_blocks_logging(monkeypatch):
    """Honest degradation, not a fabricated block: if the registry lookup
    itself raises (network error), the signal is still logged rather than
    silently dropped on an unrelated failure -- matches this module's
    best-effort contract for every other side call."""
    async def _raising_is_stock_token(contract, chain):
        raise RuntimeError("registry unavailable")

    monkeypatch.setattr(shadow, "is_stock_token", _raising_is_stock_token)
    logged = await shadow.record_signals([_pool(m5=30.0)], chain=CHAIN)
    assert logged == 1


# --- evaluate_open_signals -------------------------------------------------

async def _insert_open_row(
    *, pool_address="poolA", entry_price=1.0, minutes_ago=20.0, pool_age_minutes=None,
    realistic_entry_price=_SENTINEL_USE_ENTRY_PRICE,
):
    detected_at = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
    pool_created_at = (
        (datetime.now(timezone.utc) - timedelta(minutes=pool_age_minutes)).isoformat()
        if pool_age_minutes is not None else None
    )
    if realistic_entry_price is _SENTINEL_USE_ENTRY_PRICE:
        # Default: no simulated entry impact (matches entry_price exactly)
        # so pre-existing tests that never asked about realistic_* keep
        # getting a non-NULL realistic_final_multiplier out of the box --
        # pass an explicit value (or None) to test that column directly.
        realistic_entry_price = entry_price
    async with aiosqlite.connect(shadow._db_path()) as db:
        await db.execute(
            """
            INSERT INTO robinhood_pump_shadow_log
                (pool_address, chain, status, detected_at, entry_price, pool_created_at,
                 realistic_entry_price)
            VALUES (?, ?, 'open', ?, ?, ?, ?)
            """,
            (pool_address, CHAIN, detected_at, entry_price, pool_created_at, realistic_entry_price),
        )
        await db.commit()


@pytest.mark.asyncio
async def test_evaluate_measures_m15_once_aged_past_15_minutes():
    await _insert_open_row(pool_address="poolA", entry_price=1.0, minutes_ago=20.0)
    client = FakeClient({"poolA": 1.5})
    counts = await shadow.evaluate_open_signals(client, chain=CHAIN)
    assert counts["measured_m15"] == 1
    rows = await _rows()
    assert rows[0]["forward_price_m15"] == 1.5
    assert rows[0]["forward_pct_m15"] == pytest.approx(50.0)
    assert rows[0]["status"] == "open"  # only h2 closes the row


@pytest.mark.asyncio
async def test_evaluate_skips_pool_not_yet_old_enough():
    await _insert_open_row(minutes_ago=5.0)  # younger than the 15min horizon
    client = FakeClient({"poolA": 2.0})
    counts = await shadow.evaluate_open_signals(client, chain=CHAIN)
    assert counts == {"measured_m15": 0, "measured_h1": 0, "measured_h2": 0, "closed": 0}
    assert client.calls == []


@pytest.mark.asyncio
async def test_evaluate_closes_row_on_h2_checkpoint():
    await _insert_open_row(entry_price=1.0, minutes_ago=130.0)
    client = FakeClient({"poolA": 0.8})
    # First pass advances m15, second h1, third h2 -- exercise all three in sequence.
    await shadow.evaluate_open_signals(client, chain=CHAIN)
    await shadow.evaluate_open_signals(client, chain=CHAIN)
    counts = await shadow.evaluate_open_signals(client, chain=CHAIN)
    assert counts["closed"] == 1
    rows = await _rows()
    assert rows[0]["status"] == "closed"
    assert rows[0]["forward_pct_h2"] == pytest.approx(-20.0)
    assert rows[0]["closed_at"] is not None


@pytest.mark.asyncio
async def test_evaluate_never_fabricates_when_snapshot_unavailable():
    await _insert_open_row(minutes_ago=20.0)
    client = FakeClient({"poolA": None})  # unavailable
    counts = await shadow.evaluate_open_signals(client, chain=CHAIN)
    assert counts["measured_m15"] == 0
    rows = await _rows()
    assert rows[0]["forward_price_m15"] is None
    assert rows[0]["status"] == "open"  # left for a future retry, not force-closed


@pytest.mark.asyncio
async def test_evaluate_one_pool_failure_never_blocks_others():
    await _insert_open_row(pool_address="poolA", minutes_ago=20.0)
    await _insert_open_row(pool_address="poolB", minutes_ago=20.0)

    class RaisingThenWorkingClient(FakeClient):
        async def get_pool_snapshot(self, pool_address, *, network="robinhood"):
            if pool_address == "poolA":
                raise RuntimeError("boom")
            return await super().get_pool_snapshot(pool_address, network=network)

    client = RaisingThenWorkingClient({"poolB": 3.0})
    counts = await shadow.evaluate_open_signals(client, chain=CHAIN)
    assert counts["measured_m15"] == 1  # poolB still measured despite poolA's failure


@pytest.mark.asyncio
async def test_evaluate_never_raises_on_db_failure(monkeypatch):
    monkeypatch.setattr(shadow, "DB_PATH", "/nonexistent/dir/shadow.db")
    shadow._ensured_db_paths.clear()
    client = FakeClient({})
    counts = await shadow.evaluate_open_signals(client, chain=CHAIN)
    assert counts["measured_m15"] == 0


# --- run_cycle --------------------------------------------------------

class FakeGeckoClient(FakeClient):
    def __init__(self, pools, price_by_pool):
        super().__init__(price_by_pool)
        self._pools = pools
        self.trending_calls: list[tuple[str, str]] = []

    async def get_trending_pools(self, *, network="robinhood", duration="5m"):
        from aria_core.services.geckoterminal import TrendingPoolsResult

        self.trending_calls.append((network, duration))
        return TrendingPoolsResult(pools=self._pools, available=True, error=None)


@pytest.mark.asyncio
async def test_run_cycle_fetches_logs_and_measures():
    client = FakeGeckoClient([_pool(m5=30.0)], {})
    result = await shadow.run_cycle(client, network=CHAIN)
    assert result["fetched_pools"] == 1
    assert result["signals_logged"] == 1
    assert client.trending_calls == [("robinhood", "5m")]


@pytest.mark.asyncio
async def test_run_cycle_handles_unavailable_trending_pools():
    from aria_core.services.geckoterminal import TrendingPoolsResult

    class UnavailableClient(FakeClient):
        async def get_trending_pools(self, *, network="robinhood", duration="5m"):
            return TrendingPoolsResult(available=False, error="rate limit")

    client = UnavailableClient({})
    result = await shadow.run_cycle(client, network=CHAIN)
    assert result["fetched_pools"] == 0
    assert result["signals_logged"] == 0


# --- summary ------------------------------------------------------------

@pytest.mark.asyncio
async def test_summary_computes_win_rate_only_over_closed_rows():
    async with aiosqlite.connect(shadow._db_path()) as db:
        await db.execute(
            "INSERT INTO robinhood_pump_shadow_log (pool_address, chain, status, detected_at, entry_price, forward_pct_h2) "
            "VALUES (?, ?, 'closed', ?, 1.0, 25.0)",
            ("poolA", CHAIN, datetime.now(timezone.utc).isoformat()),
        )
        await db.execute(
            "INSERT INTO robinhood_pump_shadow_log (pool_address, chain, status, detected_at, entry_price, forward_pct_h2) "
            "VALUES (?, ?, 'closed', ?, 1.0, -10.0)",
            ("poolB", CHAIN, datetime.now(timezone.utc).isoformat()),
        )
        await db.execute(
            "INSERT INTO robinhood_pump_shadow_log (pool_address, chain, status, detected_at, entry_price) "
            "VALUES (?, ?, 'open', ?, 1.0)",
            ("poolC", CHAIN, datetime.now(timezone.utc).isoformat()),
        )
        await db.commit()

    result = await shadow.summary(CHAIN)
    assert result["open"] == 1
    assert result["closed"] == 2
    assert result["wins_h2"] == 1
    assert result["win_rate_h2"] == pytest.approx(0.5)
    assert result["avg_multiplier_h2"] == pytest.approx((1.25 + 0.90) / 2)


@pytest.mark.asyncio
async def test_summary_no_closed_rows_is_none_not_zero():
    result = await shadow.summary(CHAIN)
    assert result["closed"] == 0
    assert result["win_rate_h2"] is None


# --- advance_exit_simulation --------------------------------------------

@pytest.mark.asyncio
async def test_advance_exit_multiple_rungs_filled_in_one_slow_cycle():
    await _insert_open_row(pool_address="poolA", entry_price=1.0, minutes_ago=10.0)
    client = FakeClient({"poolA": 2.0})  # a slow cycle: price jumped straight past 3 rungs
    counts = await shadow.advance_exit_simulation(client, chain=CHAIN)
    assert counts["scale_out_fills"] == 3
    assert client.calls == ["poolA"]  # exactly one snapshot call for this position
    rows = await _rows()
    assert rows[0]["remaining_qty"] == pytest.approx(0.421875)
    assert rows[0]["realized_proceeds"] == pytest.approx(0.880126953125)
    assert rows[0]["next_scale_level"] == pytest.approx(2.44140625)
    assert rows[0]["peak_price"] == pytest.approx(2.0)
    assert rows[0]["exit_reason"] is None  # still open, dust threshold not reached
    assert rows[0]["final_multiplier"] is None


@pytest.mark.asyncio
async def test_advance_exit_scale_out_dust_closes_position():
    await _insert_open_row(pool_address="poolA", entry_price=1.0, minutes_ago=10.0)
    client = FakeClient({"poolA": 1000.0})  # far past enough rungs to leave <1% remaining
    counts = await shadow.advance_exit_simulation(client, chain=CHAIN)
    assert counts["closed_scale_out_complete"] == 1
    rows = await _rows()
    assert rows[0]["exit_reason"] == "scale_out_complete"
    assert rows[0]["remaining_qty"] == 0.0
    assert rows[0]["final_multiplier"] is not None
    assert rows[0]["final_multiplier"] > 1.0  # a 1000x spot price is a clear win


@pytest.mark.asyncio
async def test_advance_exit_trailing_stop_triggers():
    await _insert_open_row(pool_address="poolA", entry_price=1.0, minutes_ago=10.0)
    # Cycle 1: price rises past the first rung, sets a new peak at 1.3.
    await shadow.advance_exit_simulation(FakeClient({"poolA": 1.3}), chain=CHAIN)
    rows = await _rows()
    assert rows[0]["exit_reason"] is None
    assert rows[0]["peak_price"] == pytest.approx(1.3)
    # Cycle 2: price falls to exactly -20% of the 1.3 peak -> trailing stop.
    counts = await shadow.advance_exit_simulation(FakeClient({"poolA": 1.04}), chain=CHAIN)
    assert counts["closed_trailing_stop"] == 1
    rows = await _rows()
    assert rows[0]["exit_reason"] == "trailing_stop"
    assert rows[0]["remaining_qty"] == 0.0
    assert rows[0]["final_multiplier"] == pytest.approx(1.0925)


@pytest.mark.asyncio
async def test_advance_exit_max_hold_triggers():
    await _insert_open_row(pool_address="poolA", entry_price=1.0, minutes_ago=125.0)
    client = FakeClient({"poolA": 1.1})  # no rung crossed, no stop hit -- only the timeout fires
    counts = await shadow.advance_exit_simulation(client, chain=CHAIN)
    assert counts["closed_max_hold"] == 1
    rows = await _rows()
    assert rows[0]["exit_reason"] == "max_hold"
    assert rows[0]["remaining_qty"] == 0.0
    assert rows[0]["final_multiplier"] == pytest.approx(1.1)


@pytest.mark.asyncio
async def test_advance_exit_stays_open_when_nothing_triggers():
    await _insert_open_row(pool_address="poolA", entry_price=1.0, minutes_ago=10.0)
    client = FakeClient({"poolA": 1.1})  # below the first rung, above the stop, well under 2h
    counts = await shadow.advance_exit_simulation(client, chain=CHAIN)
    assert counts["checked"] == 1
    assert counts["scale_out_fills"] == 0
    rows = await _rows()
    assert rows[0]["exit_reason"] is None
    assert rows[0]["remaining_qty"] == 1.0
    assert rows[0]["peak_price"] == pytest.approx(1.1)  # still updated even with no fill/exit


@pytest.mark.asyncio
async def test_advance_exit_never_fabricates_when_snapshot_unavailable():
    await _insert_open_row(pool_address="poolA", entry_price=1.0, minutes_ago=10.0)
    client = FakeClient({"poolA": None})  # unavailable
    counts = await shadow.advance_exit_simulation(client, chain=CHAIN)
    assert counts["checked"] == 0
    rows = await _rows()
    assert rows[0]["exit_reason"] is None
    assert rows[0]["remaining_qty"] == 1.0  # untouched, left for the next passage to retry


@pytest.mark.asyncio
async def test_advance_exit_age_limit_force_closes_a_losing_position():
    await _insert_open_row(pool_address="poolA", entry_price=1.0, minutes_ago=10.0, pool_age_minutes=30.0)
    client = FakeClient({"poolA": 0.95})  # below entry -- losing, no rung/stop/max-hold triggered on its own
    counts = await shadow.advance_exit_simulation(client, chain=CHAIN)
    assert counts["closed_age_limit"] == 1
    rows = await _rows()
    assert rows[0]["exit_reason"] == "age_limit"
    assert rows[0]["remaining_qty"] == 0.0
    assert rows[0]["final_multiplier"] == pytest.approx(0.95)


@pytest.mark.asyncio
async def test_advance_exit_age_limit_never_force_closes_a_winning_position():
    await _insert_open_row(pool_address="poolA", entry_price=1.0, minutes_ago=10.0, pool_age_minutes=30.0)
    client = FakeClient({"poolA": 1.1})  # above entry -- winning, kept open despite the age
    counts = await shadow.advance_exit_simulation(client, chain=CHAIN)
    assert counts["closed_age_limit"] == 0
    rows = await _rows()
    assert rows[0]["exit_reason"] is None
    assert rows[0]["remaining_qty"] == 1.0


@pytest.mark.asyncio
async def test_advance_exit_age_limit_ignored_when_age_unknown():
    await _insert_open_row(pool_address="poolA", entry_price=1.0, minutes_ago=10.0)  # pool_age_minutes=None
    client = FakeClient({"poolA": 0.95})  # losing, but age is unknown -- never force-closed on that basis alone
    counts = await shadow.advance_exit_simulation(client, chain=CHAIN)
    assert counts["closed_age_limit"] == 0
    rows = await _rows()
    assert rows[0]["exit_reason"] is None


@pytest.mark.asyncio
async def test_advance_exit_one_pool_failure_never_blocks_others():
    await _insert_open_row(pool_address="poolA", entry_price=1.0, minutes_ago=10.0)
    await _insert_open_row(pool_address="poolB", entry_price=1.0, minutes_ago=10.0)

    class RaisingThenWorkingClient(FakeClient):
        async def get_pool_snapshot(self, pool_address, *, network="robinhood"):
            if pool_address == "poolA":
                raise RuntimeError("boom")
            return await super().get_pool_snapshot(pool_address, network=network)

    client = RaisingThenWorkingClient({"poolB": 1.1})
    counts = await shadow.advance_exit_simulation(client, chain=CHAIN)
    assert counts["checked"] == 1  # poolB still processed despite poolA's failure


@pytest.mark.asyncio
async def test_advance_exit_never_raises_on_db_failure(monkeypatch):
    monkeypatch.setattr(shadow, "DB_PATH", "/nonexistent/dir/shadow.db")
    shadow._ensured_db_paths.clear()
    client = FakeClient({})
    counts = await shadow.advance_exit_simulation(client, chain=CHAIN)
    assert counts["checked"] == 0


def _candle(ts: float, *, open_: float, high: float, low: float, close: float) -> Candle:
    return Candle(ts=int(ts), open=open_, high=high, low=low, close=close, volume=0.0)


# --- advance_exit_simulation: 16/08 OHLCV-window fix (real bug repro) ----

@pytest.mark.asyncio
async def test_advance_exit_window_low_catches_stop_missed_by_point_sample():
    """Reproduces the real live bug: a peak only +16% above entry, then a
    crash whose true low (0.02) is only visible inside a closed 15min
    candle -- the point-sample spot price polled afterward (0.03) is even
    worse than the stop threshold. The OLD code would have closed at
    whatever the spot happened to be (~0.03, a ~97% loss); the fix must
    close at the STOP'S OWN threshold price (peak*0.8 = 0.928), the
    calibrated -20% floor, not the crash extreme."""
    await _insert_open_row(pool_address="poolA", entry_price=1.0, minutes_ago=10.0)

    # Cycle 1: price rises to +16% -- sets the peak, no rung reached (first
    # rung is +25%), no stop breached. OHLCV unavailable this cycle (not
    # needed to establish the peak) -- pure point-sample fallback.
    client1 = FakeClient({"poolA": 1.16})
    await shadow.advance_exit_simulation(client1, chain=CHAIN)
    rows = await _rows()
    assert rows[0]["peak_price"] == pytest.approx(1.16)
    assert rows[0]["exit_reason"] is None
    last_checked_epoch = shadow._epoch_of(rows[0]["last_checked_at"])
    assert last_checked_epoch is not None

    # Cycle 2: two candles closed since last_checked_at reconstruct the real
    # crash (low 0.02) the point-sample spot (0.03) never directly touched
    # itself but landed well past.
    candles = [
        _candle(last_checked_epoch + 60, open_=1.16, high=1.16, low=0.02, close=0.05),
        _candle(last_checked_epoch + 120, open_=0.05, high=0.06, low=0.03, close=0.03),
    ]
    client2 = FakeClient(
        {"poolA": 0.03}, {"poolA": OHLCVResult(candles=candles, available=True, error=None)},
    )
    counts = await shadow.advance_exit_simulation(client2, chain=CHAIN)
    assert counts["closed_trailing_stop"] == 1
    assert client2.ohlcv_calls == ["poolA"]  # exactly one get_ohlcv call for this position (5min mode, 17/08)

    rows = await _rows()
    stop_price = 1.16 * (1 - shadow.TRAILING_STOP_PCT / 100.0)
    assert rows[0]["exit_reason"] == "trailing_stop"
    assert rows[0]["final_multiplier"] == pytest.approx(stop_price)  # ~0.928
    assert rows[0]["final_multiplier"] > 0.9  # nowhere near the 0.02-0.03 crash extreme
    assert rows[0]["remaining_qty"] == 0.0


@pytest.mark.asyncio
async def test_advance_exit_window_high_catches_rung_retraced_before_poll():
    """Symmetric case: a scale-out rung is reached and retraced INSIDE the
    candle window, invisible to a point-sample spot price polled after the
    retracement -- the window high must still register the fill."""
    await _insert_open_row(pool_address="poolA", entry_price=1.0, minutes_ago=10.0)
    detected_epoch = shadow._epoch_of((await _rows())[0]["detected_at"])

    # First rung is at 1.25 (entry * 1.25). A single closed candle spikes to
    # 1.30 (crossing it) then retraces to 1.05 by its close -- low kept at
    # 1.05 (above the NEW peak's -20% stop, 1.30*0.8=1.04) so only the
    # scale-out fill is exercised in isolation, not the trailing stop too.
    # The spot price polled now (1.05) never itself reaches the rung.
    candles = [_candle(detected_epoch + 60, open_=1.0, high=1.30, low=1.05, close=1.05)]
    client = FakeClient(
        {"poolA": 1.05}, {"poolA": OHLCVResult(candles=candles, available=True, error=None)},
    )
    counts = await shadow.advance_exit_simulation(client, chain=CHAIN)
    assert counts["scale_out_fills"] == 1
    rows = await _rows()
    assert rows[0]["peak_price"] == pytest.approx(1.30)  # window high, not the spot 1.05
    assert rows[0]["remaining_qty"] == pytest.approx(0.75)
    assert rows[0]["realized_proceeds"] == pytest.approx(0.25 * 1.25)


@pytest.mark.asyncio
async def test_advance_exit_falls_back_to_point_sample_when_ohlcv_unavailable():
    """Explicit fallback contract: get_ohlcv returning available=False must
    reproduce the OLD pure-spot-price math exactly, never block the row."""
    await _insert_open_row(pool_address="poolA", entry_price=1.0, minutes_ago=10.0)
    unavailable = OHLCVResult(candles=[], available=False, error="unavailable")

    client1 = FakeClient({"poolA": 1.3}, {"poolA": unavailable})
    await shadow.advance_exit_simulation(client1, chain=CHAIN)
    rows = await _rows()
    assert rows[0]["peak_price"] == pytest.approx(1.3)  # collapsed to the spot alone

    client2 = FakeClient({"poolA": 1.04}, {"poolA": unavailable})
    counts = await shadow.advance_exit_simulation(client2, chain=CHAIN)
    assert counts["closed_trailing_stop"] == 1
    rows = await _rows()
    # Same figure the pure point-sample math would have produced (peak 1.3,
    # stop exactly at the polled spot 1.04) -- the fallback changes nothing
    # observable versus the pre-16/08 behavior.
    assert rows[0]["final_multiplier"] == pytest.approx(1.0925)


@pytest.mark.asyncio
async def test_advance_exit_ohlcv_exception_falls_back_and_never_raises():
    """get_ohlcv raising must never break the row -- same best-effort
    doctrine already applied to get_pool_snapshot failures."""
    await _insert_open_row(pool_address="poolA", entry_price=1.0, minutes_ago=10.0)

    class RaisingOhlcvClient(FakeClient):
        async def get_ohlcv(self, pool_address, *, network="robinhood", mode="standard", **_kwargs):
            raise RuntimeError("boom")

    client = RaisingOhlcvClient({"poolA": 1.1})
    counts = await shadow.advance_exit_simulation(client, chain=CHAIN)
    assert counts["checked"] == 1
    rows = await _rows()
    assert rows[0]["peak_price"] == pytest.approx(1.1)  # fell back to the spot price alone
    assert rows[0]["exit_reason"] is None


# --- exit_simulation_summary ---------------------------------------------

@pytest.mark.asyncio
async def test_exit_simulation_summary_computes_over_completed_rows_only():
    async with aiosqlite.connect(shadow._db_path()) as db:
        await db.execute(
            "INSERT INTO robinhood_pump_shadow_log "
            "(pool_address, chain, status, detected_at, entry_price, exit_reason, final_multiplier) "
            "VALUES (?, ?, 'open', ?, 1.0, 'trailing_stop', 1.5)",
            ("poolA", CHAIN, datetime.now(timezone.utc).isoformat()),
        )
        await db.execute(
            "INSERT INTO robinhood_pump_shadow_log "
            "(pool_address, chain, status, detected_at, entry_price, exit_reason, final_multiplier) "
            "VALUES (?, ?, 'open', ?, 1.0, 'max_hold', 0.7)",
            ("poolB", CHAIN, datetime.now(timezone.utc).isoformat()),
        )
        await db.execute(
            "INSERT INTO robinhood_pump_shadow_log (pool_address, chain, status, detected_at, entry_price) "
            "VALUES (?, ?, 'open', ?, 1.0)",
            ("poolC", CHAIN, datetime.now(timezone.utc).isoformat()),
        )
        await db.commit()

    result = await shadow.exit_simulation_summary(CHAIN)
    assert result["completed"] == 2
    assert result["wins"] == 1
    assert result["win_rate"] == pytest.approx(0.5)
    assert result["avg_multiplier"] == pytest.approx((1.5 + 0.7) / 2)
    assert result["by_exit_reason"] == {"trailing_stop": 1, "max_hold": 1}


@pytest.mark.asyncio
async def test_exit_simulation_summary_no_completed_rows_is_none_not_zero():
    result = await shadow.exit_simulation_summary(CHAIN)
    assert result["completed"] == 0
    assert result["win_rate"] is None
    assert result["avg_multiplier"] is None


# --- run_cycle wires both measurement passes ------------------------------

@pytest.mark.asyncio
async def test_run_cycle_also_advances_exit_simulation():
    client = FakeGeckoClient([_pool(m5=30.0)], {})
    result = await shadow.run_cycle(client, network=CHAIN)
    assert "exit_sim" in result
    assert result["exit_sim"]["checked"] == 0  # poolA has no price in FakeGeckoClient's map -> unavailable, skipped


# --- chain_pnl_summary (17/08, Telegram notification cumulative PnL) -----

@pytest.mark.asyncio
async def test_chain_pnl_summary_sums_closed_rows_final_multiplier():
    async with aiosqlite.connect(shadow._db_path()) as db:
        await db.execute(
            "INSERT INTO robinhood_pump_shadow_log "
            "(pool_address, chain, status, detected_at, entry_price, exit_reason, final_multiplier) "
            "VALUES (?, ?, 'closed', ?, 1.0, 'trailing_stop', 1.5)",
            ("poolA", CHAIN, datetime.now(timezone.utc).isoformat()),
        )
        await db.execute(
            "INSERT INTO robinhood_pump_shadow_log "
            "(pool_address, chain, status, detected_at, entry_price, exit_reason, final_multiplier) "
            "VALUES (?, ?, 'closed', ?, 1.0, 'max_hold', 0.8)",
            ("poolB", CHAIN, datetime.now(timezone.utc).isoformat()),
        )
        await db.commit()

    result = await shadow.chain_pnl_summary(CHAIN)
    assert result["closed"] == 2
    assert result["total_pnl_units"] == pytest.approx((1.5 - 1.0) + (0.8 - 1.0))


@pytest.mark.asyncio
async def test_chain_pnl_summary_includes_open_row_with_known_last_price():
    async with aiosqlite.connect(shadow._db_path()) as db:
        await db.execute(
            "INSERT INTO robinhood_pump_shadow_log "
            "(pool_address, chain, status, detected_at, entry_price, remaining_qty, realized_proceeds, last_price) "
            "VALUES (?, ?, 'open', ?, 1.0, 0.5, 0.6, 2.0)",
            ("poolA", CHAIN, datetime.now(timezone.utc).isoformat()),
        )
        await db.commit()

    result = await shadow.chain_pnl_summary(CHAIN)
    assert result["open_valued"] == 1
    assert result["pending_price"] == 0
    assert result["total_pnl_units"] == pytest.approx((0.6 + 0.5 * 2.0) / 1.0 - 1.0)


@pytest.mark.asyncio
async def test_chain_pnl_summary_open_row_without_last_price_is_pending_not_counted():
    async with aiosqlite.connect(shadow._db_path()) as db:
        await db.execute(
            "INSERT INTO robinhood_pump_shadow_log (pool_address, chain, status, detected_at, entry_price) "
            "VALUES (?, ?, 'open', ?, 1.0)",
            ("poolA", CHAIN, datetime.now(timezone.utc).isoformat()),
        )
        await db.commit()

    result = await shadow.chain_pnl_summary(CHAIN)
    assert result["pending_price"] == 1
    assert result["open_valued"] == 0
    assert result["total_pnl_units"] == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_chain_pnl_summary_empty_table_is_zero_not_error():
    result = await shadow.chain_pnl_summary(CHAIN)
    assert result["total_pnl_units"] == pytest.approx(0.0)
    assert result["closed"] == 0
    assert result["open_valued"] == 0
    assert result["pending_price"] == 0


# --- realistic execution simulation (17/08, price impact + fees) --------

def test_apply_price_impact_and_fee_none_when_pool_too_shallow():
    # Reproduces the real X17690 case: reserve_usd essentially zero.
    result = shadow._apply_price_impact_and_fee(
        1.0994857292802e-05, trade_size_usd=shadow.SIMULATED_TRADE_SIZE_USD,
        reserve_usd=2.11169582131643e-07, side="buy",
    )
    assert result is None


def test_apply_price_impact_and_fee_none_when_reserve_missing():
    assert shadow._apply_price_impact_and_fee(
        1.0, trade_size_usd=20.0, reserve_usd=None, side="sell",
    ) is None
    assert shadow._apply_price_impact_and_fee(
        1.0, trade_size_usd=20.0, reserve_usd=0.0, side="sell",
    ) is None


def test_apply_price_impact_and_fee_buy_raises_price_sell_lowers_it():
    buy_price = shadow._apply_price_impact_and_fee(
        1.0, trade_size_usd=20.0, reserve_usd=6000.0, side="buy",
    )
    sell_price = shadow._apply_price_impact_and_fee(
        1.0, trade_size_usd=20.0, reserve_usd=6000.0, side="sell",
    )
    assert buy_price > 1.0  # pay more than spot
    assert sell_price < 1.0  # receive less than spot


@pytest.mark.asyncio
async def test_record_signals_stores_realistic_entry_price_on_a_deep_pool():
    pool = _pool(reserve=100000.0, price_usd=1.0)
    await shadow.record_signals([pool], chain=CHAIN)
    rows = await _rows()
    assert rows[0]["realistic_entry_price"] is not None
    assert rows[0]["realistic_entry_price"] > rows[0]["entry_price"]  # buy impact raises the paid price


@pytest.mark.asyncio
async def test_record_signals_realistic_entry_price_null_on_a_dust_pool():
    pool = _pool(reserve=0.0000002, price_usd=1.0994857292802e-05)
    await shadow.record_signals([pool], chain=CHAIN)
    rows = await _rows()
    assert rows[0]["realistic_entry_price"] is None


@pytest.mark.asyncio
async def test_advance_exit_realistic_multiplier_lower_than_ideal_on_a_normal_pool():
    # FakeClient's snapshot carries reserve_usd=1000.0 -- deep enough to
    # absorb SIMULATED_TRADE_SIZE_USD without going unreachable.
    await _insert_open_row(pool_address="poolA", entry_price=1.0, minutes_ago=10.0)
    await shadow.advance_exit_simulation(FakeClient({"poolA": 1.3}), chain=CHAIN)
    await shadow.advance_exit_simulation(FakeClient({"poolA": 1.04}), chain=CHAIN)
    rows = await _rows()
    assert rows[0]["exit_reason"] == "trailing_stop"
    assert rows[0]["realistic_final_multiplier"] is not None
    # Impact + fee always eat into proceeds relative to the zero-friction ideal.
    assert rows[0]["realistic_final_multiplier"] < rows[0]["final_multiplier"]


@pytest.mark.asyncio
async def test_advance_exit_realistic_multiplier_null_when_entry_already_unreachable():
    # realistic_entry_price=None at insert -- the simulated buy itself was
    # already too shallow to execute, so the whole realistic column stays
    # NULL through to close, even though the ideal final_multiplier resolves.
    await _insert_open_row(
        pool_address="poolA", entry_price=1.0, minutes_ago=10.0, realistic_entry_price=None,
    )
    await shadow.advance_exit_simulation(FakeClient({"poolA": 1.3}), chain=CHAIN)
    counts = await shadow.advance_exit_simulation(FakeClient({"poolA": 1.04}), chain=CHAIN)
    assert counts["closed_trailing_stop"] == 1
    rows = await _rows()
    assert rows[0]["final_multiplier"] is not None
    assert rows[0]["realistic_final_multiplier"] is None


@pytest.mark.asyncio
async def test_advance_exit_realistic_multiplier_stays_open_row_null_until_close():
    await _insert_open_row(pool_address="poolA", entry_price=1.0, minutes_ago=10.0)
    await shadow.advance_exit_simulation(FakeClient({"poolA": 1.1}), chain=CHAIN)
    rows = await _rows()
    assert rows[0]["exit_reason"] is None
    assert rows[0]["realistic_final_multiplier"] is None


@pytest.mark.asyncio
async def test_chain_pnl_summary_realistic_sums_closed_rows():
    async with aiosqlite.connect(shadow._db_path()) as db:
        await db.execute(
            "INSERT INTO robinhood_pump_shadow_log "
            "(pool_address, chain, status, detected_at, entry_price, realistic_entry_price, "
            "exit_reason, final_multiplier, realistic_final_multiplier) "
            "VALUES (?, ?, 'closed', ?, 1.0, 1.02, 'trailing_stop', 1.5, 1.4)",
            ("poolA", CHAIN, datetime.now(timezone.utc).isoformat()),
        )
        await db.commit()

    result = await shadow.chain_pnl_summary_realistic(CHAIN)
    assert result["closed"] == 1
    assert result["total_pnl_units"] == pytest.approx(0.4)
    assert result["unreachable_liquidity"] == 0


@pytest.mark.asyncio
async def test_chain_pnl_summary_realistic_excludes_unreachable_entry():
    async with aiosqlite.connect(shadow._db_path()) as db:
        await db.execute(
            "INSERT INTO robinhood_pump_shadow_log "
            "(pool_address, chain, status, detected_at, entry_price, realistic_entry_price, "
            "exit_reason, final_multiplier) "
            "VALUES (?, ?, 'closed', ?, 1.0, NULL, 'trailing_stop', 12.5)",
            ("dustpool", CHAIN, datetime.now(timezone.utc).isoformat()),
        )
        await db.commit()

    result = await shadow.chain_pnl_summary_realistic(CHAIN)
    assert result["closed"] == 0
    assert result["unreachable_liquidity"] == 1
    assert result["total_pnl_units"] == pytest.approx(0.0)
    ideal = await shadow.chain_pnl_summary(CHAIN)
    assert ideal["total_pnl_units"] == pytest.approx(11.5)


@pytest.mark.asyncio
async def test_chain_pnl_summary_realistic_excludes_row_that_turned_unreachable_mid_exit():
    async with aiosqlite.connect(shadow._db_path()) as db:
        await db.execute(
            "INSERT INTO robinhood_pump_shadow_log "
            "(pool_address, chain, status, detected_at, entry_price, realistic_entry_price, "
            "exit_reason, final_multiplier, realistic_final_multiplier) "
            "VALUES (?, ?, 'closed', ?, 1.0, 1.02, 'max_hold', 1.1, NULL)",
            ("poolB", CHAIN, datetime.now(timezone.utc).isoformat()),
        )
        await db.commit()

    result = await shadow.chain_pnl_summary_realistic(CHAIN)
    assert result["closed"] == 0
    assert result["unreachable_liquidity"] == 1
    assert result["total_pnl_units"] == pytest.approx(0.0)
