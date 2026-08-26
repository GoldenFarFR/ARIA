"""Dip-recovery shadow, v2 (26/08) -- Base/Robinhood, market-cap-bounded,
fixed +25% take-profit. Mirrors test_dip_recovery_shadow.py's pattern
(isolated tmp sqlite, state machine tested directly) but network-sourced
(DexPaprika discovery + DexScreener market-cap resolution) rather than
candle-driven, since this variant has no watchlist/candle_history feed.

Dedup is exercised directly against `dip_recovery_v2_shadow` (an open-row
check, per specs/012-dip-recovery-v2 research.md Decision 1) -- there is no
separate episode-state table to inspect, unlike the module's own first
draft."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import aiosqlite
import pytest

from aria_core import dip_recovery_v2_shadow as shadow
from aria_core import shadow_candle_archive
from aria_core.services.dexpaprika import TrendingPool, TrendingPoolsResult
from aria_core.services.dexscreener import PairSnapshot
from aria_core.services.geckoterminal import OHLCVResult
from aria_core.skills.ta_levels import Candle

CONTRACT = "0x" + "d" * 40
CHAIN = "base"


@pytest.fixture(autouse=True)
async def _tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(shadow, "DB_PATH", str(tmp_path / "shadow.db"))
    shadow._ensured_db_paths.clear()
    monkeypatch.setattr(shadow, "_last_notified_open_id", None)
    monkeypatch.setattr(shadow, "_notified_closed_ids", set())
    await shadow._ensure_tables()
    yield
    shadow._ensured_db_paths.clear()


@pytest.fixture(autouse=True)
async def _no_real_candle_archive_calls(monkeypatch):
    """The module's own before/after candle-archive wiring (26/08) calls
    `dexpaprika.get_ohlcv` on every open/advance -- without this guard,
    every test that doesn't care about archiving would otherwise make a
    REAL network call (same guard pattern as
    test_robinhood_pump_shadow.py's own `_NetworkGuardClient`)."""
    async def _fake_get_ohlcv(pool_address, *, network="base", mode="standard"):
        return OHLCVResult(candles=[], available=False)

    monkeypatch.setattr(shadow.dexpaprika, "get_ohlcv", _fake_get_ohlcv)


async def _rows(contract=CONTRACT, chain=CHAIN):
    async with aiosqlite.connect(shadow._db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM dip_recovery_v2_shadow WHERE contract = ? AND chain = ? ORDER BY id",
            (contract, chain),
        )
        return [dict(r) for r in await cur.fetchall()]


_UNSET = object()


def _pool(
    *, var_24h: float | None = -31.0, token_address: str = CONTRACT, pool_address: str = "0xPOOL",
    pool_created_at: datetime | None = _UNSET,
) -> TrendingPool:
    if pool_created_at is _UNSET:
        pool_created_at = datetime.now(timezone.utc) - timedelta(days=30)
    return TrendingPool(
        pool_address=pool_address,
        token_address=token_address,
        symbol="TOK",
        price_usd=1.0,
        price_change_pct={"h24": var_24h} if var_24h is not None else {},
        transactions_m15=None,
        volume_usd_m15=None,
        reserve_usd=30_000.0,
        pool_created_at=pool_created_at,
    )


def _snapshot(*, market_cap_usd: float | None = 200_000.0, liquidity_usd: float = 30_000.0, price_usd: float = 1.0, pair_address: str = "0xPOOL", base_symbol: str = "TOK", price_change_24h: float = 0.0) -> PairSnapshot:
    return PairSnapshot(
        pair_address=pair_address, price_usd=price_usd, liquidity_usd=liquidity_usd,
        market_cap_usd=market_cap_usd, base_symbol=base_symbol, price_change_24h=price_change_24h,
    )


# --- discover_and_record: server-side filter + open-row dedup --------------

@pytest.mark.asyncio
async def test_discover_opens_position_on_qualifying_dip(monkeypatch):
    async def fake_trending(*a, **k):
        return TrendingPoolsResult(pools=[_pool()], available=True, error=None)

    async def fake_pairs(contract, *, chain="base"):
        return [_snapshot()]

    monkeypatch.setattr(shadow.dexpaprika, "get_trending_pools", fake_trending)
    monkeypatch.setattr(shadow.dexscreener, "fetch_token_pairs", fake_pairs)

    opened = await shadow.discover_and_record(CHAIN)
    assert opened == 1
    rows = await _rows()
    assert len(rows) == 1
    assert rows[0]["status"] == "open"
    # 26/08 -- real bug fixed: symbol was hardcoded None despite DexScreener
    # already carrying it for free on the same snapshot (operator-reported
    # "?" in Telegram notifications).
    assert rows[0]["symbol"] == "TOK"
    assert rows[0]["entry_var_24h_pct"] == pytest.approx(-31.0)
    assert rows[0]["entry_market_cap_usd"] == pytest.approx(200_000.0)
    assert rows[0]["entry_pool_age_days"] >= shadow.MIN_POOL_AGE_DAYS
    # Entry pays the real DEX swap fee, never the raw quote.
    assert rows[0]["entry_price"] == pytest.approx(shadow._realistic_fill_price(1.0))


# --- specs/013: cross-provider entry sanity guard ---------------------------

@pytest.mark.asyncio
async def test_discover_rejects_entry_on_provider_sign_disagreement(monkeypatch):
    """Real incident (26/08): position id=13, contract
    0x23acfab04106a21af0ae1643b74cfec3c9aac181, chain=robinhood. DexPaprika
    read -31.9487081644224 at entry; DexScreener/DexPaprika's own live lookup
    both agreed ~+29% minutes later for the same token. Reproduced here with
    the exact real numbers."""
    async def fake_trending(*a, **k):
        return TrendingPoolsResult(pools=[_pool(var_24h=-31.9487081644224)], available=True, error=None)

    async def fake_pairs(contract, *, chain="base"):
        return [_snapshot(price_change_24h=29.0)]

    monkeypatch.setattr(shadow.dexpaprika, "get_trending_pools", fake_trending)
    monkeypatch.setattr(shadow.dexscreener, "fetch_token_pairs", fake_pairs)

    opened = await shadow.discover_and_record(CHAIN)
    assert opened == 0
    assert await _rows() == []


@pytest.mark.asyncio
async def test_discover_opens_on_ordinary_same_direction_disagreement(monkeypatch):
    """Two providers sampled moments apart rarely agree to the decimal point
    -- same-direction drift (both negative, different magnitude) is never a
    sign disagreement and must not block an otherwise-qualifying entry."""
    async def fake_trending(*a, **k):
        return TrendingPoolsResult(pools=[_pool(var_24h=-31.0)], available=True, error=None)

    async def fake_pairs(contract, *, chain="base"):
        return [_snapshot(price_change_24h=-22.0)]

    monkeypatch.setattr(shadow.dexpaprika, "get_trending_pools", fake_trending)
    monkeypatch.setattr(shadow.dexscreener, "fetch_token_pairs", fake_pairs)

    opened = await shadow.discover_and_record(CHAIN)
    assert opened == 1


@pytest.mark.asyncio
async def test_discover_opens_when_dexscreener_change_is_missing_or_zero(monkeypatch):
    """dexscreener.PairSnapshot.price_change_24h defaults to 0.0 when the
    provider's field is absent (no "unknown" sentinel exists for it, unlike
    liquidity_usd's liquidity_unknown) -- never treated as a disagreement."""
    async def fake_trending(*a, **k):
        return TrendingPoolsResult(pools=[_pool(var_24h=-35.0)], available=True, error=None)

    async def fake_pairs(contract, *, chain="base"):
        return [_snapshot(price_change_24h=0.0)]

    monkeypatch.setattr(shadow.dexpaprika, "get_trending_pools", fake_trending)
    monkeypatch.setattr(shadow.dexscreener, "fetch_token_pairs", fake_pairs)

    opened = await shadow.discover_and_record(CHAIN)
    assert opened == 1


@pytest.mark.asyncio
async def test_entry_sanity_rejection_is_logged_distinctly(monkeypatch, caplog):
    async def fake_trending(*a, **k):
        return TrendingPoolsResult(pools=[_pool(var_24h=-31.9487081644224)], available=True, error=None)

    async def fake_pairs(contract, *, chain="base"):
        return [_snapshot(price_change_24h=29.0)]

    monkeypatch.setattr(shadow.dexpaprika, "get_trending_pools", fake_trending)
    monkeypatch.setattr(shadow.dexscreener, "fetch_token_pairs", fake_pairs)

    with caplog.at_level(logging.INFO, logger=shadow.logger.name):
        opened = await shadow.discover_and_record(CHAIN)
    assert opened == 0
    assert "entry sanity guard" in caplog.text


@pytest.mark.asyncio
async def test_discover_ignores_dip_under_threshold(monkeypatch):
    async def fake_trending(*a, **k):
        return TrendingPoolsResult(pools=[_pool(var_24h=-20.0)], available=True, error=None)

    called = False

    async def fake_pairs(contract, *, chain="base"):
        nonlocal called
        called = True
        return [_snapshot()]

    monkeypatch.setattr(shadow.dexpaprika, "get_trending_pools", fake_trending)
    monkeypatch.setattr(shadow.dexscreener, "fetch_token_pairs", fake_pairs)

    opened = await shadow.discover_and_record(CHAIN)
    assert opened == 0
    assert await _rows() == []
    # Server-side/pre-filter rejection never spends the paid DexScreener call.
    assert called is False


@pytest.mark.asyncio
async def test_discover_rejects_market_cap_above_band(monkeypatch):
    async def fake_trending(*a, **k):
        return TrendingPoolsResult(pools=[_pool()], available=True, error=None)

    async def fake_pairs(contract, *, chain="base"):
        return [_snapshot(market_cap_usd=5_000_000.0)]

    monkeypatch.setattr(shadow.dexpaprika, "get_trending_pools", fake_trending)
    monkeypatch.setattr(shadow.dexscreener, "fetch_token_pairs", fake_pairs)

    opened = await shadow.discover_and_record(CHAIN)
    assert opened == 0
    assert await _rows() == []


@pytest.mark.asyncio
async def test_discover_rejects_market_cap_below_band(monkeypatch):
    async def fake_trending(*a, **k):
        return TrendingPoolsResult(pools=[_pool()], available=True, error=None)

    async def fake_pairs(contract, *, chain="base"):
        return [_snapshot(market_cap_usd=10_000.0)]

    monkeypatch.setattr(shadow.dexpaprika, "get_trending_pools", fake_trending)
    monkeypatch.setattr(shadow.dexscreener, "fetch_token_pairs", fake_pairs)

    opened = await shadow.discover_and_record(CHAIN)
    assert opened == 0
    assert await _rows() == []


@pytest.mark.asyncio
async def test_discover_rejects_liquidity_below_floor(monkeypatch):
    async def fake_trending(*a, **k):
        return TrendingPoolsResult(pools=[_pool()], available=True, error=None)

    async def fake_pairs(contract, *, chain="base"):
        return [_snapshot(liquidity_usd=1_000.0)]

    monkeypatch.setattr(shadow.dexpaprika, "get_trending_pools", fake_trending)
    monkeypatch.setattr(shadow.dexscreener, "fetch_token_pairs", fake_pairs)

    opened = await shadow.discover_and_record(CHAIN)
    assert opened == 0
    assert await _rows() == []


@pytest.mark.asyncio
async def test_discover_rejects_pair_younger_than_minimum_age(monkeypatch):
    async def fake_trending(*a, **k):
        young = datetime.now(timezone.utc) - timedelta(days=5)
        return TrendingPoolsResult(pools=[_pool(pool_created_at=young)], available=True, error=None)

    called = False

    async def fake_pairs(contract, *, chain="base"):
        nonlocal called
        called = True
        return [_snapshot()]

    monkeypatch.setattr(shadow.dexpaprika, "get_trending_pools", fake_trending)
    monkeypatch.setattr(shadow.dexscreener, "fetch_token_pairs", fake_pairs)

    opened = await shadow.discover_and_record(CHAIN)
    assert opened == 0
    assert await _rows() == []
    # Rejected before the paid call -- same funnel placement as the var_24h check.
    assert called is False


@pytest.mark.asyncio
async def test_discover_rejects_pair_with_unknown_age(monkeypatch):
    async def fake_trending(*a, **k):
        return TrendingPoolsResult(pools=[_pool(pool_created_at=None)], available=True, error=None)

    called = False

    async def fake_pairs(contract, *, chain="base"):
        nonlocal called
        called = True
        return [_snapshot()]

    monkeypatch.setattr(shadow.dexpaprika, "get_trending_pools", fake_trending)
    monkeypatch.setattr(shadow.dexscreener, "fetch_token_pairs", fake_pairs)

    opened = await shadow.discover_and_record(CHAIN)
    assert opened == 0
    assert await _rows() == []
    # Never fabricate "old enough" for a candidate with no age data.
    assert called is False


@pytest.mark.asyncio
async def test_discover_accepts_pair_at_or_above_minimum_age(monkeypatch):
    async def fake_trending(*a, **k):
        exactly_old_enough = datetime.now(timezone.utc) - timedelta(days=shadow.MIN_POOL_AGE_DAYS, hours=1)
        return TrendingPoolsResult(pools=[_pool(pool_created_at=exactly_old_enough)], available=True, error=None)

    async def fake_pairs(contract, *, chain="base"):
        return [_snapshot()]

    monkeypatch.setattr(shadow.dexpaprika, "get_trending_pools", fake_trending)
    monkeypatch.setattr(shadow.dexscreener, "fetch_token_pairs", fake_pairs)

    opened = await shadow.discover_and_record(CHAIN)
    assert opened == 1
    rows = await _rows()
    assert rows[0]["entry_pool_age_days"] >= shadow.MIN_POOL_AGE_DAYS


@pytest.mark.asyncio
async def test_discover_never_reopens_while_a_position_is_open(monkeypatch):
    async def fake_trending(*a, **k):
        return TrendingPoolsResult(pools=[_pool()], available=True, error=None)

    async def fake_pairs(contract, *, chain="base"):
        return [_snapshot()]

    monkeypatch.setattr(shadow.dexpaprika, "get_trending_pools", fake_trending)
    monkeypatch.setattr(shadow.dexscreener, "fetch_token_pairs", fake_pairs)

    await shadow.discover_and_record(CHAIN)
    opened_second = await shadow.discover_and_record(CHAIN)
    assert opened_second == 0
    rows = await _rows()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_discover_reopens_after_prior_position_closes(monkeypatch):
    """Decision 1's real fix: dedup keys on an OPEN row, not a recovery-
    triggered flag that this pocket's own discovery feed can never observe
    clearing (a token that recovers simply stops appearing in the worst-24h
    performers feed). Once the prior position is closed, a fresh qualifying
    dip must be allowed to open a new one."""
    async def fake_trending(*a, **k):
        return TrendingPoolsResult(pools=[_pool()], available=True, error=None)

    async def fake_pairs(contract, *, chain="base"):
        return [_snapshot()]

    monkeypatch.setattr(shadow.dexpaprika, "get_trending_pools", fake_trending)
    monkeypatch.setattr(shadow.dexscreener, "fetch_token_pairs", fake_pairs)

    await shadow.discover_and_record(CHAIN)
    rows = await _rows()
    assert len(rows) == 1

    async with aiosqlite.connect(shadow._db_path()) as db:
        await db.execute(
            "UPDATE dip_recovery_v2_shadow SET status = 'closed' WHERE id = ?",
            (rows[0]["id"],),
        )
        await db.commit()

    opened_second = await shadow.discover_and_record(CHAIN)
    assert opened_second == 1
    rows = await _rows()
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_discover_returns_zero_when_provider_unavailable(monkeypatch):
    async def fake_trending(*a, **k):
        return TrendingPoolsResult(available=False, error="rate limit")

    monkeypatch.setattr(shadow.dexpaprika, "get_trending_pools", fake_trending)
    opened = await shadow.discover_and_record(CHAIN)
    assert opened == 0
    assert await _rows() == []


# --- advance_open_positions: fixed take-profit / timeout --------------------

@pytest.mark.asyncio
async def test_take_profit_closes_position_at_25pct(monkeypatch):
    async def fake_trending(*a, **k):
        return TrendingPoolsResult(pools=[_pool()], available=True, error=None)

    async def fake_pairs_entry(contract, *, chain="base"):
        return [_snapshot(price_usd=1.0)]

    monkeypatch.setattr(shadow.dexpaprika, "get_trending_pools", fake_trending)
    monkeypatch.setattr(shadow.dexscreener, "fetch_token_pairs", fake_pairs_entry)
    await shadow.discover_and_record(CHAIN)
    rows = await _rows()
    entry_price = rows[0]["entry_price"]

    # Price needs to clear +25% AFTER the exit-side fee too -- solve forward
    # from the stored entry_price rather than assuming a raw multiple.
    from aria_core import risk_guard

    target_raw_price = entry_price * 1.30 / (1.0 - risk_guard.DEX_SWAP_FEE_PCT)

    async def fake_pairs_exit(contract, *, chain="base"):
        return [_snapshot(price_usd=target_raw_price)]

    monkeypatch.setattr(shadow.dexscreener, "fetch_token_pairs", fake_pairs_exit)
    counts = await shadow.advance_open_positions(CHAIN)
    assert counts["closed_take_profit"] == 1
    rows = await _rows()
    assert rows[0]["status"] == "closed"
    assert rows[0]["close_reason"] == "take_profit_25pct"
    assert rows[0]["pnl_pct"] >= shadow.TAKE_PROFIT_PCT


@pytest.mark.asyncio
async def test_position_stays_open_below_take_profit(monkeypatch):
    async def fake_trending(*a, **k):
        return TrendingPoolsResult(pools=[_pool()], available=True, error=None)

    async def fake_pairs(contract, *, chain="base"):
        return [_snapshot(price_usd=1.0)]

    monkeypatch.setattr(shadow.dexpaprika, "get_trending_pools", fake_trending)
    monkeypatch.setattr(shadow.dexscreener, "fetch_token_pairs", fake_pairs)
    await shadow.discover_and_record(CHAIN)

    async def fake_pairs_flat(contract, *, chain="base"):
        return [_snapshot(price_usd=1.05)]  # +5%, not enough

    monkeypatch.setattr(shadow.dexscreener, "fetch_token_pairs", fake_pairs_flat)
    counts = await shadow.advance_open_positions(CHAIN)
    assert counts["closed_take_profit"] == 0
    assert counts["closed_timeout"] == 0
    rows = await _rows()
    assert rows[0]["status"] == "open"


@pytest.mark.asyncio
async def test_timeout_closes_stale_position_without_a_stop_loss(monkeypatch):
    async def fake_trending(*a, **k):
        return TrendingPoolsResult(pools=[_pool()], available=True, error=None)

    async def fake_pairs(contract, *, chain="base"):
        return [_snapshot(price_usd=1.0)]

    monkeypatch.setattr(shadow.dexpaprika, "get_trending_pools", fake_trending)
    monkeypatch.setattr(shadow.dexscreener, "fetch_token_pairs", fake_pairs)
    await shadow.discover_and_record(CHAIN)

    async with aiosqlite.connect(shadow._db_path()) as db:
        await db.execute(
            "UPDATE dip_recovery_v2_shadow SET opened_at = ? WHERE contract = ?",
            ("2020-01-01T00:00:00+00:00", CONTRACT),
        )
        await db.commit()

    # Deep in the red, well below take-profit -- must time out, not stop out
    # (v2 has no stop-loss by design, unlike v1).
    async def fake_pairs_down(contract, *, chain="base"):
        return [_snapshot(price_usd=0.5)]

    monkeypatch.setattr(shadow.dexscreener, "fetch_token_pairs", fake_pairs_down)
    counts = await shadow.advance_open_positions(CHAIN)
    assert counts["closed_timeout"] == 1
    rows = await _rows()
    assert rows[0]["close_reason"] == "timeout_max_hold"
    assert rows[0]["pnl_pct"] < 0


@pytest.mark.asyncio
async def test_advance_position_never_fabricates_a_close_on_missing_snapshot(monkeypatch):
    async def fake_trending(*a, **k):
        return TrendingPoolsResult(pools=[_pool()], available=True, error=None)

    async def fake_pairs(contract, *, chain="base"):
        return [_snapshot(price_usd=1.0)]

    monkeypatch.setattr(shadow.dexpaprika, "get_trending_pools", fake_trending)
    monkeypatch.setattr(shadow.dexscreener, "fetch_token_pairs", fake_pairs)
    await shadow.discover_and_record(CHAIN)

    async def fake_pairs_empty(contract, *, chain="base"):
        return []

    monkeypatch.setattr(shadow.dexscreener, "fetch_token_pairs", fake_pairs_empty)
    counts = await shadow.advance_open_positions(CHAIN)
    assert counts["checked"] == 1
    assert counts["closed_take_profit"] == 0
    assert counts["closed_timeout"] == 0
    rows = await _rows()
    assert rows[0]["status"] == "open"


@pytest.mark.asyncio
async def test_exit_check_ignores_implausible_price_jump(monkeypatch):
    """Decision 2 -- same failure class confirmed live the same day on
    base_momentum_shadow.py (a corrupted AMM reserve-ratio price read as a
    huge nominal, never-executable gain). A 10,000x exit quote must never
    close this position as a phantom take-profit."""
    async def fake_trending(*a, **k):
        return TrendingPoolsResult(pools=[_pool()], available=True, error=None)

    async def fake_pairs_entry(contract, *, chain="base"):
        return [_snapshot(price_usd=1.0)]

    monkeypatch.setattr(shadow.dexpaprika, "get_trending_pools", fake_trending)
    monkeypatch.setattr(shadow.dexscreener, "fetch_token_pairs", fake_pairs_entry)
    await shadow.discover_and_record(CHAIN)
    rows = await _rows()
    entry_price = rows[0]["entry_price"]

    async def fake_pairs_corrupt(contract, *, chain="base"):
        return [_snapshot(price_usd=entry_price * 10_000.0)]

    monkeypatch.setattr(shadow.dexscreener, "fetch_token_pairs", fake_pairs_corrupt)
    counts = await shadow.advance_open_positions(CHAIN)
    assert counts["closed_take_profit"] == 0
    rows = await _rows()
    assert rows[0]["status"] == "open"


@pytest.mark.asyncio
async def test_exit_check_processes_price_just_under_sanity_bar_normally(monkeypatch):
    async def fake_trending(*a, **k):
        return TrendingPoolsResult(pools=[_pool()], available=True, error=None)

    async def fake_pairs_entry(contract, *, chain="base"):
        return [_snapshot(price_usd=1.0)]

    monkeypatch.setattr(shadow.dexpaprika, "get_trending_pools", fake_trending)
    monkeypatch.setattr(shadow.dexscreener, "fetch_token_pairs", fake_pairs_entry)
    await shadow.discover_and_record(CHAIN)
    rows = await _rows()
    entry_price = rows[0]["entry_price"]

    async def fake_pairs_high_but_plausible(contract, *, chain="base"):
        # Comfortably clears +25% (the take-profit target) while staying
        # well under EXIT_PRICE_SANITY_MULTIPLE -- must still close normally.
        return [_snapshot(price_usd=entry_price * (shadow.EXIT_PRICE_SANITY_MULTIPLE - 1.0))]

    monkeypatch.setattr(shadow.dexscreener, "fetch_token_pairs", fake_pairs_high_but_plausible)
    counts = await shadow.advance_open_positions(CHAIN)
    assert counts["closed_take_profit"] == 1
    rows = await _rows()
    assert rows[0]["close_reason"] == "take_profit_25pct"


# --- shadow_candle_archive wiring (26/08, operator-directed) ---------------

@pytest.mark.asyncio
async def test_open_position_archives_before_candles(monkeypatch):
    async def fake_trending(*a, **k):
        return TrendingPoolsResult(pools=[_pool()], available=True, error=None)

    async def fake_pairs(contract, *, chain="base"):
        return [_snapshot()]

    ohlcv = OHLCVResult(candles=[Candle(ts=1000, open=1.0, high=1.2, low=0.9, close=1.1, volume=5.0)], available=True)
    captured = []

    async def fake_get_ohlcv(pool_address, *, network="base", mode="standard"):
        return ohlcv

    async def fake_store_candles(**kwargs):
        captured.append(kwargs)
        return len(kwargs["candles"])

    monkeypatch.setattr(shadow.dexpaprika, "get_trending_pools", fake_trending)
    monkeypatch.setattr(shadow.dexscreener, "fetch_token_pairs", fake_pairs)
    monkeypatch.setattr(shadow.dexpaprika, "get_ohlcv", fake_get_ohlcv)
    monkeypatch.setattr(shadow_candle_archive, "store_candles", fake_store_candles)

    await shadow.discover_and_record(CHAIN)

    assert len(captured) == 1
    call = captured[0]
    assert call["module"] == "dip_recovery_v2"
    assert call["phase"] == "before"
    assert call["chain"] == CHAIN
    assert call["candles"] == ohlcv.candles
    rows = await _rows()
    assert call["position_id"] == rows[0]["id"]


@pytest.mark.asyncio
async def test_open_position_survives_candle_archive_failure(monkeypatch):
    async def fake_trending(*a, **k):
        return TrendingPoolsResult(pools=[_pool()], available=True, error=None)

    async def fake_pairs(contract, *, chain="base"):
        return [_snapshot()]

    async def fake_get_ohlcv(pool_address, *, network="base", mode="standard"):
        raise RuntimeError("boom")

    monkeypatch.setattr(shadow.dexpaprika, "get_trending_pools", fake_trending)
    monkeypatch.setattr(shadow.dexscreener, "fetch_token_pairs", fake_pairs)
    monkeypatch.setattr(shadow.dexpaprika, "get_ohlcv", fake_get_ohlcv)

    opened = await shadow.discover_and_record(CHAIN)
    assert opened == 1  # position still opens despite the archive call raising


@pytest.mark.asyncio
async def test_advance_open_position_archives_after_candles(monkeypatch):
    async def fake_trending(*a, **k):
        return TrendingPoolsResult(pools=[_pool()], available=True, error=None)

    async def fake_pairs(contract, *, chain="base"):
        return [_snapshot(price_usd=1.0)]

    async def fake_get_ohlcv_empty(pool_address, *, network="base", mode="standard"):
        return OHLCVResult(candles=[], available=False)

    monkeypatch.setattr(shadow.dexpaprika, "get_trending_pools", fake_trending)
    monkeypatch.setattr(shadow.dexscreener, "fetch_token_pairs", fake_pairs)
    monkeypatch.setattr(shadow.dexpaprika, "get_ohlcv", fake_get_ohlcv_empty)
    await shadow.discover_and_record(CHAIN)

    ohlcv = OHLCVResult(candles=[Candle(ts=2000, open=1.0, high=1.1, low=0.95, close=1.05, volume=3.0)], available=True)
    captured = []

    async def fake_get_ohlcv(pool_address, *, network="base", mode="standard"):
        return ohlcv

    async def fake_store_candles(**kwargs):
        captured.append(kwargs)
        return len(kwargs["candles"])

    monkeypatch.setattr(shadow.dexpaprika, "get_ohlcv", fake_get_ohlcv)
    monkeypatch.setattr(shadow_candle_archive, "store_candles", fake_store_candles)

    await shadow.advance_open_positions(CHAIN)

    assert len(captured) == 1
    call = captured[0]
    assert call["module"] == "dip_recovery_v2"
    assert call["phase"] == "after"
    assert call["chain"] == CHAIN
    assert call["candles"] == ohlcv.candles


# --- Telegram notifications (26/08, operator-directed) ---------------------

@pytest.mark.asyncio
async def test_pending_notifications_first_pass_only_anchors(monkeypatch):
    """Same doctrine as shadow_notify.notify_pocket(): the very first pass
    after a (re)start must never replay pre-existing history as if it just
    happened."""
    async def fake_trending(*a, **k):
        return TrendingPoolsResult(pools=[_pool()], available=True, error=None)

    async def fake_pairs(contract, *, chain="base"):
        return [_snapshot()]

    monkeypatch.setattr(shadow.dexpaprika, "get_trending_pools", fake_trending)
    monkeypatch.setattr(shadow.dexscreener, "fetch_token_pairs", fake_pairs)
    await shadow.discover_and_record(CHAIN)

    texts = await shadow.pending_notifications()
    assert texts == []


@pytest.mark.asyncio
async def test_calling_notifications_after_the_very_first_cycle_loses_that_cycles_opens(monkeypatch):
    """Documents the REAL bug found live 26/08 (8 real Base/Robinhood
    positions opened at deploy time, zero Telegram messages sent): calling
    pending_notifications() only AFTER run_cycle() means the very first
    call ever made anchors on MAX(id), which by then already includes
    whatever THIS SAME cycle just opened -- those positions are absorbed
    into the anchor and never notified. This test locks in the WRONG shape
    so a future refactor cannot silently reintroduce it -- the correct
    fix (heartbeat.py calling pending_notifications() once BEFORE
    run_cycle(), see the next test) is the one actually wired in prod."""
    async def fake_trending(*a, **k):
        return TrendingPoolsResult(pools=[_pool()], available=True, error=None)

    async def fake_pairs(contract, *, chain="base"):
        return [_snapshot()]

    monkeypatch.setattr(shadow.dexpaprika, "get_trending_pools", fake_trending)
    monkeypatch.setattr(shadow.dexscreener, "fetch_token_pairs", fake_pairs)

    # Wrong order: discover (opens a position) THEN notify for the first time.
    await shadow.discover_and_record(CHAIN)
    texts = await shadow.pending_notifications()
    assert texts == []  # the open from THIS very cycle is silently lost


@pytest.mark.asyncio
async def test_anchoring_before_the_first_cycle_still_reports_that_cycles_opens(monkeypatch):
    """The actual fix wired into heartbeat.py: call pending_notifications()
    once BEFORE the first run_cycle() (a pure anchor, since nothing exists
    yet) so a position opened during that very first cycle is, by
    construction, newer than the anchor and gets reported by the
    SECOND (post-cycle) call -- never silently absorbed."""
    async def fake_trending(*a, **k):
        return TrendingPoolsResult(pools=[_pool()], available=True, error=None)

    async def fake_pairs(contract, *, chain="base"):
        return [_snapshot()]

    monkeypatch.setattr(shadow.dexpaprika, "get_trending_pools", fake_trending)
    monkeypatch.setattr(shadow.dexscreener, "fetch_token_pairs", fake_pairs)

    # Correct order: anchor first (nothing to report yet, DB is empty).
    assert await shadow.pending_notifications() == []
    await shadow.discover_and_record(CHAIN)
    texts = await shadow.pending_notifications()
    assert len(texts) == 1
    assert "OUVERTURE" in texts[0]


@pytest.mark.asyncio
async def test_pending_notifications_reports_a_new_open(monkeypatch):
    async def fake_trending(*a, **k):
        return TrendingPoolsResult(pools=[_pool()], available=True, error=None)

    async def fake_pairs(contract, *, chain="base"):
        return [_snapshot()]

    monkeypatch.setattr(shadow.dexpaprika, "get_trending_pools", fake_trending)
    monkeypatch.setattr(shadow.dexscreener, "fetch_token_pairs", fake_pairs)

    await shadow.pending_notifications()  # anchors on zero positions

    await shadow.discover_and_record(CHAIN)
    texts = await shadow.pending_notifications()
    assert len(texts) == 1
    assert "OUVERTURE" in texts[0]
    assert CHAIN in texts[0]
    assert "Market cap" in texts[0]
    assert "Age du pool" in texts[0]

    # A second pass with nothing new must not re-report the same open.
    assert await shadow.pending_notifications() == []


@pytest.mark.asyncio
async def test_pending_notifications_reports_a_new_close_once(monkeypatch):
    async def fake_trending(*a, **k):
        return TrendingPoolsResult(pools=[_pool()], available=True, error=None)

    async def fake_pairs(contract, *, chain="base"):
        return [_snapshot(price_usd=1.0)]

    monkeypatch.setattr(shadow.dexpaprika, "get_trending_pools", fake_trending)
    monkeypatch.setattr(shadow.dexscreener, "fetch_token_pairs", fake_pairs)
    await shadow.pending_notifications()  # anchor
    await shadow.discover_and_record(CHAIN)
    await shadow.pending_notifications()  # consume the open

    rows = await _rows()

    async with aiosqlite.connect(shadow._db_path()) as db:
        await db.execute(
            "UPDATE dip_recovery_v2_shadow SET status='closed', closed_at=?, "
            "pnl_pct=?, close_reason=? WHERE id=?",
            (datetime.now(timezone.utc).isoformat(), 30.0, "take_profit_25pct", rows[0]["id"]),
        )
        await db.commit()

    texts = await shadow.pending_notifications()
    assert len(texts) == 1
    assert "CLOTURE" in texts[0]
    assert "take_profit_25pct" in texts[0]
    assert "+30.0%" in texts[0]

    # Never notified twice for the same close.
    assert await shadow.pending_notifications() == []


@pytest.mark.asyncio
async def test_pending_notifications_never_raises_on_db_failure(monkeypatch):
    monkeypatch.setattr(shadow, "DB_PATH", "/nonexistent/dir/shadow.db")
    shadow._ensured_db_paths.clear()
    texts = await shadow.pending_notifications()
    assert texts == []


# --- summary -----------------------------------------------------------

@pytest.mark.asyncio
async def test_summary_aggregates_open_closed_and_winrate(monkeypatch):
    async def fake_trending(*a, **k):
        return TrendingPoolsResult(pools=[_pool(token_address=CONTRACT, pool_address="0xPOOLA")], available=True, error=None)

    async def fake_pairs(contract, *, chain="base"):
        return [_snapshot(pair_address="0xPOOLA")]

    monkeypatch.setattr(shadow.dexpaprika, "get_trending_pools", fake_trending)
    monkeypatch.setattr(shadow.dexscreener, "fetch_token_pairs", fake_pairs)
    await shadow.discover_and_record(CHAIN)

    other_contract = "0x" + "e" * 40

    async def fake_trending_2(*a, **k):
        return TrendingPoolsResult(pools=[_pool(token_address=other_contract, pool_address="0xPOOLB")], available=True, error=None)

    async def fake_pairs_2(contract, *, chain="base"):
        return [_snapshot(pair_address="0xPOOLB")]

    monkeypatch.setattr(shadow.dexpaprika, "get_trending_pools", fake_trending_2)
    monkeypatch.setattr(shadow.dexscreener, "fetch_token_pairs", fake_pairs_2)
    await shadow.discover_and_record(CHAIN)

    summary = await shadow.summary()
    assert summary["open"] == 2
    assert summary["closed"] == 0
    assert summary["distinct_tokens"] == 2


# --- run_cycle wiring: both chains ---------------------------------------

@pytest.mark.asyncio
async def test_run_cycle_covers_both_chains(monkeypatch):
    seen_chains = []

    async def fake_trending(chain, **k):
        seen_chains.append(chain)
        return TrendingPoolsResult(pools=[], available=True, error=None)

    monkeypatch.setattr(shadow.dexpaprika, "get_trending_pools", fake_trending)
    stats = await shadow.run_cycle()
    assert seen_chains == list(shadow.CHAINS)
    assert stats["opened"] == 0
