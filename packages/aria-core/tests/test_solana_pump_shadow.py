"""Solana "take the train" shadow (16/08) -- pure read+log, never a trigger.
Mirrors v8_rsi_reversal_shadow's isolated-tmp-db + detect/measure state-
machine test pattern; the GeckoTerminal client is always injected/mocked,
never a real network call (same doctrine as test_geckoterminal_client.py)."""
from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import aiosqlite
import pytest

from aria_core import solana_pump_shadow as shadow
from aria_core.services.dexscreener import PairSnapshot
from aria_core.services.geckoterminal import OHLCVResult, PoolSnapshot, TrendingPool
from aria_core.services.rugcheck import RugCheckReport
from aria_core.skills.ta_levels import Candle

CHAIN = "solana"
_SENTINEL_USE_ENTRY_PRICE = object()


@pytest.fixture(autouse=True)
async def _tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(shadow, "DB_PATH", str(tmp_path / "shadow.db"))
    shadow._ensured_db_paths.clear()
    await shadow._ensure_table()
    # 16/08 -- RugCheck enrichment is shadow-only/best-effort (see
    # services/rugcheck.py), but a real network call has no place in a unit
    # test (same doctrine that closed the real pytest-hang bug this
    # session). Default: unavailable, matching the "best-effort, never
    # blocking" contract -- individual tests override this when they need to
    # assert on a specific rugcheck_* column.
    monkeypatch.setattr(
        shadow.rugcheck, "get_token_report",
        AsyncMock(return_value=RugCheckReport(available=False, error="test default")),
    )
    yield
    shadow._ensured_db_paths.clear()


async def _rows():
    async with aiosqlite.connect(shadow._db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM solana_pump_shadow_log")
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
        # 17/08 -- the age filter became a WINDOW (MIN..MAX), so a pool
        # created "now" is no longer valid by default: it sits below the
        # minimum. Default to the middle of the window so every test that
        # does not care about age keeps exercising a funded signal.
        pool_created_at=pool_created_at if pool_created_at is not None else (
            datetime.now(timezone.utc)
            - timedelta(minutes=(shadow.MIN_POOL_AGE_MINUTES + shadow.MAX_POOL_AGE_MINUTES) / 2)
        ),
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

    async def get_pool_snapshot(self, pool_address, *, network="solana"):
        self.calls.append(pool_address)
        price = self._prices.get(pool_address)
        if price is None:
            return PoolSnapshot(pool_address=pool_address, available=False, error="unavailable")
        return PoolSnapshot(pool_address=pool_address, price_usd=price, reserve_usd=1000.0, available=True)

    async def get_ohlcv(self, pool_address, *, network="solana", mode="standard", **_kwargs):
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
async def test_record_signals_stores_rugcheck_enrichment(monkeypatch):
    from aria_core.services.rugcheck import RugCheckReport

    async def _fake_report(mint):
        assert mint == "tokA"
        return RugCheckReport(
            score_normalised=46, risks=["Low Liquidity", "High holder concentration"],
            top_holder_pct=32.9, creator="DevWallet111", available=True, error=None,
        )

    monkeypatch.setattr(shadow.rugcheck, "get_token_report", _fake_report)
    logged = await shadow.record_signals([_pool(m5=40.0)], chain=CHAIN)
    assert logged == 1
    rows = await _rows()
    assert rows[0]["rugcheck_score"] == 46
    assert rows[0]["rugcheck_risks"] == "Low Liquidity,High holder concentration"
    assert rows[0]["rugcheck_top_holder_pct"] == pytest.approx(32.9)
    assert rows[0]["rugcheck_creator"] == "DevWallet111"


@pytest.mark.asyncio
async def test_record_signals_rugcheck_failure_never_blocks_logging(monkeypatch):
    async def _raising_report(mint):
        raise RuntimeError("network down")

    monkeypatch.setattr(shadow.rugcheck, "get_token_report", _raising_report)
    logged = await shadow.record_signals([_pool(m5=40.0)], chain=CHAIN)
    assert logged == 1  # RugCheck is shadow-only enrichment, never a hard requirement
    rows = await _rows()
    assert rows[0]["rugcheck_score"] is None


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
        await db.execute("UPDATE solana_pump_shadow_log SET status = 'closed'")
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


# --- _snapshot_with_fallback (16/08 API cascade, 27/08 ws_feed tried first) -

@dataclasses.dataclass
class _FakeWsSnapshot:
    available: bool
    price_usd: float | None = None
    reserve_usd: float | None = None
    dex_id: str | None = "pumpswap"


class _FakeWsFeed:
    def __init__(self, snapshots: dict) -> None:
        self._snapshots = snapshots

    def get_snapshot(self, pool_address: str) -> _FakeWsSnapshot:
        return self._snapshots.get(pool_address, _FakeWsSnapshot(available=False))


@pytest.mark.asyncio
async def test_snapshot_fallback_uses_ws_feed_first_when_available(monkeypatch):
    """27/08 -- operator-directed migration off GeckoTerminal onto Chainstack
    WS for real-time price, same doctrine already proven on Base/Robinhood
    and on solana_late_bonding_shadow's own bonding_ws_feed. A tracked pool's
    live push must win over DexScreener too, not just over GeckoTerminal."""
    ws_feed = _FakeWsFeed({"poolA": _FakeWsSnapshot(available=True, price_usd=4.2, reserve_usd=9000.0)})

    async def _boom(contract, *, chain="solana"):
        raise AssertionError("dexscreener must not be called when the ws_feed already answered")

    monkeypatch.setattr(shadow.dexscreener, "fetch_token_pairs", _boom)
    client = FakeClient({"poolA": 99.0})
    snapshot = await shadow._snapshot_with_fallback(client, "poolA", "tokA", chain=CHAIN, ws_feed=ws_feed)
    assert snapshot.available is True
    assert snapshot.price_usd == 4.2
    assert snapshot.reserve_usd == 9000.0
    assert client.calls == []


@pytest.mark.asyncio
async def test_snapshot_fallback_falls_through_to_dexscreener_when_ws_feed_unavailable(monkeypatch):
    """A ws_feed that doesn't yet track this pool (or hasn't ticked) must
    never block the existing REST cascade -- same as ws_feed=None."""
    ws_feed = _FakeWsFeed({})  # empty: every lookup returns available=False

    async def fake_fetch_token_pairs(contract, *, chain="solana"):
        return [PairSnapshot(base_address=contract, price_usd=3.5, liquidity_usd=10000.0)]

    monkeypatch.setattr(shadow.dexscreener, "fetch_token_pairs", fake_fetch_token_pairs)
    client = FakeClient({"poolA": 99.0})
    snapshot = await shadow._snapshot_with_fallback(client, "poolA", "tokA", chain=CHAIN, ws_feed=ws_feed)
    assert snapshot.available is True
    assert snapshot.price_usd == 3.5  # DexScreener, the existing cascade unchanged


@pytest.mark.asyncio
async def test_snapshot_fallback_uses_dexscreener_when_available(monkeypatch):
    async def fake_fetch_token_pairs(contract, *, chain="solana"):
        return [PairSnapshot(base_address=contract, price_usd=3.5, liquidity_usd=10000.0)]

    monkeypatch.setattr(shadow.dexscreener, "fetch_token_pairs", fake_fetch_token_pairs)
    client = FakeClient({"poolA": 99.0})  # would prove wrong if GeckoTerminal got used instead
    snapshot = await shadow._snapshot_with_fallback(client, "poolA", "tokA", chain=CHAIN)
    assert snapshot.available is True
    assert snapshot.price_usd == 3.5
    assert snapshot.reserve_usd == 10000.0
    assert client.calls == []  # GeckoTerminal never called -- DexScreener answered first


@pytest.mark.asyncio
async def test_snapshot_fallback_unknown_liquidity_backfilled_from_geckoterminal(monkeypatch):
    """17/08, real bug: DexScreener's liquidity_unknown=True (pump.fun
    bonding-curve pools and freshly-indexed pairs report no traditional
    reserve) was silently taken as liquidity_usd's default 0.0, which
    advance_exit_simulation's liquidity_collapse check then read as
    "genuinely drained" -- confirmed live (TESTIBULL: entry reserve 5534$,
    closed liquidity_collapse 39s later while price had barely moved -0.9%).

    18/08, follow-up real bug: leaving it at a bare None fixed THAT false
    positive, but introduced a new one downstream -- `_apply_price_impact_
    and_fee` treats `reserve_usd is None` identically to a genuine 0, so an
    actively-traded pool (Krackpot, $123K/24h volume) got marked unsellable/
    stranded in the realistic PnL purely because DexScreener didn't report a
    number. GeckoTerminal had a real reserve figure for it the same night.
    Now backfills from GeckoTerminal specifically when DexScreener came back
    unknown -- DexScreener's own (fresher) price is never overwritten,
    only the reserve figure is."""
    async def fake_fetch_token_pairs(contract, *, chain="solana"):
        return [PairSnapshot(base_address=contract, price_usd=3.5, liquidity_usd=0.0, liquidity_unknown=True)]

    monkeypatch.setattr(shadow.dexscreener, "fetch_token_pairs", fake_fetch_token_pairs)
    client = FakeClient({"poolA": 99.0})  # FakeClient.get_pool_snapshot reports reserve_usd=1000.0
    snapshot = await shadow._snapshot_with_fallback(client, "poolA", "tokA", chain=CHAIN)
    assert snapshot.available is True
    assert snapshot.price_usd == 3.5  # DexScreener's price, never overwritten by the backfill call
    assert snapshot.reserve_usd == 1000.0  # backfilled from GeckoTerminal, not left None
    assert client.calls == ["poolA"]  # the backfill call really happened


@pytest.mark.asyncio
async def test_snapshot_fallback_unknown_liquidity_backfilled_from_dexpaprika_when_gecko_empty(monkeypatch):
    """18/08, real bug: GeckoTerminal itself was 429'ing at the exact moment
    a shadow exit-check ran (SadDog, $13K/24h real volume, still got marked
    PIEGEE/stranded) -- DexPaprika is a THIRD source on an independent
    rate-limit budget, reached only when both DexScreener AND GeckoTerminal
    already came back empty."""
    async def fake_fetch_token_pairs(contract, *, chain="solana"):
        return [PairSnapshot(base_address=contract, price_usd=3.5, liquidity_usd=0.0, liquidity_unknown=True)]

    async def fake_get_pool_reserve_usd(pool_address, *, network="solana"):
        return 2062.5

    monkeypatch.setattr(shadow.dexscreener, "fetch_token_pairs", fake_fetch_token_pairs)
    monkeypatch.setattr(shadow.dexpaprika, "get_pool_reserve_usd", fake_get_pool_reserve_usd)
    client = FakeClient({"poolA": None})  # GeckoTerminal has nothing for this pool
    snapshot = await shadow._snapshot_with_fallback(client, "poolA", "tokA", chain=CHAIN)
    assert snapshot.available is True
    assert snapshot.price_usd == 3.5  # DexScreener's price, still never overwritten
    assert snapshot.reserve_usd == 2062.5  # backfilled from DexPaprika, not left None


@pytest.mark.asyncio
async def test_snapshot_fallback_unknown_liquidity_stays_none_when_all_three_sources_empty(monkeypatch):
    """The backfill is best-effort, never a fabrication -- if DexScreener,
    GeckoTerminal, AND DexPaprika all have nothing, reserve_usd must stay
    None, same never-fabricate dome as the rest of this module."""
    async def fake_fetch_token_pairs(contract, *, chain="solana"):
        return [PairSnapshot(base_address=contract, price_usd=3.5, liquidity_usd=0.0, liquidity_unknown=True)]

    async def fake_get_pool_reserve_usd(pool_address, *, network="solana"):
        return None

    monkeypatch.setattr(shadow.dexscreener, "fetch_token_pairs", fake_fetch_token_pairs)
    monkeypatch.setattr(shadow.dexpaprika, "get_pool_reserve_usd", fake_get_pool_reserve_usd)
    client = FakeClient({"poolA": None})  # GeckoTerminal also has nothing for this pool
    snapshot = await shadow._snapshot_with_fallback(client, "poolA", "tokA", chain=CHAIN)
    assert snapshot.available is True
    assert snapshot.price_usd == 3.5
    assert snapshot.reserve_usd is None


@pytest.mark.asyncio
async def test_snapshot_fallback_falls_back_to_geckoterminal_when_dexscreener_empty(monkeypatch):
    async def fake_fetch_token_pairs(contract, *, chain="solana"):
        return []

    monkeypatch.setattr(shadow.dexscreener, "fetch_token_pairs", fake_fetch_token_pairs)
    client = FakeClient({"poolA": 2.0})
    snapshot = await shadow._snapshot_with_fallback(client, "poolA", "tokA", chain=CHAIN)
    assert snapshot.available is True
    assert snapshot.price_usd == 2.0
    assert client.calls == ["poolA"]  # GeckoTerminal used as the real fallback


@pytest.mark.asyncio
async def test_snapshot_fallback_falls_back_on_dexscreener_exception(monkeypatch):
    async def broken_fetch_token_pairs(contract, *, chain="solana"):
        raise RuntimeError("dexscreener unreachable")

    monkeypatch.setattr(shadow.dexscreener, "fetch_token_pairs", broken_fetch_token_pairs)
    client = FakeClient({"poolA": 2.0})
    snapshot = await shadow._snapshot_with_fallback(client, "poolA", "tokA", chain=CHAIN)
    assert snapshot.available is True
    assert snapshot.price_usd == 2.0


@pytest.mark.asyncio
async def test_snapshot_fallback_unavailable_when_both_sources_fail(monkeypatch):
    async def fake_fetch_token_pairs(contract, *, chain="solana"):
        return []

    monkeypatch.setattr(shadow.dexscreener, "fetch_token_pairs", fake_fetch_token_pairs)
    client = FakeClient({"poolA": None})  # GeckoTerminal also has nothing
    snapshot = await shadow._snapshot_with_fallback(client, "poolA", "tokA", chain=CHAIN)
    assert snapshot.available is False  # never fabricated, both sources genuinely empty


@pytest.mark.asyncio
async def test_snapshot_fallback_skips_dexscreener_without_a_token_address(monkeypatch):
    async def unexpected_call(contract, *, chain="solana"):
        raise AssertionError("dexscreener should never be called without a token_address")

    monkeypatch.setattr(shadow.dexscreener, "fetch_token_pairs", unexpected_call)
    client = FakeClient({"poolA": 4.0})
    snapshot = await shadow._snapshot_with_fallback(client, "poolA", None, chain=CHAIN)
    assert snapshot.available is True
    assert snapshot.price_usd == 4.0


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
            INSERT INTO solana_pump_shadow_log
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
        async def get_pool_snapshot(self, pool_address, *, network="solana"):
            if pool_address == "poolA":
                raise RuntimeError("boom")
            return await super().get_pool_snapshot(pool_address, network=network)

    client = RaisingThenWorkingClient({"poolB": 3.0})
    counts = await shadow.evaluate_open_signals(client, chain=CHAIN)
    assert counts["measured_m15"] == 1  # poolB still measured despite poolA's failure


@pytest.mark.asyncio
async def test_evaluate_old_row_awaiting_h2_never_starves_a_younger_rows_m15():
    """Reproduces the real live bug (17/08): the query used to select the
    `limit` OLDEST open rows unconditionally, even when they had nothing due
    (e.g. m15+h1 already measured, just waiting on the 120min h2
    checkpoint). Those rows never leave status='open' until h2 lands, so
    with a small limit they kept re-winning ORDER BY detected_at ASC every
    passage and starved every younger row behind them -- confirmed live: a
    batch of positions stuck with forward_pct_m15 still NULL well past their
    due age, purely because this query never reached them, not because
    their snapshot failed. poolOld (70min old, m15+h1 already measured,
    just waiting on h2 at 120min) must never block poolYoung (20min old,
    m15 genuinely due) even with limit=1."""
    await _insert_open_row(pool_address="poolOld", minutes_ago=70.0)
    await _insert_open_row(pool_address="poolYoung", minutes_ago=20.0)
    async with aiosqlite.connect(shadow._db_path()) as db:
        await db.execute(
            "UPDATE solana_pump_shadow_log SET forward_price_m15 = 1.1, forward_pct_m15 = 10.0, "
            "forward_price_h1 = 1.2, forward_pct_h1 = 20.0 WHERE pool_address = 'poolOld'"
        )
        await db.commit()

    client = FakeClient({"poolOld": 1.3, "poolYoung": 2.0})
    counts = await shadow.evaluate_open_signals(client, chain=CHAIN, limit=1)
    assert counts["measured_m15"] == 1  # poolYoung's due m15, not wasted on poolOld
    assert client.calls == ["poolYoung"]
    rows = {r["pool_address"]: r for r in await _rows()}
    assert rows["poolYoung"]["forward_pct_m15"] == pytest.approx(100.0)
    assert rows["poolOld"]["forward_price_h1"] == pytest.approx(1.2)  # untouched, not yet due for h2


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

    async def get_trending_pools(self, *, network="solana", duration="5m"):
        from aria_core.services.geckoterminal import TrendingPoolsResult

        self.trending_calls.append((network, duration))
        return TrendingPoolsResult(pools=self._pools, available=True, error=None)


@pytest.mark.asyncio
async def test_run_cycle_fetches_logs_and_measures():
    client = FakeGeckoClient([_pool(m5=30.0)], {})
    result = await shadow.run_cycle(client, network=CHAIN)
    assert result["fetched_pools"] == 1
    assert result["signals_logged"] == 1
    assert client.trending_calls == [("solana", "5m")]


@pytest.mark.asyncio
async def test_run_cycle_handles_unavailable_trending_pools():
    from aria_core.services.geckoterminal import TrendingPoolsResult

    class UnavailableClient(FakeClient):
        async def get_trending_pools(self, *, network="solana", duration="5m"):
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
            "INSERT INTO solana_pump_shadow_log (pool_address, chain, status, detected_at, entry_price, forward_pct_h2) "
            "VALUES (?, ?, 'closed', ?, 1.0, 25.0)",
            ("poolA", CHAIN, datetime.now(timezone.utc).isoformat()),
        )
        await db.execute(
            "INSERT INTO solana_pump_shadow_log (pool_address, chain, status, detected_at, entry_price, forward_pct_h2) "
            "VALUES (?, ?, 'closed', ?, 1.0, -10.0)",
            ("poolB", CHAIN, datetime.now(timezone.utc).isoformat()),
        )
        await db.execute(
            "INSERT INTO solana_pump_shadow_log (pool_address, chain, status, detected_at, entry_price) "
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
async def test_advance_exit_simulation_threads_ws_feed_to_snapshot_fallback():
    """27/08 -- ws_feed must actually reach _snapshot_with_fallback, not just
    be accepted as an unused parameter."""
    await _insert_open_row(pool_address="poolA", entry_price=1.0, minutes_ago=10.0)
    ws_feed = _FakeWsFeed({"poolA": _FakeWsSnapshot(available=True, price_usd=1.1)})
    client = FakeClient({"poolA": 99.0})  # would prove wrong if this got used instead
    counts = await shadow.advance_exit_simulation(client, chain=CHAIN, ws_feed=ws_feed)
    assert counts.get("checked", 0) >= 1 or client.calls == []
    assert client.calls == []  # GeckoTerminal never reached -- the ws_feed answered first


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
    await _insert_open_row(pool_address="poolA", entry_price=1.0, minutes_ago=10.0, pool_age_minutes=shadow.MAX_POOL_AGE_MINUTES + 5)
    client = FakeClient({"poolA": 0.95})  # below entry -- losing, no rung/stop/max-hold triggered on its own
    counts = await shadow.advance_exit_simulation(client, chain=CHAIN)
    assert counts["closed_age_limit"] == 1
    rows = await _rows()
    assert rows[0]["exit_reason"] == "age_limit"
    assert rows[0]["remaining_qty"] == 0.0
    assert rows[0]["final_multiplier"] == pytest.approx(0.95)


@pytest.mark.asyncio
async def test_advance_exit_age_limit_never_force_closes_a_winning_position():
    await _insert_open_row(pool_address="poolA", entry_price=1.0, minutes_ago=10.0, pool_age_minutes=shadow.MAX_POOL_AGE_MINUTES + 5)
    client = FakeClient({"poolA": 1.1})  # above entry -- winning, kept open despite the age
    counts = await shadow.advance_exit_simulation(client, chain=CHAIN)
    assert counts["closed_age_limit"] == 0
    rows = await _rows()
    assert rows[0]["exit_reason"] is None
    assert rows[0]["remaining_qty"] == 1.0


@pytest.mark.asyncio
async def test_advance_exit_age_limit_defers_to_trailing_stop_when_already_crossed():
    """Real bug found live (17/08, SOLCATANA closed at -48.3% via age_limit,
    below TRAILING_STOP_PCT's -20% floor): age_limit was checked FIRST and
    sold unconditionally at the point-sample price, so a position whose
    period LOW had already crossed the trailing-stop threshold never got the
    chance to use it. Fix must close at the stop's OWN threshold price
    (peak*0.8 = 0.72, on a peak of 0.9), never the worse point-sample spot,
    and report `trailing_stop` -- not `age_limit`."""
    await _insert_open_row(pool_address="poolA", entry_price=1.0, minutes_ago=10.0, pool_age_minutes=shadow.MAX_POOL_AGE_MINUTES + 5)

    # No prior cycle -> peak_price defaults to entry_price (1.0). A single
    # point-sample crash to 0.10 (well past the -20% stop line at 0.8) --
    # the price the old code would have sold at unconditionally.
    client = FakeClient({"poolA": 0.10})
    counts = await shadow.advance_exit_simulation(client, chain=CHAIN)
    rows = await _rows()
    assert rows[0]["exit_reason"] == "trailing_stop"
    assert counts["closed_age_limit"] == 0
    assert counts["closed_trailing_stop"] == 1
    # Closed at the calibrated stop threshold (1.0 * 0.8 = 0.8), never the
    # crash extreme (0.10) that age_limit would have used.
    assert rows[0]["final_multiplier"] == pytest.approx(1.0 * (1 - shadow.TRAILING_STOP_PCT / 100.0))


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
        async def get_pool_snapshot(self, pool_address, *, network="solana"):
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


def _candle(ts: float, *, open_: float, high: float, low: float, close: float, volume: float = 0.0) -> Candle:
    return Candle(ts=int(ts), open=open_, high=high, low=low, close=close, volume=volume)


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
async def test_advance_exit_accumulates_window_volume_across_passages():
    """17/08, operator-requested -- window_volume_usd must be a RUNNING
    total across the row's whole life, not just the latest passage's
    window (which would be overwritten and lost by the time the row
    closes, useless for a post-hoc analysis)."""
    await _insert_open_row(pool_address="poolA", entry_price=1.0, minutes_ago=10.0)
    detected_epoch = shadow._epoch_of((await _rows())[0]["detected_at"])

    candles1 = [_candle(detected_epoch + 60, open_=1.0, high=1.05, low=0.98, close=1.0, volume=100.0)]
    client1 = FakeClient(
        {"poolA": 1.0}, {"poolA": OHLCVResult(candles=candles1, available=True, error=None)},
    )
    await shadow.advance_exit_simulation(client1, chain=CHAIN)
    rows = await _rows()
    assert rows[0]["window_volume_usd"] == pytest.approx(100.0)
    last_checked_epoch = shadow._epoch_of(rows[0]["last_checked_at"])

    candles2 = [_candle(last_checked_epoch + 60, open_=1.0, high=1.05, low=0.98, close=1.0, volume=250.0)]
    client2 = FakeClient(
        {"poolA": 1.0}, {"poolA": OHLCVResult(candles=candles2, available=True, error=None)},
    )
    await shadow.advance_exit_simulation(client2, chain=CHAIN)
    rows = await _rows()
    assert rows[0]["window_volume_usd"] == pytest.approx(350.0)  # 100 + 250, never overwritten


@pytest.mark.asyncio
async def test_advance_exit_window_volume_stays_none_when_ohlcv_unavailable():
    await _insert_open_row(pool_address="poolA", entry_price=1.0, minutes_ago=10.0)
    client = FakeClient({"poolA": 1.0})  # no OHLCV configured -> unavailable
    await shadow.advance_exit_simulation(client, chain=CHAIN)
    rows = await _rows()
    assert rows[0]["window_volume_usd"] is None  # never fabricated as 0


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
        async def get_ohlcv(self, pool_address, *, network="solana", mode="standard", **_kwargs):
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
            "INSERT INTO solana_pump_shadow_log "
            "(pool_address, chain, status, detected_at, entry_price, exit_reason, final_multiplier) "
            "VALUES (?, ?, 'open', ?, 1.0, 'trailing_stop', 1.5)",
            ("poolA", CHAIN, datetime.now(timezone.utc).isoformat()),
        )
        await db.execute(
            "INSERT INTO solana_pump_shadow_log "
            "(pool_address, chain, status, detected_at, entry_price, exit_reason, final_multiplier) "
            "VALUES (?, ?, 'open', ?, 1.0, 'max_hold', 0.7)",
            ("poolB", CHAIN, datetime.now(timezone.utc).isoformat()),
        )
        await db.execute(
            "INSERT INTO solana_pump_shadow_log (pool_address, chain, status, detected_at, entry_price) "
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
            "INSERT INTO solana_pump_shadow_log "
            "(pool_address, chain, status, detected_at, entry_price, exit_reason, final_multiplier) "
            "VALUES (?, ?, 'closed', ?, 1.0, 'trailing_stop', 1.5)",
            ("poolA", CHAIN, datetime.now(timezone.utc).isoformat()),
        )
        await db.execute(
            "INSERT INTO solana_pump_shadow_log "
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
            "INSERT INTO solana_pump_shadow_log "
            "(pool_address, chain, status, detected_at, entry_price, remaining_qty, realized_proceeds, last_price) "
            "VALUES (?, ?, 'open', ?, 1.0, 0.5, 0.6, 2.0)",
            ("poolA", CHAIN, datetime.now(timezone.utc).isoformat()),
        )
        await db.commit()

    result = await shadow.chain_pnl_summary(CHAIN)
    assert result["open_valued"] == 1
    assert result["pending_price"] == 0
    # (realized 0.6 + remaining 0.5 * last_price 2.0) / entry 1.0 - 1.0
    assert result["total_pnl_units"] == pytest.approx((0.6 + 0.5 * 2.0) / 1.0 - 1.0)


@pytest.mark.asyncio
async def test_chain_pnl_summary_open_row_without_last_price_is_pending_not_counted():
    async with aiosqlite.connect(shadow._db_path()) as db:
        await db.execute(
            "INSERT INTO solana_pump_shadow_log (pool_address, chain, status, detected_at, entry_price) "
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
            "INSERT INTO solana_pump_shadow_log "
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
    # A pool so shallow the SIMULATED entry itself never fills -- must be
    # counted separately, never silently dropped or treated as 0% return.
    async with aiosqlite.connect(shadow._db_path()) as db:
        await db.execute(
            "INSERT INTO solana_pump_shadow_log "
            "(pool_address, chain, status, detected_at, entry_price, realistic_entry_price, "
            "exit_reason, final_multiplier) "
            "VALUES (?, ?, 'closed', ?, 1.0, NULL, 'trailing_stop', 341.68)",
            ("X17690", CHAIN, datetime.now(timezone.utc).isoformat()),
        )
        await db.commit()

    result = await shadow.chain_pnl_summary_realistic(CHAIN)
    assert result["closed"] == 0
    assert result["unreachable_liquidity"] == 1
    assert result["total_pnl_units"] == pytest.approx(0.0)
    # The zero-friction ideal summary, unaffected, still shows the full spike.
    ideal = await shadow.chain_pnl_summary(CHAIN)
    assert ideal["total_pnl_units"] == pytest.approx(340.68)


@pytest.mark.asyncio
async def test_chain_pnl_summary_realistic_counts_row_that_turned_unreachable_mid_exit_as_a_loss():
    """INVARIANT DELIBERATELY CHANGED 17/08 (this test previously asserted the
    opposite, and in doing so locked in a real bug). Entry was reachable but a
    later sell hit a pool too thin to absorb it, so
    ``realistic_final_multiplier`` stays NULL despite ``exit_reason`` being
    set. The old contract filed that under ``unreachable_liquidity`` and
    contributed 0.0 to the total -- meaning capital genuinely spent on a
    position that could never be sold vanished from the P&L instead of
    appearing as a loss. On the real data that dropped 77 of 148 closed
    positions and turned a -30% result into a headline "+663%", which the
    operator caught. A bought-then-stranded position is a LOSS of whatever
    was not salvaged."""
    async with aiosqlite.connect(shadow._db_path()) as db:
        await db.execute(
            "INSERT INTO solana_pump_shadow_log "
            "(pool_address, chain, status, detected_at, entry_price, realistic_entry_price, "
            "exit_reason, final_multiplier, realistic_final_multiplier, realistic_realized_proceeds) "
            "VALUES (?, ?, 'closed', ?, 1.0, 1.02, 'max_hold', 1.1, NULL, 0.0)",
            ("poolB", CHAIN, datetime.now(timezone.utc).isoformat()),
        )
        await db.commit()

    result = await shadow.chain_pnl_summary_realistic(CHAIN)
    assert result["closed"] == 0
    assert result["stranded"] == 1
    assert result["unreachable_liquidity"] == 0  # it WAS bought -- never "unreachable"
    assert result["total_pnl_units"] == pytest.approx(-1.0)  # nothing salvaged
    assert result["total_pnl_usd"] == pytest.approx(-shadow.SIMULATED_TRADE_SIZE_USD)


# --- 17/08: survivorship-bias bug in the realistic P&L aggregate ----------

async def _insert_row_for_pnl(
    *, pool_address, realistic_entry_price, exit_reason=None,
    realistic_final_multiplier=None, realistic_realized_proceeds=0.0,
    last_price=None, remaining_qty=1.0,
):
    async with aiosqlite.connect(shadow._db_path()) as db:
        await db.execute(
            """
            INSERT INTO solana_pump_shadow_log
                (pool_address, chain, status, detected_at, entry_price,
                 realistic_entry_price, exit_reason, realistic_final_multiplier,
                 realistic_realized_proceeds, last_price, remaining_qty)
            VALUES (?, ?, 'open', ?, 1.0, ?, ?, ?, ?, ?, ?)
            """,
            (pool_address, CHAIN, datetime.now(timezone.utc).isoformat(),
             realistic_entry_price, exit_reason, realistic_final_multiplier,
             realistic_realized_proceeds, last_price, remaining_qty),
        )
        await db.commit()


@pytest.mark.asyncio
async def test_stranded_position_counts_as_a_loss_not_as_unmeasurable():
    """THE bug the operator caught live (17/08): a position genuinely BOUGHT
    whose exit later became impossible (pool drained) used to be filed under
    ``unreachable_liquidity`` and dropped from the total -- so the aggregate
    kept only positions that exited cleanly. Textbook survivorship bias: on
    the real data it reported a large POSITIVE percentage while the position
    set was actually down 30%. Stranded capital is a loss."""
    await _insert_row_for_pnl(
        pool_address="poolStranded", realistic_entry_price=1.0,
        exit_reason="trailing_stop", realistic_final_multiplier=None,
        realistic_realized_proceeds=0.0,  # nothing salvaged before it dried up
    )
    summary = await shadow.chain_pnl_summary_realistic(CHAIN)
    assert summary["stranded"] == 1
    assert summary["total_pnl_units"] == pytest.approx(-1.0)  # total loss
    assert summary["total_pnl_usd"] == pytest.approx(-shadow.SIMULATED_TRADE_SIZE_USD)


@pytest.mark.asyncio
async def test_partially_salvaged_stranded_position_keeps_what_it_banked():
    """A ladder that banked some proceeds before the pool dried up is not a
    total loss -- the real figure must reflect what was actually sold."""
    await _insert_row_for_pnl(
        pool_address="poolPartial", realistic_entry_price=1.0,
        exit_reason="trailing_stop", realistic_final_multiplier=None,
        realistic_realized_proceeds=0.4,
    )
    summary = await shadow.chain_pnl_summary_realistic(CHAIN)
    assert summary["total_pnl_units"] == pytest.approx(-0.6)


@pytest.mark.asyncio
async def test_never_bought_position_is_excluded_not_counted_as_a_loss():
    """The symmetric error would be as bad: a pool too thin to ever enter was
    never funded, so it must NOT drag the return down."""
    await _insert_row_for_pnl(pool_address="poolNeverBought", realistic_entry_price=None)
    summary = await shadow.chain_pnl_summary_realistic(CHAIN)
    assert summary["unreachable_liquidity"] == 1
    assert summary["stranded"] == 0
    assert summary["total_pnl_units"] == pytest.approx(0.0)
    assert summary["capital_deployed_usd"] == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_dollar_figures_are_reported_against_capital_actually_deployed():
    """Operator request 17/08: the percentage alone reads like a portfolio
    return without being one. One winner at +100% and one total loss must
    report a NET result on the capital really deployed, not a headline sum."""
    await _insert_row_for_pnl(
        pool_address="poolWin", realistic_entry_price=1.0,
        exit_reason="scale_out_complete", realistic_final_multiplier=2.0,
    )
    await _insert_row_for_pnl(
        pool_address="poolLoss", realistic_entry_price=1.0,
        exit_reason="trailing_stop", realistic_final_multiplier=None,
        realistic_realized_proceeds=0.0,
    )
    summary = await shadow.chain_pnl_summary_realistic(CHAIN)
    size = shadow.SIMULATED_TRADE_SIZE_USD
    assert summary["capital_deployed_usd"] == pytest.approx(2 * size)
    assert summary["total_pnl_usd"] == pytest.approx(0.0)      # +1.0 and -1.0 net out
    assert summary["return_on_deployed_pct"] == pytest.approx(0.0)


# --- 17/08 liquidity-first revision --------------------------------------

class _ReserveClient(FakeClient):
    """FakeClient whose snapshot reports a CONTROLLED current reserve, so the
    liquidity-collapse exit can be exercised against a known entry level."""

    def __init__(self, price_by_pool, reserve_now, ohlcv_by_pool=None, dex_id=None):
        super().__init__(price_by_pool, ohlcv_by_pool)
        self._reserve_now = reserve_now
        self._dex_id = dex_id

    async def get_pool_snapshot(self, pool_address, *, network="solana"):
        self.calls.append(pool_address)
        price = self._prices.get(pool_address)
        if price is None:
            return PoolSnapshot(pool_address=pool_address, available=False, error="unavailable")
        return PoolSnapshot(
            pool_address=pool_address, price_usd=price,
            reserve_usd=self._reserve_now, available=True, dex_id=self._dex_id,
        )


@pytest.mark.asyncio
async def test_pool_below_min_reserve_is_observed_but_never_funded():
    """17/08 root-cause finding: entry liquidity predicts whether a position
    can EVER be closed (<2k$ -> ~70% stranded). Such a pool is still logged
    (sourcing stays unfiltered, per this module's doctrine) but must not be
    funded, so it can never produce a stranded loss."""
    logged = await shadow.record_signals(
        [_pool(m5=40.0, reserve=shadow.MIN_RESERVE_USD_AT_ENTRY - 1)], chain=CHAIN)
    assert logged == 1  # observed
    rows = await _rows()
    assert rows[0]["realistic_entry_price"] is None  # never bought
    assert rows[0]["reserve_usd"] == shadow.MIN_RESERVE_USD_AT_ENTRY - 1


@pytest.mark.asyncio
async def test_pool_at_or_above_min_reserve_is_funded():
    await shadow.record_signals(
        [_pool(m5=40.0, reserve=shadow.MIN_RESERVE_USD_AT_ENTRY)], chain=CHAIN)
    rows = await _rows()
    assert rows[0]["realistic_entry_price"] is not None


@pytest.mark.asyncio
async def test_unknown_reserve_is_never_funded():
    """Fail-CLOSED on unknown depth, same doctrine as the unknown-pool-age
    protection -- an unverifiable pool is 'too risky', never 'assume fine'."""
    pool = dataclasses.replace(_pool(m5=40.0), reserve_usd=None)
    await shadow.record_signals([pool], chain=CHAIN)
    rows = await _rows()
    assert rows[0]["realistic_entry_price"] is None


@pytest.mark.asyncio
async def test_liquidity_collapse_closes_the_position_immediately():
    """THE fix for the 96%-of-losses finding: a draining pool is exited at
    once, without waiting for the price stop -- the point is to sell while a
    buyer still exists."""
    await shadow.record_signals([_pool(m5=40.0, reserve=100_000.0, price_usd=1.0)], chain=CHAIN)
    collapsed = 100_000.0 * (1 - shadow.LIQUIDITY_COLLAPSE_EXIT_PCT / 100.0) - 1
    client = _ReserveClient({"poolA": 1.05}, reserve_now=collapsed)  # price still FINE
    counts = await shadow.advance_exit_simulation(client, chain=CHAIN)
    assert counts["closed_liquidity_collapse"] == 1
    rows = await _rows()
    assert rows[0]["exit_reason"] == "liquidity_collapse"
    assert rows[0]["remaining_qty"] == 0.0


@pytest.mark.asyncio
async def test_stable_liquidity_never_triggers_the_collapse_exit():
    await shadow.record_signals([_pool(m5=40.0, reserve=100_000.0, price_usd=1.0)], chain=CHAIN)
    client = _ReserveClient({"poolA": 1.05}, reserve_now=95_000.0)
    counts = await shadow.advance_exit_simulation(client, chain=CHAIN)
    assert counts["closed_liquidity_collapse"] == 0
    rows = await _rows()
    assert rows[0]["exit_reason"] is None


@pytest.mark.asyncio
async def test_unknown_current_reserve_never_forces_a_close():
    """Fail-OPEN here, deliberately asymmetric with the entry filter: closing
    a position on missing data would fabricate an exit that no real signal
    justified."""
    await shadow.record_signals([_pool(m5=40.0, reserve=100_000.0, price_usd=1.0)], chain=CHAIN)
    client = _ReserveClient({"poolA": 1.05}, reserve_now=None)
    counts = await shadow.advance_exit_simulation(client, chain=CHAIN)
    assert counts["closed_liquidity_collapse"] == 0


@pytest.mark.asyncio
async def test_pumpswap_pool_never_triggers_liquidity_collapse():
    """17/08, real bug found live (EYE, PumpSwap dex_id): both DexScreener
    and GeckoTerminal report near-zero reserve for graduated pump.fun pools
    regardless of real liquidity -- the check is disabled entirely for this
    pool type rather than firing false closures."""
    await shadow.record_signals([_pool(m5=40.0, reserve=100_000.0, price_usd=1.0)], chain=CHAIN)
    client = _ReserveClient({"poolA": 1.05}, reserve_now=0.0, dex_id="pumpswap")
    counts = await shadow.advance_exit_simulation(client, chain=CHAIN)
    assert counts["closed_liquidity_collapse"] == 0
    rows = await _rows()
    assert rows[0]["exit_reason"] is None


@pytest.mark.asyncio
async def test_liquidity_collapse_takes_priority_over_age_limit():
    """Both would fire; the liquidity exit must win, since it is the one that
    protects against the stranded-total-loss case."""
    await shadow.record_signals([_pool(m5=40.0, reserve=100_000.0, price_usd=1.0)], chain=CHAIN)
    # A pool already past MAX_POOL_AGE_MINUTES is refused at sourcing, so the
    # age limit can only ever apply to a pool that aged WHILE held -- reproduce
    # that by ageing the stored row rather than the incoming signal.
    old = (datetime.now(timezone.utc)
           - timedelta(minutes=shadow.MAX_POOL_AGE_MINUTES + 5)).isoformat()
    async with aiosqlite.connect(shadow._db_path()) as db:
        await db.execute(
            "UPDATE solana_pump_shadow_log SET pool_created_at = ?", (old,))
        await db.commit()

    client = _ReserveClient({"poolA": 0.9}, reserve_now=1_000.0)  # losing AND drained
    counts = await shadow.advance_exit_simulation(client, chain=CHAIN)
    assert counts["closed_liquidity_collapse"] == 1
    assert counts["closed_age_limit"] == 0


@pytest.mark.asyncio
async def test_entry_cap_when_armed_observes_but_does_not_fund(monkeypatch):
    """Operator's own idea, confirmed on the real sample: an entry that has
    already run far in 5 minutes is buying the top of a launch spike, and
    those are the pools that drain. Worth ~nothing alone, strong combined
    with the liquidity floor (stranded rate 35% -> 25%). Currently DISABLED
    (None) for the age-window run, so the test arms it explicitly rather
    than asserting against whatever the live setting happens to be."""
    monkeypatch.setattr(shadow, "M5_ENTRY_CAP_PCT", 60.0)
    await shadow.record_signals([_pool(m5=61.0, reserve=100_000.0)], chain=CHAIN)
    rows = await _rows()
    assert rows[0]["realistic_entry_price"] is None  # observed, never bought
    assert rows[0]["m5_pct"] == 61.0  # still fully logged


@pytest.mark.asyncio
async def test_entry_below_the_cap_with_enough_liquidity_is_funded(monkeypatch):
    monkeypatch.setattr(shadow, "M5_ENTRY_CAP_PCT", 60.0)
    await shadow.record_signals([_pool(m5=59.0, reserve=100_000.0)], chain=CHAIN)
    rows = await _rows()
    assert rows[0]["realistic_entry_price"] is not None


@pytest.mark.asyncio
async def test_no_cap_funds_even_a_large_spike(monkeypatch):
    """The disabled state must genuinely disable -- not silently fall back to
    some default that would keep filtering the age-window run."""
    monkeypatch.setattr(shadow, "M5_ENTRY_CAP_PCT", None)
    await shadow.record_signals([_pool(m5=500.0, reserve=100_000.0)], chain=CHAIN)
    rows = await _rows()
    assert rows[0]["realistic_entry_price"] is not None


@pytest.mark.asyncio
async def test_current_reserve_is_traced_on_every_pass():
    """Needed to ever calibrate the collapse threshold on evidence: today it
    is a static guess with no data on how fast pools really drain."""
    await shadow.record_signals([_pool(m5=40.0, reserve=100_000.0, price_usd=1.0)], chain=CHAIN)
    client = _ReserveClient({"poolA": 1.02}, reserve_now=88_000.0)
    await shadow.advance_exit_simulation(client, chain=CHAIN)
    rows = await _rows()
    assert rows[0]["last_reserve_usd"] == pytest.approx(88_000.0)
    assert rows[0]["reserve_usd"] == pytest.approx(100_000.0)  # entry value untouched


@pytest.mark.asyncio
async def test_age_filter_is_a_window_not_just_a_ceiling():
    """17/08 -- the age filter was inverted after the archive showed the
    stranded rate falls sharply with pool age (0-5min -> 73% stranded,
    10-15min -> 31%). A cap alone FORCED entries into the most dangerous
    window; a minimum skips the launch chaos."""
    too_young = datetime.now(timezone.utc) - timedelta(minutes=shadow.MIN_POOL_AGE_MINUTES - 1)
    assert await shadow.record_signals(
        [_pool(m5=40.0, pool_created_at=too_young)], chain=CHAIN) == 0

    too_old = datetime.now(timezone.utc) - timedelta(minutes=shadow.MAX_POOL_AGE_MINUTES + 1)
    assert await shadow.record_signals(
        [_pool(m5=40.0, pool_address="poolOld", pool_created_at=too_old)], chain=CHAIN) == 0

    inside = datetime.now(timezone.utc) - timedelta(minutes=shadow.MIN_POOL_AGE_MINUTES + 1)
    assert await shadow.record_signals(
        [_pool(m5=40.0, pool_address="poolOk", pool_created_at=inside)], chain=CHAIN) == 1


@pytest.mark.asyncio
async def test_funded_rows_are_tracked_before_unfunded_ones():
    """17/08 -- since the entry filters landed most observed signals are never
    bought (11 tracked for 3 funded when this was added), yet each still costs
    a real API call against an already-strained shared throttle, crowding out
    the only positions the test measures. Funded rows must win the limited
    budget; unfunded ones are still tracked, just after."""
    await _insert_open_row(pool_address="poolUnfunded", minutes_ago=90.0,
                           realistic_entry_price=None)
    await _insert_open_row(pool_address="poolFunded", minutes_ago=20.0)  # younger!

    client = FakeClient({"poolFunded": 2.0, "poolUnfunded": 2.0})
    await shadow.evaluate_open_signals(client, chain=CHAIN, limit=1)
    assert client.calls == ["poolFunded"], "une ligne non financee a vole le budget"


# ---------------------------------------------------------------- regime gate
# 23/08 -- same mechanism/threshold as solana_late_bonding_shadow.py's own
# regime gate, generalised to this pocket (operator: "construit ce 30 sur
# tout le monde"). See shadow.py's own module-level comment for the full
# design rationale (why this pocket reuses the discovery fetch instead of a
# dedicated poll, and why regime_median_peak is duplicated rather than
# imported -- this module is imported BY solana_late_bonding_shadow.py, so a
# reverse import would be circular).
class TestRegimeGate:
    def test_below_the_window_there_is_no_verdict(self):
        assert shadow.regime_median_peak([50.0] * (shadow.REGIME_WINDOW - 1)) is None
        assert shadow.regime_median_peak([]) is None

    def test_the_median_ignores_a_single_spike(self):
        peaks = [0.0] * (shadow.REGIME_WINDOW - 1) + [1000.0]
        assert shadow.regime_median_peak(peaks) == 0.0

    @pytest.mark.asyncio
    async def test_an_empty_pocket_reads_as_open(self):
        state = await shadow.regime_state()
        assert state["open"] is True
        assert state["samples"] == 0

    @pytest.mark.asyncio
    async def test_a_cold_market_shuts_the_gate_and_a_hot_one_opens_it(self, monkeypatch):
        monkeypatch.setattr(shadow, "REGIME_MIN_MEDIAN_PEAK_PCT", 20.0)
        await shadow._ensure_regime_candidates_table()

        async def _fill(peak_pct):
            async with aiosqlite.connect(shadow._db_path()) as db:
                await db.execute(f"DELETE FROM {shadow.REGIME_CANDIDATES_TABLE}")
                for i in range(shadow.REGIME_WINDOW):
                    await db.execute(
                        f"INSERT INTO {shadow.REGIME_CANDIDATES_TABLE} "
                        f"(pool_address, mint, chain, decided_at, entry_price, "
                        f" reserve_usd, peak_price, last_checked_at, tracking_status) "
                        f"VALUES (?,?,?,?,?,?,?,?,?)",
                        (f"pool{i}", f"mint{i}", CHAIN, f"2026-08-23T00:{i:02d}:00+00:00",
                         1.0, 5000.0, 1.0 + peak_pct / 100.0,
                         f"2026-08-23T00:{i:02d}:00+00:00", "closed"),
                    )
                await db.commit()

        await _fill(15.0)
        cold = await shadow.regime_state()
        assert cold["open"] is False

        await _fill(25.0)
        hot = await shadow.regime_state()
        assert hot["open"] is True

    @pytest.mark.asyncio
    async def test_a_shut_gate_still_records_new_candidates(self, monkeypatch):
        """The exact defect late_bonding's first sensor had: a shut gate must
        not stop feeding its own sensor."""
        for i in range(shadow.REGIME_WINDOW):
            await shadow.record_regime_candidate(
                pool_address=f"cold{i}", mint=f"cold{i}", chain=CHAIN,
                entry_price=1.0, reserve_usd=5000.0,
            )
        monkeypatch.setattr(shadow, "REGIME_MIN_MEDIAN_PEAK_PCT", 999.0)
        assert (await shadow.regime_state())["open"] is False

        async with aiosqlite.connect(shadow._db_path()) as db:
            before = (await (await db.execute(
                f"SELECT COUNT(*) FROM {shadow.REGIME_CANDIDATES_TABLE}"
            )).fetchone())[0]

        await shadow.record_regime_candidate(
            pool_address="poolX", mint="mintX", chain=CHAIN,
            entry_price=1.0, reserve_usd=5000.0,
        )

        async with aiosqlite.connect(shadow._db_path()) as db:
            after = (await (await db.execute(
                f"SELECT COUNT(*) FROM {shadow.REGIME_CANDIDATES_TABLE}"
            )).fetchone())[0]
        assert after == before + 1

    @pytest.mark.asyncio
    async def test_a_disarmed_gate_reads_as_open_never_as_shut(self, monkeypatch):
        monkeypatch.setattr(shadow, "REGIME_MIN_MEDIAN_PEAK_PCT", None)
        state = await shadow.regime_state()
        assert state["open"] is True
        assert state["disarmed"] is True

    @pytest.mark.asyncio
    async def test_record_signals_blocks_a_new_entry_when_the_regime_is_shut(self, monkeypatch):
        """Integration: a candidate clearing every other filter must still
        be refused (never inserted into the pocket's own log) once the
        regime gate is shut -- and the refusal must be tracked, even though
        this pocket has no pre-existing ``_refuse`` helper to reuse."""
        monkeypatch.setattr(shadow, "REGIME_MIN_MEDIAN_PEAK_PCT", 999.0)
        for i in range(shadow.REGIME_WINDOW):
            await shadow.record_regime_candidate(
                pool_address=f"seed{i}", mint=f"seed{i}", chain=CHAIN,
                entry_price=1.0, reserve_usd=5000.0,
            )
        assert (await shadow.regime_state())["open"] is False

        seen = []

        async def _capture(decision, **_kw):
            seen.append(decision)
            return 1

        from aria_core import pretrade_rejection_log
        original = pretrade_rejection_log.record_decision
        pretrade_rejection_log.record_decision = _capture
        try:
            await shadow.record_signals([_pool(pool_address="poolNew", token_address="tokNew")], chain=CHAIN)
        finally:
            pretrade_rejection_log.record_decision = original

        assert await _rows() == [], "the regime gate must block the insert entirely"
        reasons = [d.reason for d in seen]
        assert "blocked_regime_closed" in reasons

    @pytest.mark.asyncio
    async def test_record_signals_still_opens_when_regime_is_disarmed(self, monkeypatch):
        """Default production posture (REGIME_MIN_MEDIAN_PEAK_PCT=25.0, but a
        fresh table has < REGIME_WINDOW samples) must leave record_signals
        behaving exactly as it did before this change -- a candidate that
        passes every other filter still gets logged."""
        n = await shadow.record_signals([_pool(pool_address="poolFresh", token_address="tokFresh")], chain=CHAIN)
        assert n == 1
        rows = await _rows()
        assert len(rows) == 1
        assert rows[0]["pool_address"] == "poolFresh"

    @pytest.mark.asyncio
    async def test_advance_regime_candidates_from_pools_updates_peak_with_no_network_call(self):
        """The whole point of reusing the discovery fetch: a plain list of
        already-fetched TrendingPool objects is enough, no client/feed
        object is even accepted by this function's signature."""
        await shadow.record_regime_candidate(
            pool_address="poolY", mint="mintY", chain=CHAIN,
            entry_price=1.0, reserve_usd=5000.0,
        )
        fresh_pools = [_pool(pool_address="poolY", token_address="mintY", price_usd=1.8)]
        stats = await shadow.advance_regime_candidates_from_pools(fresh_pools)
        assert stats["checked"] == 1
        assert stats["updated"] == 1

        async with aiosqlite.connect(shadow._db_path()) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                f"SELECT peak_price, tracking_status FROM {shadow.REGIME_CANDIDATES_TABLE}"
            )
            row = dict(await cur.fetchone())
        assert row["peak_price"] == 1.8
        assert row["tracking_status"] == "tracking"

    @pytest.mark.asyncio
    async def test_advance_regime_candidates_from_pools_expires_after_the_window(self):
        await shadow._ensure_regime_candidates_table()
        async with aiosqlite.connect(shadow._db_path()) as db:
            await db.execute(
                f"INSERT INTO {shadow.REGIME_CANDIDATES_TABLE} "
                f"(pool_address, mint, chain, decided_at, entry_price, reserve_usd, "
                f" peak_price, last_checked_at, tracking_status) "
                f"VALUES ('poolZ','mintZ','{CHAIN}','2020-01-01T00:00:00+00:00',"
                f" 1.0, 5000.0, 1.0, '2020-01-01T00:00:00+00:00', 'tracking')"
            )
            await db.commit()
        stats = await shadow.advance_regime_candidates_from_pools([])
        assert stats["closed"] == 1

    @pytest.mark.asyncio
    async def test_advance_regime_candidates_from_pools_a_pool_dropped_from_trending_still_expires(self):
        """HONEST LIMIT case: a candidate absent from the current cycle's
        response must not track forever -- it still closes on schedule, just
        without a fresher peak."""
        await shadow._ensure_regime_candidates_table()
        async with aiosqlite.connect(shadow._db_path()) as db:
            await db.execute(
                f"INSERT INTO {shadow.REGIME_CANDIDATES_TABLE} "
                f"(pool_address, mint, chain, decided_at, entry_price, reserve_usd, "
                f" peak_price, last_checked_at, tracking_status) "
                f"VALUES ('poolGone','mintGone','{CHAIN}','2020-01-01T00:00:00+00:00',"
                f" 1.0, 5000.0, 1.2, '2020-01-01T00:00:00+00:00', 'tracking')"
            )
            await db.commit()
        stats = await shadow.advance_regime_candidates_from_pools(
            [_pool(pool_address="poolOther", token_address="tokOther", price_usd=2.0)]
        )
        assert stats["closed"] == 1

        async with aiosqlite.connect(shadow._db_path()) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                f"SELECT peak_price FROM {shadow.REGIME_CANDIDATES_TABLE} WHERE pool_address='poolGone'"
            )
            row = dict(await cur.fetchone())
        assert row["peak_price"] == 1.2, "peak must not move without a real observation"

    @pytest.mark.asyncio
    async def test_advance_regime_candidates_from_pools_respects_max_rows(self):
        """The debit mechanism: even with more tracked rows than max_rows,
        this stays a bounded local read (never an unbounded scan)."""
        for i in range(5):
            await shadow.record_regime_candidate(
                pool_address=f"cap{i}", mint=f"cap{i}", chain=CHAIN,
                entry_price=1.0, reserve_usd=5000.0,
            )
        stats = await shadow.advance_regime_candidates_from_pools(
            [_pool(pool_address=f"cap{i}", token_address=f"cap{i}", price_usd=2.0) for i in range(5)],
            max_rows=2,
        )
        assert stats["checked"] == 2

    def test_the_regime_reject_reason_is_tracked_forward(self):
        import inspect
        from aria_core import pretrade_rejection_log
        source = inspect.getsource(pretrade_rejection_log.record_decision)
        assert '"blocked_regime_closed"' in source
