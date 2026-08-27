"""Tests for onchain_pool_discovery.py -- specs/006-onchain-dayzero-entry.

Payload construction mirrors test_evm_swap_ws.py's own pattern (_FakeTopic,
raw event data built by hand from the real ABI layouts verified in
005-discovery-budget), so a decode bug here is caught the same way that
module's real bugs (missing "0x" prefix, v4 fan-out) were caught.
"""
from __future__ import annotations

import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, "packages/aria-core/src")

from aria_core.services import onchain_pool_discovery as m  # noqa: E402
from aria_core.services.geckoterminal import TrendingPool  # noqa: E402


class _FakeTopic:
    def __init__(self, raw: bytes) -> None:
        self._raw = raw

    def hex(self) -> str:
        return self._raw.hex()  # no "0x" prefix, like real HexBytes


TOKEN0 = "1111111111111111111111111111111111111111"
TOKEN1 = "2222222222222222222222222222222222222222"
WETH = "4200000000000000000000000000000000000006"
PAIR = "3333333333333333333333333333333333333333"
BASE_V2_FACTORY = "0x8909dc15e40173ff4699343b6eb8132c65e18ec6"
BASE_V3_FACTORY = "0x33128a8fc17869897dce68ed026d694621f6fdfd"
BASE_AERODROME_CLASSIC_FACTORY = "0x420dd381b31aef6683db6b902084cb0ffece40da"


def _addr_topic(addr_hex: str) -> _FakeTopic:
    return _FakeTopic(bytes.fromhex(addr_hex.replace("0x", "").rjust(64, "0")))


def _addr_word(addr_hex: str) -> bytes:
    return bytes.fromhex(addr_hex.replace("0x", "").rjust(64, "0"))


def _make_feed(chain: str = "base") -> m.OnChainPoolDiscoveryFeed:
    ws_feed = MagicMock()
    ws_feed._pool_manager_address.return_value = "0x498581ff718922c3f8e6a244956af099b2652b2b"
    ws_feed.add_pool = AsyncMock(return_value=True)
    feed = m.OnChainPoolDiscoveryFeed(chain=chain, ws_url="wss://test.invalid", ws_feed=ws_feed)
    return feed


@pytest.fixture(autouse=True)
def _no_real_symbol_resolution_calls(monkeypatch):
    """26/08 -- check_candidates() now resolves the qualified pool's symbol
    via dexpaprika._resolve_base_token (real HTTP call). Default to None
    (matches every pre-existing test's prior behavior) unless a test
    explicitly overrides this mock to exercise resolution itself."""
    async def _fake_resolve_base_token(network, pool_address):
        return None

    monkeypatch.setattr(m.dexpaprika, "_resolve_base_token", _fake_resolve_base_token)


# --- v2 PairCreated decode --------------------------------------------------

@pytest.mark.asyncio
async def test_v2_pair_created_registers_candidate_with_weth_quote():
    feed = _make_feed()
    topics = [_FakeTopic(bytes.fromhex(m._PAIR_CREATED_TOPIC[2:])),
              _addr_topic(TOKEN0), _addr_topic(WETH)]
    data = "0x" + (_addr_word(PAIR) + _addr_word("00")).hex()
    payload = {"result": {"address": BASE_V2_FACTORY, "topics": topics, "data": data}}
    feed._handle_notification(payload)
    assert f"0x{PAIR}" in feed._candidates
    cand = feed._candidates[f"0x{PAIR}"]
    assert cand.dex_id == "uniswap_v2"
    assert cand.token_address == f"0x{TOKEN0}"


@pytest.mark.asyncio
async def test_v2_pair_created_ignored_for_unknown_factory_address():
    feed = _make_feed()
    topics = [_FakeTopic(bytes.fromhex(m._PAIR_CREATED_TOPIC[2:])),
              _addr_topic(TOKEN0), _addr_topic(WETH)]
    data = "0x" + (_addr_word(PAIR) + _addr_word("00")).hex()
    payload = {"result": {"address": "0xdeadbeef00000000000000000000000000000000", "topics": topics, "data": data}}
    feed._handle_notification(payload)
    assert not feed._candidates


@pytest.mark.asyncio
async def test_v2_pair_created_skipped_when_neither_side_is_a_known_quote():
    feed = _make_feed()
    topics = [_FakeTopic(bytes.fromhex(m._PAIR_CREATED_TOPIC[2:])),
              _addr_topic(TOKEN0), _addr_topic(TOKEN1)]
    data = "0x" + (_addr_word(PAIR) + _addr_word("00")).hex()
    payload = {"result": {"address": BASE_V2_FACTORY, "topics": topics, "data": data}}
    feed._handle_notification(payload)
    assert not feed._candidates


# --- 27/08, diagnostic counters (backlog: Base went silent post-fix) -------
# Real incident: after the dict-mutation race was fixed, Base STAYED at zero
# new candidates for 48min+ of clean observation with zero errors logged --
# indistinguishable from the outside whether the WS received nothing, or
# received plenty the quote-token filter rejected. These counters exist to
# tell the two apart from a log line alone, no behaviour change.

@pytest.mark.asyncio
async def test_raw_notifications_seen_counts_every_notification_with_a_result():
    feed = _make_feed()
    topics = [_FakeTopic(bytes.fromhex(m._PAIR_CREATED_TOPIC[2:])),
              _addr_topic(TOKEN0), _addr_topic(TOKEN1)]
    data = "0x" + (_addr_word(PAIR) + _addr_word("00")).hex()
    payload = {"result": {"address": BASE_V2_FACTORY, "topics": topics, "data": data}}

    assert feed.raw_notifications_seen == 0
    feed._handle_notification(payload)
    feed._handle_notification(payload)
    assert feed.raw_notifications_seen == 2


@pytest.mark.asyncio
async def test_rejected_not_priceable_count_tracks_the_quote_filter_only():
    feed = _make_feed()
    unpriceable_topics = [_FakeTopic(bytes.fromhex(m._PAIR_CREATED_TOPIC[2:])),
                          _addr_topic(TOKEN0), _addr_topic(TOKEN1)]
    unpriceable_data = "0x" + (_addr_word(PAIR) + _addr_word("00")).hex()
    unpriceable_payload = {"result": {"address": BASE_V2_FACTORY,
                                      "topics": unpriceable_topics, "data": unpriceable_data}}
    feed._handle_notification(unpriceable_payload)
    assert feed.rejected_not_priceable_count == 1
    assert not feed._candidates

    # Reuses the exact same WETH-quote scenario as
    # test_v2_pair_created_registers_candidate_with_weth_quote -- a real
    # accept must never touch this counter.
    priceable_topics = [_FakeTopic(bytes.fromhex(m._PAIR_CREATED_TOPIC[2:])),
                        _addr_topic(TOKEN0), _addr_topic(WETH)]
    priceable_data = "0x" + (_addr_word(PAIR) + _addr_word("00")).hex()
    priceable_payload = {"result": {"address": BASE_V2_FACTORY,
                                    "topics": priceable_topics, "data": priceable_data}}
    feed._handle_notification(priceable_payload)
    assert feed.rejected_not_priceable_count == 1, "an accepted candidate must not bump this counter"


# --- 27/08, real gap found live: Robinhood's own quote tokens were never in
# the (Base-only) filter, so every single Robinhood notification was rejected
# as unpriceable -- 1095/1095 in a real 24h prod sample, not a partial miss.
# Addresses verified against robinhoodchain.blockscout.com's live token
# listing (WETH: 505,469 holders; USDG: 104,054 holders -- both dominant by
# a wide margin over the next candidate).

ROBINHOOD_V2_FACTORY = "0x8bceaa40b9acdfaedf85adf4ff01f5ad6517937f"
ROBINHOOD_WETH = "0bd7d308f8e1639fab988df18a8011f41eacad73"
ROBINHOOD_USDG = "5fc5360d0400a0fd4f2af552add042d716f1d168"


@pytest.mark.asyncio
async def test_robinhood_pair_created_registers_candidate_with_weth_quote():
    feed = _make_feed(chain="robinhood")
    topics = [_FakeTopic(bytes.fromhex(m._PAIR_CREATED_TOPIC[2:])),
              _addr_topic(TOKEN0), _addr_topic(ROBINHOOD_WETH)]
    data = "0x" + (_addr_word(PAIR) + _addr_word("00")).hex()
    payload = {"result": {"address": ROBINHOOD_V2_FACTORY, "topics": topics, "data": data}}
    feed._handle_notification(payload)
    assert f"0x{PAIR}" in feed._candidates
    assert feed.rejected_not_priceable_count == 0


@pytest.mark.asyncio
async def test_robinhood_pair_created_registers_candidate_with_usdg_quote():
    feed = _make_feed(chain="robinhood")
    topics = [_FakeTopic(bytes.fromhex(m._PAIR_CREATED_TOPIC[2:])),
              _addr_topic(TOKEN0), _addr_topic(ROBINHOOD_USDG)]
    data = "0x" + (_addr_word(PAIR) + _addr_word("00")).hex()
    payload = {"result": {"address": ROBINHOOD_V2_FACTORY, "topics": topics, "data": data}}
    feed._handle_notification(payload)
    assert f"0x{PAIR}" in feed._candidates
    assert feed.rejected_not_priceable_count == 0


# --- 27/08, real gap found live: cbBTC confirmed as a real, actively-used
# Base quote token (GeckoTerminal live top-pools listing shows genuine
# third-party tokens quoted directly against it, e.g. "SOL/cbBTC") but was
# entirely absent from the discovery filter -- Base's own ~94% rejection
# rate (measured live 19:07-19:08 UTC) was partly this gap.

CBBTC = "cbb7c0000ab88b473b1f5afd9ef808440eed33bf"


@pytest.mark.asyncio
async def test_base_pair_created_registers_candidate_with_cbbtc_quote():
    feed = _make_feed()
    topics = [_FakeTopic(bytes.fromhex(m._PAIR_CREATED_TOPIC[2:])),
              _addr_topic(TOKEN0), _addr_topic(CBBTC)]
    data = "0x" + (_addr_word(PAIR) + _addr_word("00")).hex()
    payload = {"result": {"address": BASE_V2_FACTORY, "topics": topics, "data": data}}
    feed._handle_notification(payload)
    assert f"0x{PAIR}" in feed._candidates
    assert feed.rejected_not_priceable_count == 0


# --- Aerodrome Classic PoolCreated decode (specs/011, 26/08) ----------------
# `event PoolCreated(address indexed token0, address indexed token1, bool
# indexed stable, address pool, uint256)` -- `pool` is the FIRST word of the
# non-indexed data, same layout as PairCreated's own pair address (unlike
# the V3-style topics below, where pool is the LAST word).

@pytest.mark.asyncio
async def test_aerodrome_classic_pool_created_registers_candidate_with_weth_quote():
    feed = _make_feed()
    topics = [_FakeTopic(bytes.fromhex(m._POOL_CREATED_AERODROME_CLASSIC_TOPIC[2:])),
              _addr_topic(TOKEN0), _addr_topic(WETH), _addr_topic("00")]
    data = "0x" + (_addr_word(PAIR) + _addr_word("00")).hex()
    payload = {"result": {"address": BASE_AERODROME_CLASSIC_FACTORY, "topics": topics, "data": data}}
    feed._handle_notification(payload)
    assert f"0x{PAIR}" in feed._candidates
    cand = feed._candidates[f"0x{PAIR}"]
    assert cand.dex_id == "aerodrome"
    assert cand.token_address == f"0x{TOKEN0}"


@pytest.mark.asyncio
async def test_aerodrome_classic_pool_created_ignored_for_unknown_factory_address():
    feed = _make_feed()
    topics = [_FakeTopic(bytes.fromhex(m._POOL_CREATED_AERODROME_CLASSIC_TOPIC[2:])),
              _addr_topic(TOKEN0), _addr_topic(WETH), _addr_topic("00")]
    data = "0x" + (_addr_word(PAIR) + _addr_word("00")).hex()
    payload = {"result": {"address": "0xdeadbeef00000000000000000000000000000000", "topics": topics, "data": data}}
    feed._handle_notification(payload)
    assert not feed._candidates


@pytest.mark.asyncio
async def test_aerodrome_classic_pool_created_skipped_when_neither_side_is_a_known_quote():
    feed = _make_feed()
    topics = [_FakeTopic(bytes.fromhex(m._POOL_CREATED_AERODROME_CLASSIC_TOPIC[2:])),
              _addr_topic(TOKEN0), _addr_topic(TOKEN1), _addr_topic("00")]
    data = "0x" + (_addr_word(PAIR) + _addr_word("00")).hex()
    payload = {"result": {"address": BASE_AERODROME_CLASSIC_FACTORY, "topics": topics, "data": data}}
    feed._handle_notification(payload)
    assert not feed._candidates


# --- v3-style PoolCreated decode (Uniswap V3 / PancakeSwap V3) --------------

@pytest.mark.asyncio
async def test_v3_pool_created_registers_candidate_pool_address_from_last_word():
    feed = _make_feed()
    topics = [_FakeTopic(bytes.fromhex(m._POOL_CREATED_V3_TOPIC[2:])),
              _addr_topic(TOKEN0), _addr_topic(WETH)]
    tick_spacing_word = (60).to_bytes(32, "big")
    data = "0x" + (tick_spacing_word + _addr_word(PAIR)).hex()
    payload = {"result": {"address": BASE_V3_FACTORY, "topics": topics, "data": data}}
    feed._handle_notification(payload)
    assert f"0x{PAIR}" in feed._candidates
    assert feed._candidates[f"0x{PAIR}"].dex_id == "uniswap_v3"


# --- v4 Initialize decode ----------------------------------------------------

@pytest.mark.asyncio
async def test_v4_initialize_registers_candidate_by_pool_id():
    feed = _make_feed()
    pool_id = "aa" * 32
    topics = [_FakeTopic(bytes.fromhex(m._INITIALIZE_V4_TOPIC[2:])), _FakeTopic(bytes.fromhex(pool_id))]
    data = "0x" + (
        _addr_word(TOKEN0) + _addr_word(WETH) + b"\x00" * 32 + b"\x00" * 32
        + b"\x00" * 32 + b"\x00" * 32 + b"\x00" * 32
    ).hex()
    payload = {"result": {"address": "0x498581ff718922c3f8e6a244956af099b2652b2b", "topics": topics, "data": data}}
    feed._handle_notification(payload)
    assert f"0x{pool_id}" in feed._candidates
    cand = feed._candidates[f"0x{pool_id}"]
    assert cand.dex_id == "uniswap_v4"
    assert cand.token_address == f"0x{TOKEN0}"


@pytest.mark.asyncio
async def test_v4_initialize_calls_add_pool_with_currency_literal_not_address():
    """evm_swap_ws._add_pool_v4 trusts `token_address` to literally be the
    string "currency0"/"currency1" for v4 (no separate pool contract to
    introspect) -- a real address there would silently mis-track which side
    is the tracked token. This is the one real gotcha found while wiring
    this module -- worth a dedicated regression test."""
    feed = _make_feed()
    pool_id = "bb" * 32
    topics = [_FakeTopic(bytes.fromhex(m._INITIALIZE_V4_TOPIC[2:])), _FakeTopic(bytes.fromhex(pool_id))]
    data = "0x" + (
        _addr_word(TOKEN0) + _addr_word(WETH) + b"\x00" * 32 * 5
    ).hex()
    payload = {"result": {"address": "0x498581ff718922c3f8e6a244956af099b2652b2b", "topics": topics, "data": data}}
    feed._handle_notification(payload)
    import asyncio
    await asyncio.sleep(0)  # let the fire-and-forget add_pool task run
    feed._ws_feed.add_pool.assert_awaited_once()
    _, kwargs = feed._ws_feed.add_pool.call_args
    assert kwargs["token_address"] == "currency0"


# --- check_candidates: liquidity qualification / TTL drop -------------------

@pytest.mark.asyncio
async def test_check_candidates_qualifies_a_pool_with_exact_v2_stable_reserve():
    feed = _make_feed()
    feed._candidates["0xpool"] = m._Candidate(
        pool_key="0xpool", dex_id="uniswap_v2", token_address="0xtoken", chain="base",
    )
    snapshot = MagicMock(available=True, reserve_usd=9000.0, quote_is_weth=False, price_quote=0.001)
    feed._ws_feed.get_snapshot.return_value = snapshot
    result = await feed.check_candidates(min_liquidity_usd=4000.0)
    assert len(result) == 1
    assert isinstance(result[0], TrendingPool)
    assert result[0].reserve_usd == 9000.0
    assert result[0].price_usd == 0.001
    assert "0xpool" not in feed._candidates  # qualified candidate is removed


@pytest.mark.asyncio
async def test_check_candidates_logs_diagnostic_counters_every_cycle(caplog):
    """27/08 -- one line per cycle so a silent Base (or Robinhood) can be
    diagnosed from logs alone: raw_notifications_seen distinguishes "the WS
    never received anything" from "it did, but the quote-token filter ate
    it all" (rejected_not_priceable), which look identical without this."""
    import logging

    feed = _make_feed()
    feed.raw_notifications_seen = 5
    feed.rejected_not_priceable_count = 3

    with caplog.at_level(logging.INFO):
        await feed.check_candidates(min_liquidity_usd=4000.0)

    assert any(
        "raw_notifications_seen=5" in r.message and "rejected_not_priceable=3" in r.message
        for r in caplog.records
    )


@pytest.mark.asyncio
async def test_check_candidates_resolves_symbol_for_a_qualified_pool(monkeypatch):
    """26/08 -- real bug fixed: a day-zero on-chain PairCreated/Initialize
    event carries no ERC-20 symbol metadata, so every qualified candidate
    used to log with symbol=None ("?" in Telegram notifications, confirmed
    live on base_momentum_shadow.py). Resolved here (bounded to QUALIFIED
    candidates only, funnel doctrine) via the same pool-detail call
    dexpaprika.py already makes for its own REST-sourced TrendingPool."""
    feed = _make_feed()
    feed._candidates["0xpool"] = m._Candidate(
        pool_key="0xpool", dex_id="uniswap_v2", token_address="0xtoken", chain="base",
    )
    snapshot = MagicMock(available=True, reserve_usd=9000.0, quote_is_weth=False, price_quote=0.001)
    feed._ws_feed.get_snapshot.return_value = snapshot

    async def _fake_resolve_base_token(network, pool_address):
        assert pool_address == "0xpool"
        return ("0xtoken", "MYTOKEN", None)

    monkeypatch.setattr(m.dexpaprika, "_resolve_base_token", _fake_resolve_base_token)
    result = await feed.check_candidates(min_liquidity_usd=4000.0)
    assert len(result) == 1
    assert result[0].symbol == "MYTOKEN"


@pytest.mark.asyncio
async def test_check_candidates_survives_concurrent_registration_mid_iteration(monkeypatch):
    """27/08, real incident: `check_candidates()` awaits per-candidate
    (dexpaprika resolution here), and `_register_candidate` -- called
    synchronously from the WS notification handler -- can insert a new key
    into `self._candidates` during that await, since both run on the same
    event loop. Iterating the live dict then raised "dictionary changed
    size during iteration", confirmed live on base_discovery_loop (186
    consecutive failures, ~36h with zero new Base candidates). Reproduces
    the race directly: the symbol-resolution await is the trigger point a
    concurrent WS notification would land in."""
    feed = _make_feed()
    feed._candidates["0xpool"] = m._Candidate(
        pool_key="0xpool", dex_id="uniswap_v2", token_address="0xtoken", chain="base",
    )
    snapshot = MagicMock(available=True, reserve_usd=9000.0, quote_is_weth=False, price_quote=0.001)
    feed._ws_feed.get_snapshot.return_value = snapshot

    async def _resolve_and_register_concurrently(network, pool_address):
        # Simulates a WS notification landing mid-await, exactly like
        # _handle_notification -> _register_candidate would in production.
        feed._candidates["0xnewpool"] = m._Candidate(
            pool_key="0xnewpool", dex_id="uniswap_v2", token_address="0xother", chain="base",
        )
        return None

    monkeypatch.setattr(m.dexpaprika, "_resolve_base_token", _resolve_and_register_concurrently)
    result = await feed.check_candidates(min_liquidity_usd=4000.0)  # must not raise
    assert len(result) == 1
    assert result[0].pool_address == "0xpool"
    # The concurrently-registered candidate is untouched, picked up next pass.
    assert "0xnewpool" in feed._candidates


@pytest.mark.asyncio
async def test_check_candidates_symbol_stays_none_on_resolution_failure():
    """Never fabricated -- a failed/unavailable pool-detail lookup leaves
    symbol=None rather than guessing, same dome doctrine as every other
    field in this module."""
    feed = _make_feed()
    feed._candidates["0xpool"] = m._Candidate(
        pool_key="0xpool", dex_id="uniswap_v2", token_address="0xtoken", chain="base",
    )
    snapshot = MagicMock(available=True, reserve_usd=9000.0, quote_is_weth=False, price_quote=0.001)
    feed._ws_feed.get_snapshot.return_value = snapshot
    result = await feed.check_candidates(min_liquidity_usd=4000.0)  # fixture default: resolves to None
    assert len(result) == 1
    assert result[0].symbol is None


@pytest.mark.asyncio
async def test_check_candidates_rejects_below_liquidity_floor():
    feed = _make_feed()
    feed._candidates["0xpool"] = m._Candidate(
        pool_key="0xpool", dex_id="uniswap_v2", token_address="0xtoken", chain="base",
    )
    snapshot = MagicMock(available=True, reserve_usd=500.0, quote_is_weth=False, price_quote=0.001)
    feed._ws_feed.get_snapshot.return_value = snapshot
    result = await feed.check_candidates(min_liquidity_usd=4000.0)
    assert result == []
    assert "0xpool" in feed._candidates  # stays pending, not dropped yet


@pytest.mark.asyncio
async def test_check_candidates_drops_after_observation_window_and_counts_it():
    feed = _make_feed()
    old_candidate = m._Candidate(
        pool_key="0xstale", dex_id="uniswap_v2", token_address="0xtoken", chain="base",
    )
    old_candidate.discovered_at -= m._OBSERVATION_WINDOW_SECONDS + 1
    feed._candidates["0xstale"] = old_candidate
    feed._ws_feed.get_snapshot.return_value = MagicMock(available=False)
    result = await feed.check_candidates(min_liquidity_usd=4000.0)
    assert result == []
    assert "0xstale" not in feed._candidates
    assert feed.dropped_count == 1


@pytest.mark.asyncio
async def test_check_candidates_falls_back_to_dexpaprika_for_v4_liquidity(monkeypatch):
    feed = _make_feed()
    feed._candidates["0xpoolid"] = m._Candidate(
        pool_key="0xpoolid", dex_id="uniswap_v4", token_address="0xtoken", chain="base",
    )
    snapshot = MagicMock(available=True, reserve_usd=None, quote_is_weth=False, price_quote=None)
    feed._ws_feed.get_snapshot.return_value = snapshot

    async def _fake_get_pool_reserve_usd(pool_address, *, network):
        return 15000.0

    async def _fake_get_json(path, *, params):
        return {"price_usd": 0.002}, None

    monkeypatch.setattr(m.dexpaprika, "get_pool_reserve_usd", _fake_get_pool_reserve_usd)
    monkeypatch.setattr(m.dexpaprika, "_get_json", _fake_get_json)
    result = await feed.check_candidates(min_liquidity_usd=4000.0)
    assert len(result) == 1
    assert result[0].reserve_usd == 15000.0
    assert result[0].price_usd == 0.002


@pytest.mark.asyncio
async def test_check_candidates_never_requeries_dexpaprika_within_recheck_interval(monkeypatch):
    feed = _make_feed()
    feed._candidates["0xpoolid"] = m._Candidate(
        pool_key="0xpoolid", dex_id="uniswap_v4", token_address="0xtoken", chain="base",
    )
    snapshot = MagicMock(available=True, reserve_usd=None, quote_is_weth=False, price_quote=None)
    feed._ws_feed.get_snapshot.return_value = snapshot
    calls = []

    async def _fake_get_pool_reserve_usd(pool_address, *, network):
        calls.append(1)
        return None

    monkeypatch.setattr(m.dexpaprika, "get_pool_reserve_usd", _fake_get_pool_reserve_usd)
    await feed.check_candidates(min_liquidity_usd=4000.0)
    await feed.check_candidates(min_liquidity_usd=4000.0)
    assert len(calls) == 1  # second call within _RECHECK_INTERVAL_SECONDS is skipped


@pytest.mark.asyncio
async def test_check_candidates_falls_back_to_dexpaprika_when_add_pool_never_succeeded(monkeypatch):
    """26/08 real incident: a fresh day-zero pool whose add_pool() verify
    call failed (RPC hadn't indexed the block yet -- "no such table"-style
    race) used to `continue` straight past the REST fallback below, forever
    -- zero real Base candidates got through despite the pool genuinely
    qualifying. `available=False` must fall through to the SAME fallback
    path a v3/v4 pool (which never gets a WS reserve_usd either) already
    uses, not be treated as a dead end."""
    feed = _make_feed()
    feed._candidates["0xpoolid"] = m._Candidate(
        pool_key="0xpoolid", dex_id="uniswap_v2", token_address="0xtoken", chain="base",
    )
    feed._ws_feed.get_snapshot.return_value = MagicMock(
        available=False, reserve_usd=None, quote_is_weth=False, price_quote=None,
    )
    feed._ws_feed.add_pool = AsyncMock(return_value=False)

    async def _fake_get_pool_reserve_usd(pool_address, *, network):
        return 15000.0

    async def _fake_get_json(path, *, params):
        return {"price_usd": 0.002}, None

    monkeypatch.setattr(m.dexpaprika, "get_pool_reserve_usd", _fake_get_pool_reserve_usd)
    monkeypatch.setattr(m.dexpaprika, "_get_json", _fake_get_json)
    result = await feed.check_candidates(min_liquidity_usd=4000.0)
    assert len(result) == 1
    assert result[0].reserve_usd == 15000.0
    assert result[0].price_usd == 0.002


@pytest.mark.asyncio
async def test_check_candidates_retries_add_pool_when_snapshot_unavailable(monkeypatch):
    """The other half of the same fix: a retry gives the WS feed a real
    chance to take over on a later pass once the RPC catches up, instead of
    depending solely on the REST fallback forever."""
    feed = _make_feed()
    feed._candidates["0xpoolid"] = m._Candidate(
        pool_key="0xpoolid", dex_id="uniswap_v2", token_address="0xtoken", chain="base",
    )
    feed._ws_feed.get_snapshot.return_value = MagicMock(
        available=False, reserve_usd=None, quote_is_weth=False, price_quote=None,
    )
    add_pool_calls = []

    async def _fake_add_pool(pool_address, *, dex_id, token_address):
        add_pool_calls.append(pool_address)
        return False

    feed._ws_feed.add_pool = _fake_add_pool
    monkeypatch.setattr(m.dexpaprika, "get_pool_reserve_usd", AsyncMock(return_value=None))
    await feed.check_candidates(min_liquidity_usd=4000.0)
    import asyncio
    await asyncio.sleep(0)  # let the fire-and-forget create_task actually run
    assert add_pool_calls == ["0xpoolid"]
