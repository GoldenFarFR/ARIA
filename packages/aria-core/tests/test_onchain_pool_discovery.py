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
    # specs/015-robinhood-chainstack-only -- replaces the old dexpaprika
    # fallback mocks. Default to "nothing resolves" (matches every
    # pre-existing test's prior behavior with the old fixture) unless a
    # test explicitly overrides these to exercise the cold-read path.
    ws_feed.resolve_cold = AsyncMock(return_value=MagicMock(
        available=False, reserve_usd=None, price_usd=None, price_quote=None, quote_is_weth=False,
    ))
    ws_feed.resolve_token_symbol = AsyncMock(return_value=None)
    feed = m.OnChainPoolDiscoveryFeed(chain=chain, ws_url="wss://test.invalid", ws_feed=ws_feed)
    return feed


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
async def test_check_candidates_logs_event_provenance_when_qualified_from_websocket(caplog):
    """28/08, operator-directed post-deploy observation ask -- distinguish a
    real decoded Sync/Swap event from a cold eth_call read in the logs,
    without a DB schema change."""
    import logging

    feed = _make_feed()
    feed._candidates["0xpool"] = m._Candidate(
        pool_key="0xpool", dex_id="uniswap_v2", token_address="0xtoken", chain="base",
    )
    snapshot = MagicMock(available=True, reserve_usd=9000.0, quote_is_weth=False, price_quote=0.001)
    feed._ws_feed.get_snapshot.return_value = snapshot
    with caplog.at_level(logging.INFO):
        await feed.check_candidates(min_liquidity_usd=4000.0)
    assert any("qualified 0xpool via event" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_check_candidates_logs_cold_read_provenance_when_qualified_from_resolve_cold(caplog):
    import logging

    feed = _make_feed()
    feed._candidates["0xpool"] = m._Candidate(
        pool_key="0xpool", dex_id="uniswap_v2", token_address="0xtoken", chain="base",
    )
    feed._ws_feed.get_snapshot.return_value = MagicMock(
        available=False, reserve_usd=None, quote_is_weth=False, price_quote=None,
    )
    feed._ws_feed.resolve_cold = AsyncMock(return_value=MagicMock(
        available=True, price_usd=2.0, reserve_usd=9000.0, quote_is_weth=False,
    ))
    with caplog.at_level(logging.INFO):
        result = await feed.check_candidates(min_liquidity_usd=4000.0)
    assert len(result) == 1
    assert any("qualified 0xpool via cold_read" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_check_candidates_converts_weth_reserve_via_live_snapshot(monkeypatch):
    """28/08, specs/015-robinhood-chainstack-only -- real finding (T023/T024
    worked example): 11/11 real Robinhood pools sampled were WETH-quoted,
    and reserve_usd was NEVER converted for them (only price_usd was),
    permanently blocking every one from qualifying regardless of real
    liquidity. Reuses the SAME eth_usd_rate() call already made for
    price_usd, never a second network call."""
    async def fake_eth_usd_rate():
        return 3000.0

    monkeypatch.setattr("aria_core.services.doppler.eth_usd_rate", fake_eth_usd_rate)
    feed = _make_feed()
    feed._candidates["0xpool"] = m._Candidate(
        pool_key="0xpool", dex_id="uniswap_v2", token_address="0xtoken", chain="base",
    )
    snapshot = MagicMock(
        available=True, reserve_usd=None, quote_is_weth=True, price_quote=0.001, quote_reserve_raw=5.0,
    )
    feed._ws_feed.get_snapshot.return_value = snapshot
    result = await feed.check_candidates(min_liquidity_usd=4000.0)
    assert len(result) == 1
    assert result[0].reserve_usd == pytest.approx(30000.0)  # 2 * 5.0 WETH * 3000
    assert result[0].price_usd == pytest.approx(3.0)  # 0.001 * 3000


@pytest.mark.asyncio
async def test_check_candidates_converts_weth_reserve_via_cold_read(monkeypatch):
    async def fake_eth_usd_rate():
        return 2000.0

    monkeypatch.setattr("aria_core.services.doppler.eth_usd_rate", fake_eth_usd_rate)
    feed = _make_feed()
    feed._candidates["0xpool"] = m._Candidate(
        pool_key="0xpool", dex_id="uniswap_v2", token_address="0xtoken", chain="base",
    )
    feed._ws_feed.get_snapshot.return_value = MagicMock(
        available=False, reserve_usd=None, quote_is_weth=False, price_quote=None,
    )
    feed._ws_feed.resolve_cold = AsyncMock(return_value=MagicMock(
        available=True, price_usd=None, price_quote=0.0004, reserve_usd=None,
        quote_reserve_raw=8.0, quote_is_weth=True,
    ))
    result = await feed.check_candidates(min_liquidity_usd=4000.0)
    assert len(result) == 1
    assert result[0].reserve_usd == pytest.approx(32000.0)  # 2 * 8.0 WETH * 2000
    assert result[0].price_usd == pytest.approx(0.8)  # 0.0004 * 2000


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
    candidates only, funnel doctrine) via a direct on-chain symbol() read
    (specs/015-robinhood-chainstack-only -- replaces the old DexPaprika
    pool-detail call)."""
    feed = _make_feed()
    feed._candidates["0xpool"] = m._Candidate(
        pool_key="0xpool", dex_id="uniswap_v2", token_address="0xtoken", chain="base",
    )
    snapshot = MagicMock(available=True, reserve_usd=9000.0, quote_is_weth=False, price_quote=0.001)
    feed._ws_feed.get_snapshot.return_value = snapshot

    async def _fake_resolve_token_symbol(token_address):
        assert token_address == "0xtoken"
        return "MYTOKEN"

    feed._ws_feed.resolve_token_symbol = AsyncMock(side_effect=_fake_resolve_token_symbol)
    result = await feed.check_candidates(min_liquidity_usd=4000.0)
    assert len(result) == 1
    assert result[0].symbol == "MYTOKEN"


@pytest.mark.asyncio
async def test_check_candidates_survives_concurrent_registration_mid_iteration(monkeypatch):
    """27/08, real incident: `check_candidates()` awaits per-candidate
    (on-chain symbol resolution here), and `_register_candidate` -- called
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

    async def _resolve_and_register_concurrently(token_address):
        # Simulates a WS notification landing mid-await, exactly like
        # _handle_notification -> _register_candidate would in production.
        feed._candidates["0xnewpool"] = m._Candidate(
            pool_key="0xnewpool", dex_id="uniswap_v2", token_address="0xother", chain="base",
        )
        return None

    feed._ws_feed.resolve_token_symbol = AsyncMock(side_effect=_resolve_and_register_concurrently)
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


# --- log-only liquidity/price observation (29/08, operator-directed) -------
# discovery_liquidity_observation.record_observation is mocked here rather
# than exercised end-to-end (see test_discovery_liquidity_observation.py for
# the real-DB tests) -- these tests confirm check_candidates' CONTRACT with
# it: called with the right values at the right verdict, and -- the whole
# point -- the qualification decision itself is byte-identical to before
# this instrumentation existed.

@pytest.mark.asyncio
async def test_a_below_floor_candidate_is_observed_with_its_real_reserve(monkeypatch):
    from aria_core import discovery_liquidity_observation

    recorded = []

    async def _capture(**kwargs):
        recorded.append(kwargs)

    monkeypatch.setattr(discovery_liquidity_observation, "record_observation", _capture)

    feed = _make_feed()
    feed._candidates["0xpool"] = m._Candidate(
        pool_key="0xpool", dex_id="uniswap_v2", token_address="0xtoken", chain="base",
    )
    snapshot = MagicMock(available=True, reserve_usd=50.0, quote_is_weth=False, price_quote=0.001)
    feed._ws_feed.get_snapshot.return_value = snapshot
    result = await feed.check_candidates(min_liquidity_usd=200.0)

    # Same decision as test_check_candidates_rejects_below_liquidity_floor,
    # unchanged by the new observation call.
    assert result == []
    assert "0xpool" in feed._candidates

    assert len(recorded) == 1
    obs = recorded[0]
    assert obs["chain"] == "base"
    assert obs["pool_address"] == "0xpool"
    assert obs["token_address"] == "0xtoken"
    assert obs["reserve_usd"] == 50.0
    assert obs["price_usd"] == 0.001
    assert obs["min_liquidity_usd"] == 200.0
    assert obs["source"] == "event"
    assert obs["qualified"] is False


@pytest.mark.asyncio
async def test_a_qualified_candidate_is_observed_alongside_the_qualification(monkeypatch):
    from aria_core import discovery_liquidity_observation

    recorded = []

    async def _capture(**kwargs):
        recorded.append(kwargs)

    monkeypatch.setattr(discovery_liquidity_observation, "record_observation", _capture)

    feed = _make_feed()
    feed._candidates["0xpool"] = m._Candidate(
        pool_key="0xpool", dex_id="uniswap_v2", token_address="0xtoken", chain="base",
    )
    snapshot = MagicMock(available=True, reserve_usd=9000.0, quote_is_weth=False, price_quote=0.001)
    feed._ws_feed.get_snapshot.return_value = snapshot
    result = await feed.check_candidates(min_liquidity_usd=4000.0)

    # Same decision as test_check_candidates_qualifies_a_pool_with_exact_v2_stable_reserve.
    assert len(result) == 1
    assert isinstance(result[0], TrendingPool)
    assert result[0].reserve_usd == 9000.0

    assert len(recorded) == 1
    obs = recorded[0]
    assert obs["reserve_usd"] == 9000.0
    assert obs["price_usd"] == 0.001
    assert obs["source"] == "event"
    assert obs["qualified"] is True


@pytest.mark.asyncio
async def test_an_unpriceable_candidate_is_observed_with_explicit_nones(monkeypatch):
    """Neither the websocket nor the cold read resolved anything this
    cycle -- the operator's explicit ask: None must stay None, never
    collapsed to 0."""
    from aria_core import discovery_liquidity_observation

    recorded = []

    async def _capture(**kwargs):
        recorded.append(kwargs)

    monkeypatch.setattr(discovery_liquidity_observation, "record_observation", _capture)

    feed = _make_feed()
    feed._candidates["0xpool"] = m._Candidate(
        pool_key="0xpool", dex_id="uniswap_v2", token_address="0xtoken", chain="base",
    )
    feed._ws_feed.get_snapshot.return_value = MagicMock(
        available=True, reserve_usd=None, quote_is_weth=False, price_quote=None,
    )
    # _make_feed's default resolve_cold already returns available=False.
    result = await feed.check_candidates(min_liquidity_usd=200.0)

    assert result == []
    assert feed.not_yet_priceable_count == 1  # unchanged existing counter

    assert len(recorded) == 1
    obs = recorded[0]
    assert obs["reserve_usd"] is None
    assert obs["price_usd"] is None
    assert obs["source"] is None
    assert obs["qualified"] is False


@pytest.mark.asyncio
async def test_activity_observation_is_recorded_alongside_qualification_unchanged(monkeypatch):
    """29/08, operator-directed -- point 6 of the activity-observation ask:
    wiring onchain_activity_observation must never change the qualification
    decision (same scenario/assertions as
    test_a_qualified_candidate_is_observed_alongside_the_qualification)."""
    from aria_core import onchain_activity_observation

    recorded = []

    async def _capture(**kwargs):
        recorded.append(kwargs)

    monkeypatch.setattr(onchain_activity_observation, "record_observation", _capture)

    feed = _make_feed()
    feed._candidates["0xpool"] = m._Candidate(
        pool_key="0xpool", dex_id="uniswap_v2", token_address="0xtoken", chain="base",
    )
    snapshot = MagicMock(
        available=True, reserve_usd=9000.0, quote_is_weth=False, price_quote=0.001,
        family="v2", swap_count=42, cumulative_volume_quote=1234.5,
        distinct_traders_count=7, stale_seconds=3.2,
        buy_count=25, sell_count=17, undetermined_count=0,
        buy_volume_quote=700.0, sell_volume_quote=534.5, undetermined_volume_quote=0.0,
        liquidity_added_quote=900.0, liquidity_removed_quote=150.0,
        liquidity_added_raw=0.0, liquidity_removed_raw=0.0,
    )
    feed._ws_feed.get_snapshot.return_value = snapshot
    result = await feed.check_candidates(min_liquidity_usd=4000.0)

    # Same decision as test_check_candidates_qualifies_a_pool_with_exact_v2_stable_reserve
    # / test_a_qualified_candidate_is_observed_alongside_the_qualification -- unchanged.
    assert len(result) == 1
    assert result[0].reserve_usd == 9000.0

    assert len(recorded) == 1
    obs = recorded[0]
    assert obs["chain"] == "base"
    assert obs["pool_address"] == "0xpool"
    assert obs["token_address"] == "0xtoken"
    assert obs["available"] is True
    assert obs["family"] == "v2"
    assert obs["swap_count"] == 42
    assert obs["cumulative_volume_quote"] == 1234.5
    assert obs["distinct_traders_count"] == 7
    assert obs["last_swap_age_seconds"] == 3.2
    # brique 2/5 (29/08) -- buy/sell fields actually wired through, not just
    # decodable in evm_swap_ws.py in isolation.
    assert obs["buy_count"] == 25
    assert obs["sell_count"] == 17
    assert obs["undetermined_count"] == 0
    assert obs["buy_volume_quote"] == 700.0
    assert obs["sell_volume_quote"] == 534.5
    assert obs["undetermined_volume_quote"] == 0.0
    # brique 3/5 (29/08) -- liquidity fields actually wired through too.
    assert obs["liquidity_added_quote"] == 900.0
    assert obs["liquidity_removed_quote"] == 150.0
    assert obs["liquidity_added_raw"] == 0.0
    assert obs["liquidity_removed_raw"] == 0.0


@pytest.mark.asyncio
async def test_activity_observation_records_none_when_snapshot_unavailable(monkeypatch):
    """Same point 6, unavailable-snapshot branch: not_yet_priceable_count
    (the real decision counter) stays unchanged, and every activity field
    is explicitly None rather than a fabricated 0."""
    from aria_core import onchain_activity_observation

    recorded = []

    async def _capture(**kwargs):
        recorded.append(kwargs)

    monkeypatch.setattr(onchain_activity_observation, "record_observation", _capture)

    feed = _make_feed()
    feed._candidates["0xpool"] = m._Candidate(
        pool_key="0xpool", dex_id="uniswap_v2", token_address="0xtoken", chain="base",
    )
    feed._ws_feed.get_snapshot.return_value = MagicMock(
        available=False, reserve_usd=None, quote_is_weth=False, price_quote=None,
        family=None, swap_count=0, cumulative_volume_quote=0.0,
        distinct_traders_count=0, stale_seconds=None,
        buy_count=0, sell_count=0, undetermined_count=0,
        buy_volume_quote=0.0, sell_volume_quote=0.0, undetermined_volume_quote=0.0,
        liquidity_added_quote=0.0, liquidity_removed_quote=0.0,
        liquidity_added_raw=0.0, liquidity_removed_raw=0.0,
    )
    # _make_feed's default resolve_cold already returns available=False.
    result = await feed.check_candidates(min_liquidity_usd=200.0)

    assert result == []
    assert feed.not_yet_priceable_count == 1  # unchanged existing counter

    assert len(recorded) == 1
    obs = recorded[0]
    assert obs["available"] is False
    assert obs["family"] is None
    # brique 2/5 -- an unavailable snapshot must pass None, never the mock's
    # own zero-valued fields, per the caller's `if snapshot.available else None`.
    assert obs["buy_count"] is None
    assert obs["sell_count"] is None
    assert obs["undetermined_count"] is None
    assert obs["buy_volume_quote"] is None
    assert obs["sell_volume_quote"] is None
    assert obs["undetermined_volume_quote"] is None
    assert obs["swap_count"] is None
    assert obs["cumulative_volume_quote"] is None
    assert obs["distinct_traders_count"] is None
    assert obs["last_swap_age_seconds"] is None
    assert obs["liquidity_added_quote"] is None
    assert obs["liquidity_removed_quote"] is None
    assert obs["liquidity_added_raw"] is None
    assert obs["liquidity_removed_raw"] is None


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
async def test_check_candidates_v4_pool_without_websocket_reserve_stays_unqualified():
    """specs/015-robinhood-chainstack-only -- v4 has no per-pool contract to
    `eth_call` against (singleton PoolManager, see EVMSwapWebSocketFeed.
    resolve_cold's own docstring), so a v4 pool without a real websocket-
    observed Sync/Swap event stays `not_yet_priceable` forever this cycle --
    never a DexPaprika fallback anymore. Verifies the state machine honestly
    reports unavailable rather than qualifying on a v4 guess."""
    feed = _make_feed()
    feed._candidates["0xpoolid"] = m._Candidate(
        pool_key="0xpoolid", dex_id="uniswap_v4", token_address="0xtoken", chain="base",
    )
    snapshot = MagicMock(available=True, reserve_usd=None, quote_is_weth=False, price_quote=None)
    feed._ws_feed.get_snapshot.return_value = snapshot
    # _make_feed's default resolve_cold already returns available=False --
    # explicit here for a v4 pool_id key (not an address) to match how a
    # real v4 candidate is keyed.
    result = await feed.check_candidates(min_liquidity_usd=4000.0)
    assert result == []
    assert "0xpoolid" in feed._candidates  # stays pending, not dropped, not fabricated
    assert feed.not_yet_priceable_count == 1  # T019 -- explicit, counted state


# --- T019/T020/T022 (Phase 4, US2 -- not_yet_priceable state + subscription cap) --

@pytest.mark.asyncio
async def test_check_candidates_counts_not_yet_priceable_only_when_both_sources_fail():
    """T019 -- distinct from `rejected_not_priceable_count` (a permanent
    rejection at registration time). This counter is temporary/retryable:
    no websocket snapshot AND resolve_cold both unresolved this cycle."""
    feed = _make_feed()
    feed._candidates["0xpoolid"] = m._Candidate(
        pool_key="0xpoolid", dex_id="uniswap_v2", token_address="0xtoken", chain="base",
    )
    feed._ws_feed.get_snapshot.return_value = MagicMock(
        available=False, reserve_usd=None, quote_is_weth=False, price_quote=None,
    )
    assert feed.not_yet_priceable_count == 0
    await feed.check_candidates(min_liquidity_usd=4000.0)
    assert feed.not_yet_priceable_count == 1
    assert "0xpoolid" in feed._candidates  # retried later, never dropped for this alone


@pytest.mark.asyncio
async def test_check_candidates_never_counts_not_yet_priceable_on_a_qualified_read():
    feed = _make_feed()
    feed._candidates["0xpool"] = m._Candidate(
        pool_key="0xpool", dex_id="uniswap_v2", token_address="0xtoken", chain="base",
    )
    snapshot = MagicMock(available=True, reserve_usd=9000.0, quote_is_weth=False, price_quote=0.001)
    feed._ws_feed.get_snapshot.return_value = snapshot
    result = await feed.check_candidates(min_liquidity_usd=4000.0)
    assert len(result) == 1
    assert feed.not_yet_priceable_count == 0  # resolved on the first (WS) source, never reached resolve_cold


@pytest.mark.asyncio
async def test_check_candidates_never_qualifies_a_partial_cold_read():
    """T022 -- real resolve_cold contract case: a v3 stable-quoted pool
    resolves `price_usd` but NEVER `reserve_usd` (v3 has no reserve figure,
    only active-tick liquidity, see resolve_cold's own docstring). The
    qualification gate below must still reject this as unpriceable rather
    than treat the resolved price as good enough on its own -- the operator's
    explicit "no subtler fabricated-price failure class" requirement."""
    feed = _make_feed()
    feed._candidates["0xpoolid"] = m._Candidate(
        pool_key="0xpoolid", dex_id="uniswap_v3", token_address="0xtoken", chain="base",
    )
    feed._ws_feed.get_snapshot.return_value = MagicMock(
        available=False, reserve_usd=None, quote_is_weth=False, price_quote=None,
    )
    feed._ws_feed.resolve_cold = AsyncMock(return_value=MagicMock(
        available=True, price_usd=0.002, reserve_usd=None, price_quote=0.002, quote_is_weth=False,
    ))
    result = await feed.check_candidates(min_liquidity_usd=4000.0)
    assert result == []  # a resolved price alone, with no reserve, never qualifies
    assert "0xpoolid" in feed._candidates  # stays pending, not fabricated as qualified


@pytest.mark.asyncio
async def test_register_candidate_skips_and_counts_when_cap_reached():
    """T020 -- an initial guardrail (150), not a tuned optimum. Skip/defer,
    never an unbounded queue: a candidate arriving once the cap is full is
    simply never subscribed this notification."""
    feed = _make_feed()
    for i in range(m.MAX_CONCURRENT_TRACKED_POOLS):
        feed._candidates[f"0xfull{i}"] = m._Candidate(
            pool_key=f"0xfull{i}", dex_id="uniswap_v2", token_address="0xtoken", chain="base",
        )
    assert feed.cap_dropped_count == 0
    feed._register_candidate("0xoverflow", "uniswap_v2", f"0x{TOKEN0}", f"0x{WETH}")
    assert "0xoverflow" not in feed._candidates
    assert feed.cap_dropped_count == 1
    feed._ws_feed.add_pool.assert_not_called()


@pytest.mark.asyncio
async def test_register_candidate_still_registers_below_the_cap():
    feed = _make_feed()
    for i in range(m.MAX_CONCURRENT_TRACKED_POOLS - 1):
        feed._candidates[f"0xfull{i}"] = m._Candidate(
            pool_key=f"0xfull{i}", dex_id="uniswap_v2", token_address="0xtoken", chain="base",
        )
    feed._register_candidate("0xroom", "uniswap_v2", f"0x{TOKEN0}", f"0x{WETH}")
    assert "0xroom" in feed._candidates
    assert feed.cap_dropped_count == 0


@pytest.mark.asyncio
async def test_check_candidates_never_requeries_resolve_cold_within_recheck_interval():
    feed = _make_feed()
    feed._candidates["0xpoolid"] = m._Candidate(
        pool_key="0xpoolid", dex_id="uniswap_v2", token_address="0xtoken", chain="base",
    )
    snapshot = MagicMock(available=True, reserve_usd=None, quote_is_weth=False, price_quote=None)
    feed._ws_feed.get_snapshot.return_value = snapshot
    feed._ws_feed.resolve_cold = AsyncMock(return_value=MagicMock(
        available=False, reserve_usd=None, price_usd=None, price_quote=None, quote_is_weth=False,
    ))
    await feed.check_candidates(min_liquidity_usd=4000.0)
    await feed.check_candidates(min_liquidity_usd=4000.0)
    assert feed._ws_feed.resolve_cold.await_count == 1  # second call within _RECHECK_INTERVAL_SECONDS is skipped


@pytest.mark.asyncio
async def test_check_candidates_falls_back_to_resolve_cold_when_add_pool_never_succeeded():
    """26/08 real incident: a fresh day-zero pool whose add_pool() verify
    call failed (RPC hadn't indexed the block yet -- "no such table"-style
    race) used to `continue` straight past the fallback below, forever --
    zero real Base candidates got through despite the pool genuinely
    qualifying. `available=False` must fall through to the SAME cold-read
    path a v3 pool (which never gets a WS reserve_usd either) already uses,
    not be treated as a dead end. specs/015-robinhood-chainstack-only --
    the fallback itself is now `resolve_cold`, never DexPaprika."""
    feed = _make_feed()
    feed._candidates["0xpoolid"] = m._Candidate(
        pool_key="0xpoolid", dex_id="uniswap_v2", token_address="0xtoken", chain="base",
    )
    feed._ws_feed.get_snapshot.return_value = MagicMock(
        available=False, reserve_usd=None, quote_is_weth=False, price_quote=None,
    )
    feed._ws_feed.add_pool = AsyncMock(return_value=False)
    feed._ws_feed.resolve_cold = AsyncMock(return_value=MagicMock(
        available=True, reserve_usd=15000.0, price_usd=0.002, price_quote=0.002, quote_is_weth=False,
    ))
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
    await feed.check_candidates(min_liquidity_usd=4000.0)
    import asyncio
    await asyncio.sleep(0)  # let the fire-and-forget create_task actually run
    assert add_pool_calls == ["0xpoolid"]
