"""services/evm_swap_ws.py -- decode logic (pure, offset-verified) and the
live feed's in-memory snapshot/state logic. Never a real network call; the
websocket connection is never exercised here (start()/_run() are covered
implicitly by the module's own defensive try/except, same posture as
pumpswap_ws.py). Same rigor as every other service test in this dome."""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from aria_core.services import chainstack_ru_budget
from aria_core.services import evm_swap_ws as m


@pytest.fixture(autouse=True)
def _isolated_chainstack_ru_budget_db(tmp_path, monkeypatch):
    """24/08 -- add_pool() now calls chainstack_ru_budget.can_spend() and
    _handle_notification() calls record_usage_fast(); without this, every
    test in this file touched the real dev DB and shared in-memory state
    with whatever else imported the module (same isolation gap already
    fixed in test_chainstack_ru_budget.py's own fixture)."""
    monkeypatch.setattr(chainstack_ru_budget, "aria_db_path", lambda: tmp_path / "chainstack_ru_budget_test.db")
    chainstack_ru_budget._pending_units.clear()
    chainstack_ru_budget._read_cache.clear()
    yield
    chainstack_ru_budget._pending_units.clear()
    chainstack_ru_budget._read_cache.clear()


# --- topic0: recomputed independently, never trusted from a memorized value -

def test_topic0_values_are_computed_via_keccak_independently_from_the_module():
    """Recomputes each topic0 with a fresh Web3.keccak call (same library the
    module itself uses, but a SEPARATE call, not routed through m._topic0())
    so a regression in the module's own signature strings or hashing would
    be caught -- never a hand-typed/memorized hash, which is exactly the
    mistake this test replaces (a memorized value was one character off)."""
    from web3 import Web3

    assert m._SYNC_TOPIC == "0x" + Web3.keccak(text="Sync(uint112,uint112)").hex()
    assert m._SYNC_TOPIC_AERODROME == "0x" + Web3.keccak(text="Sync(uint256,uint256)").hex()
    assert m._V3_SWAP_TOPIC == "0x" + Web3.keccak(
        text="Swap(address,address,int256,int256,uint160,uint128,int24)"
    ).hex()
    assert m._V4_SWAP_TOPIC == "0x" + Web3.keccak(
        text="Swap(bytes32,address,int128,int128,uint160,uint128,int24,uint24)"
    ).hex()


def test_topic0_values_are_well_formed_32_byte_hashes():
    for topic in (m._SYNC_TOPIC, m._SYNC_TOPIC_AERODROME, m._V3_SWAP_TOPIC, m._V4_SWAP_TOPIC):
        assert topic.startswith("0x")
        assert len(topic) == 66  # "0x" + 64 hex chars = 32 bytes


def test_topic0_values_are_all_distinct():
    topics = {m._SYNC_TOPIC, m._SYNC_TOPIC_AERODROME, m._V3_SWAP_TOPIC, m._V4_SWAP_TOPIC}
    assert len(topics) == 4


# --- dex_family mapping: unmapped stays honestly uncovered ------------------

def test_dex_family_maps_known_ids():
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    assert feed.dex_family("uniswap_v2") == "v2"
    assert feed.dex_family("uniswap_v3") == "v3"
    assert feed.dex_family("uniswap_v4") == "v4"
    assert feed.dex_family("aerodrome") == "v2"
    assert feed.dex_family("aerodrome_v3") == "v3"
    assert feed.dex_family("aerodrome_slipstream_3") == "v3"


def test_dex_family_returns_none_for_unmapped_id():
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    assert feed.dex_family("some_unmapped_dex") is None
    assert feed.dex_family(None) is None


# --- _to_signed256: raw uint256 -> Python signed int -------------------------

def test_to_signed256_positive_value_unchanged():
    assert m.EVMSwapWebSocketFeed._to_signed256(1_000_000) == 1_000_000


def test_to_signed256_negative_value_decoded_from_twos_complement():
    raw = (1 << 256) - 1_000_000  # two's-complement encoding of -1_000_000
    assert m.EVMSwapWebSocketFeed._to_signed256(raw) == -1_000_000


def test_to_signed256_zero():
    assert m.EVMSwapWebSocketFeed._to_signed256(0) == 0


# --- get_snapshot: pure state, no network ------------------------------------

def _pool(**overrides) -> m._TrackedPool:
    defaults = dict(
        dex_id="uniswap_v3", family="v3", token_is_currency0=True,
        decimals0=18, decimals1=6, quote_is_weth=False, quote_is_stable=True,
    )
    defaults.update(overrides)
    return m._TrackedPool(**defaults)


def test_get_snapshot_unavailable_for_untracked_pool():
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    snap = feed.get_snapshot("0xnope")
    assert snap.available is False


def test_get_snapshot_unavailable_before_first_tick():
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    feed._pools["0xpool"] = _pool()
    feed._connected = True
    snap = feed.get_snapshot("0xpool")
    assert snap.available is False


def test_get_snapshot_unavailable_when_disconnected_even_with_ticks():
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    pool = _pool()
    pool.ticks.append((time.monotonic(), 1.5))
    feed._pools["0xpool"] = pool
    feed._connected = False
    snap = feed.get_snapshot("0xpool")
    assert snap.available is False


def test_get_snapshot_computes_window_high_low_and_usd_for_stable_quote():
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    pool = _pool(quote_is_stable=True)
    now = time.monotonic()
    pool.ticks.append((now - 10, 2.0))
    pool.ticks.append((now - 5, 3.0))
    pool.ticks.append((now, 1.0))
    pool.swap_count = 3
    pool.cumulative_volume_quote = 42.0
    pool.distinct_traders = {"0xaaa", "0xbbb"}
    feed._pools["0xpool"] = pool
    feed._connected = True
    snap = feed.get_snapshot("0xpool")
    assert snap.available is True
    assert snap.price_quote == pytest.approx(1.0)
    assert snap.price_usd == pytest.approx(1.0)  # quote_is_stable -> price_usd = price_quote
    assert snap.window_high_quote == pytest.approx(3.0)
    assert snap.window_low_quote == pytest.approx(1.0)
    assert snap.swap_count == 3
    assert snap.cumulative_volume_quote == pytest.approx(42.0)
    assert snap.distinct_traders_count == 2


def test_get_snapshot_exposes_pool_family():
    """29/08, operator-directed -- lets onchain_activity_observation.py
    distinguish v2 (Sync-derived swap_count, biased by Mint/Burn) from
    v3/v4 (clean, from a real decoded Swap event)."""
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    pool = _pool(family="v2", quote_is_stable=True)
    pool.ticks.append((time.monotonic(), 1.0))
    feed._pools["0xpool"] = pool
    feed._connected = True
    snap = feed.get_snapshot("0xpool")
    assert snap.family == "v2"


def test_get_snapshot_weth_quote_leaves_price_usd_none():
    """USD resolution for a WETH-quoted pool needs doppler.eth_usd_rate(),
    deliberately not called here (no network I/O inside a decoder) -- the
    caller resolves it. price_usd must stay None, never fabricated."""
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    pool = _pool(quote_is_weth=True, quote_is_stable=False)
    pool.ticks.append((time.monotonic(), 0.0005))
    feed._pools["0xpool"] = pool
    feed._connected = True
    snap = feed.get_snapshot("0xpool")
    assert snap.available is True
    assert snap.price_usd is None
    assert snap.price_quote == pytest.approx(0.0005)
    assert snap.quote_is_weth is True


def test_get_snapshot_cbbtc_quote_leaves_price_usd_none():
    """27/08 -- same honesty rule as the WETH case above, for a cbBTC-quoted
    pool: USD resolution needs doppler.btc_usd_rate(), never computed here."""
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    pool = _pool(quote_is_weth=False, quote_is_stable=False, quote_is_btc=True)
    pool.ticks.append((time.monotonic(), 0.00002))
    feed._pools["0xpool"] = pool
    feed._connected = True
    snap = feed.get_snapshot("0xpool")
    assert snap.available is True
    assert snap.price_usd is None
    assert snap.price_quote == pytest.approx(0.00002)
    assert snap.quote_is_btc is True


def test_get_snapshot_window_excludes_stale_ticks():
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    pool = _pool()
    now = time.monotonic()
    pool.ticks.append((now - 500, 99.0))  # outside a 300s window
    pool.ticks.append((now, 1.0))
    feed._pools["0xpool"] = pool
    feed._connected = True
    snap = feed.get_snapshot("0xpool", window_seconds=300.0)
    assert snap.available is True
    assert snap.window_high_quote == pytest.approx(1.0)
    assert snap.window_low_quote == pytest.approx(1.0)


# --- _handle_sync (v2/aerodrome-classic): reserve ratio + exact USD reserve -

def _u256(x: int) -> bytes:
    return x.to_bytes(32, "big")


def test_handle_sync_computes_price_and_exact_reserve_usd_for_stable_quote():
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    # token is currency0 (18 decimals), quote is currency1 (6 decimals, USDC-like stable).
    pool = m._TrackedPool(
        dex_id="uniswap_v2", family="v2", token_is_currency0=True,
        decimals0=18, decimals1=6, quote_is_weth=False, quote_is_stable=True,
    )
    feed._pools["0xpool"] = pool
    # reserve0 = 10 tokens (18 dec), reserve1 = 20 quote units (6 dec) -> price = 2.0 quote/token
    reserve0 = 10 * (10 ** 18)
    reserve1 = 20 * (10 ** 6)
    data = _u256(reserve0) + _u256(reserve1)
    feed._handle_sync("0xpool", {"data": "0x" + data.hex()})
    # 29/08 fix: Sync alone (no accompanying V2 Swap event) never counts as
    # a swap anymore -- see test_a_sync_only_event_never_counts_as_a_swap
    # below for the Mint/Burn-signature regression guard this replaces.
    assert pool.swap_count == 0
    assert pool.last_reserve_usd == pytest.approx(40.0)  # 2 * quote_reserve (20)
    assert pool.ticks[-1][1] == pytest.approx(2.0)
    assert pool.last_quote_reserve_raw == pytest.approx(20.0)  # unconditional, even for a stable quote


def test_handle_sync_records_quote_reserve_raw_for_a_weth_quote():
    """28/08, specs/015-robinhood-chainstack-only -- unlike last_reserve_usd
    (only exact for a known USD stable), last_quote_reserve_raw is computed
    regardless, so a WETH-quoted pool's caller can still convert it to USD
    itself via doppler.eth_usd_rate() -- see onchain_pool_discovery.py's
    check_candidates for the real conversion site."""
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    pool = m._TrackedPool(
        dex_id="uniswap_v2", family="v2", token_is_currency0=True,
        decimals0=18, decimals1=18, quote_is_weth=True, quote_is_stable=False,
    )
    feed._pools["0xpool"] = pool
    reserve0 = 10 * (10 ** 18)
    reserve1 = 5 * (10 ** 18)  # 5 WETH on the quote side
    data = _u256(reserve0) + _u256(reserve1)
    feed._handle_sync("0xpool", {"data": "0x" + data.hex()})
    assert pool.last_reserve_usd is None  # never fabricated without a real ETH rate
    assert pool.last_quote_reserve_raw == pytest.approx(5.0)


def test_handle_sync_ignores_unknown_pool():
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    data = _u256(1) + _u256(1)
    feed._handle_sync("0xunknown", {"data": "0x" + data.hex()})  # must not raise


def test_handle_sync_skips_zero_reserve_without_recording_a_tick():
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    pool = m._TrackedPool(
        dex_id="uniswap_v2", family="v2", token_is_currency0=True,
        decimals0=18, decimals1=6, quote_is_weth=False, quote_is_stable=True,
    )
    feed._pools["0xpool"] = pool
    data = _u256(0) + _u256(20 * 10**6)
    feed._handle_sync("0xpool", {"data": "0x" + data.hex()})
    assert len(pool.ticks) == 0
    assert pool.swap_count == 0


# --- _handle_v2_swap: real V2 Swap event, distinct from Mint/Burn's Sync ---

def _v2_swap_data(amount0_in: int, amount1_in: int, amount0_out: int, amount1_out: int) -> str:
    return "0x" + (_u256(amount0_in) + _u256(amount1_in) + _u256(amount0_out) + _u256(amount1_out)).hex()


def test_handle_v2_swap_decodes_volume_and_sender_and_increments_swap_count():
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    pool = _pool(family="v2", token_is_currency0=True, decimals0=18, decimals1=6)
    feed._pools["0xpool"] = pool
    # token is currency0 -> quote is currency1 (6 decimals): swapper paid
    # 20 quote units in, received the tracked token out (a buy).
    data = _v2_swap_data(amount0_in=0, amount1_in=20 * 10**6, amount0_out=5 * 10**18, amount1_out=0)
    sender_topic = _FakeTopic(bytes.fromhex("00" * 12 + "bb" * 20))
    topics = [MagicMock(), sender_topic]
    feed._handle_v2_swap("0xpool", topics, {"data": data})
    assert pool.swap_count == 1
    assert pool.cumulative_volume_quote == pytest.approx(20.0)
    assert "0x" + "bb" * 20 in pool.distinct_traders


def test_handle_v2_swap_orientation_token_is_currency1():
    """The quote leg is currency0 when the tracked token is currency1 --
    same decoding, opposite side picked."""
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    pool = _pool(family="v2", token_is_currency0=False, decimals0=6, decimals1=18)
    feed._pools["0xpool"] = pool
    data = _v2_swap_data(amount0_in=30 * 10**6, amount1_in=0, amount0_out=0, amount1_out=7 * 10**18)
    feed._handle_v2_swap("0xpool", [MagicMock(), MagicMock()], {"data": data})
    assert pool.cumulative_volume_quote == pytest.approx(30.0)


def test_handle_v2_swap_ignores_unknown_pool():
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    data = _v2_swap_data(0, 1, 1, 0)
    feed._handle_v2_swap("0xunknown", [MagicMock(), MagicMock()], {"data": data})  # must not raise


def test_a_sync_only_event_never_counts_as_a_swap_mint_burn_signature():
    """THE regression guard for the 27/08-found, 29/08-fixed defect: a
    Mint/Burn emits Sync but NEVER the real V2 Swap event. Simulates that
    exact signature -- a Sync notification with no accompanying Swap --
    and asserts swap_count/volume/distinct_traders all stay at zero."""
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    pool = _pool(family="v2", token_is_currency0=True, decimals0=18, decimals1=6, quote_is_stable=True)
    feed._pools["0xpool"] = pool
    data = _u256(10 * 10**18) + _u256(20 * 10**6)
    feed._handle_sync("0xpool", {"data": "0x" + data.hex()})
    assert pool.swap_count == 0
    assert pool.cumulative_volume_quote == pytest.approx(0.0)
    assert len(pool.distinct_traders) == 0
    # The price itself IS still correctly updated by Sync -- Mint/Burn
    # preserve the reserve ratio, so this part was never wrong.
    assert pool.ticks[-1][1] == pytest.approx(2.0)


def test_a_real_swap_transaction_sync_then_swap_counts_exactly_once():
    """The real transaction shape: Sync arrives first (price), then the
    real Swap event (volume/sender) in the same transaction. Must count
    exactly once -- never twice, never zero."""
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    pool = _pool(family="v2", token_is_currency0=True, decimals0=18, decimals1=6, quote_is_stable=True)
    feed._pools["0xpool"] = pool
    sync_data = _u256(10 * 10**18) + _u256(20 * 10**6)
    feed._handle_sync("0xpool", {"data": "0x" + sync_data.hex()})
    swap_data = _v2_swap_data(amount0_in=0, amount1_in=1 * 10**6, amount0_out=1 * 10**17, amount1_out=0)
    feed._handle_v2_swap("0xpool", [MagicMock(), MagicMock()], {"data": swap_data})
    assert pool.swap_count == 1
    assert pool.last_reserve_usd == pytest.approx(40.0)  # from Sync, untouched by the Swap handler
    assert pool.ticks[-1][1] == pytest.approx(2.0)  # price still comes from Sync only


# --- brique 2/5 (29/08): buy/sell direction classification ------------------
# Operator-required guard: each buy/sell case is doubled across
# token_is_currency0=True/False, since the "quote entering the pool = BUY"
# convention only means "bought the TRACKED token" if this flag is correctly
# propagated -- a silent inversion would flip buy/sell with nothing else
# catching it.

def test_handle_v2_swap_buy_when_quote_in_positive_currency0_true():
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    pool = _pool(family="v2", token_is_currency0=True, decimals0=18, decimals1=6)
    feed._pools["0xpool"] = pool
    # quote is currency1 -- swapper paid quote in, received tracked token out.
    data = _v2_swap_data(amount0_in=0, amount1_in=20 * 10**6, amount0_out=5 * 10**18, amount1_out=0)
    feed._handle_v2_swap("0xpool", [MagicMock(), MagicMock()], {"data": data})
    assert pool.buy_count == 1
    assert pool.sell_count == 0
    assert pool.undetermined_count == 0
    assert pool.buy_volume_quote == pytest.approx(20.0)
    assert pool.sell_volume_quote == pytest.approx(0.0)


def test_handle_v2_swap_buy_when_quote_in_positive_currency0_false():
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    pool = _pool(family="v2", token_is_currency0=False, decimals0=6, decimals1=18)
    feed._pools["0xpool"] = pool
    # quote is currency0 -- swapper paid quote in, received tracked token out.
    data = _v2_swap_data(amount0_in=30 * 10**6, amount1_in=0, amount0_out=0, amount1_out=7 * 10**18)
    feed._handle_v2_swap("0xpool", [MagicMock(), MagicMock()], {"data": data})
    assert pool.buy_count == 1
    assert pool.sell_count == 0
    assert pool.buy_volume_quote == pytest.approx(30.0)


def test_handle_v2_swap_sell_when_quote_out_positive_currency0_true():
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    pool = _pool(family="v2", token_is_currency0=True, decimals0=18, decimals1=6)
    feed._pools["0xpool"] = pool
    # quote is currency1 -- swapper paid tracked token in, received quote out.
    data = _v2_swap_data(amount0_in=5 * 10**18, amount1_in=0, amount0_out=0, amount1_out=20 * 10**6)
    feed._handle_v2_swap("0xpool", [MagicMock(), MagicMock()], {"data": data})
    assert pool.sell_count == 1
    assert pool.buy_count == 0
    assert pool.undetermined_count == 0
    assert pool.sell_volume_quote == pytest.approx(20.0)
    assert pool.buy_volume_quote == pytest.approx(0.0)


def test_handle_v2_swap_sell_when_quote_out_positive_currency0_false():
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    pool = _pool(family="v2", token_is_currency0=False, decimals0=6, decimals1=18)
    feed._pools["0xpool"] = pool
    # quote is currency0 -- swapper paid tracked token in, received quote out.
    data = _v2_swap_data(amount0_in=0, amount1_in=7 * 10**18, amount0_out=30 * 10**6, amount1_out=0)
    feed._handle_v2_swap("0xpool", [MagicMock(), MagicMock()], {"data": data})
    assert pool.sell_count == 1
    assert pool.buy_count == 0
    assert pool.sell_volume_quote == pytest.approx(30.0)


def test_handle_v2_swap_both_in_and_out_nonzero_is_undetermined_never_forced():
    """Not a real V2 Swap shape (one side should always be zero) -- if it
    ever happens, never force a side, count as undetermined instead."""
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    pool = _pool(family="v2", token_is_currency0=True, decimals0=18, decimals1=6)
    feed._pools["0xpool"] = pool
    data = _v2_swap_data(amount0_in=0, amount1_in=5 * 10**6, amount0_out=0, amount1_out=5 * 10**6)
    feed._handle_v2_swap("0xpool", [MagicMock(), MagicMock()], {"data": data})
    assert pool.buy_count == 0
    assert pool.sell_count == 0
    assert pool.undetermined_count == 1
    # swap_count/cumulative_volume_quote (brique 1) stay exactly as before --
    # this brique never regresses that behaviour.
    assert pool.swap_count == 1
    assert pool.cumulative_volume_quote == pytest.approx(10.0)


@pytest.mark.asyncio
async def test_resubscribe_includes_v2_swap_topic_in_the_v2v3_filter():
    """Confirms the new topic is actually wired into the real subscription
    filter (not just decodable in isolation) -- without this, the decoder
    above would never receive a real V2 Swap notification in production."""
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    feed._w3 = MagicMock()
    feed._w3.eth.subscribe = AsyncMock(return_value="sub_v2v3")
    feed._pools["0xpool"] = m._TrackedPool(
        dex_id="uniswap_v2", family="v2", token_is_currency0=True,
        decimals0=18, decimals1=18, quote_is_weth=False, quote_is_stable=False,
    )
    await feed._resubscribe()
    filter_arg = feed._w3.eth.subscribe.call_args[0][1]
    assert m._V2_SWAP_TOPIC in filter_arg["topics"][0]
    assert m._V2_SWAP_TOPIC not in (m._SYNC_TOPIC, m._SYNC_TOPIC_AERODROME, m._V3_SWAP_TOPIC)


# --- _handle_v3_swap / _handle_v4_swap: sqrtPriceX96 + amounts + sender -----

def _sqrt_price_x96_for_price(price_token1_per_token0: float) -> int:
    """sqrtPriceX96 = sqrt(price) * 2**96, inverse of doppler.price_from_sqrt_price_x96."""
    return int((price_token1_per_token0 ** 0.5) * (2 ** 96))


class _FakeTopic:
    def __init__(self, raw: bytes):
        self._raw = raw

    def hex(self) -> str:
        return self._raw.hex()  # deliberately WITHOUT "0x" prefix, like real HexBytes


def test_handle_v3_swap_decodes_price_liquidity_volume_and_sender():
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    pool = m._TrackedPool(
        dex_id="uniswap_v3", family="v3", token_is_currency0=True,
        decimals0=18, decimals1=18, quote_is_weth=True, quote_is_stable=False,
    )
    feed._pools["0xpool"] = pool
    sqrt_price = _sqrt_price_x96_for_price(0.001)  # 1 token = 0.001 quote
    amount0 = m.EVMSwapWebSocketFeed._to_signed256(-5 * 10**18) % (1 << 256)
    amount1 = 5 * 10**15  # positive, 18 decimals
    liquidity = 123_456
    tick = 0
    raw = (
        amount0.to_bytes(32, "big") + amount1.to_bytes(32, "big")
        + sqrt_price.to_bytes(32, "big") + liquidity.to_bytes(32, "big")
        + (tick % (1 << 256)).to_bytes(32, "big")
    )
    sender_topic = _FakeTopic(bytes.fromhex("00" * 12 + "aa" * 20))
    topics = [MagicMock(), sender_topic]
    feed._handle_v3_swap("0xpool", topics, {"data": "0x" + raw.hex()})
    assert pool.swap_count == 1
    assert pool.last_raw_liquidity == pytest.approx(float(liquidity))
    assert pool.cumulative_volume_quote == pytest.approx(5 * 10**15 / 10**18)
    assert "0x" + "aa" * 20 in pool.distinct_traders
    assert len(pool.ticks) == 1


def test_handle_v3_swap_ignores_unknown_pool():
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    raw = bytes(32 * 5)
    feed._handle_v3_swap("0xunknown", [MagicMock(), MagicMock()], {"data": "0x" + raw.hex()})  # must not raise


def test_handle_v3_swap_skips_zero_sqrt_price():
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    pool = _pool()
    feed._pools["0xpool"] = pool
    raw = bytes(32 * 5)  # sqrt_price_x96 = 0
    feed._handle_v3_swap("0xpool", [MagicMock(), MagicMock()], {"data": "0x" + raw.hex()})
    assert pool.swap_count == 0
    assert len(pool.ticks) == 0


def _v3v4_raw(amount0_signed: int, amount1_signed: int, *, sqrt_price: int = None) -> bytes:
    if sqrt_price is None:
        sqrt_price = _sqrt_price_x96_for_price(1.0)
    a0 = amount0_signed % (1 << 256)
    a1 = amount1_signed % (1 << 256)
    return (
        a0.to_bytes(32, "big") + a1.to_bytes(32, "big")
        + sqrt_price.to_bytes(32, "big") + (0).to_bytes(32, "big") + (0).to_bytes(32, "big")
    )


def test_handle_v3_swap_buy_when_quote_raw_positive_currency0_true():
    """token_is_currency0=True -> quote leg is amount1. Positive amount1
    means the pool RECEIVED quote (trader paid quote for the tracked
    token) -> BUY."""
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    pool = _pool(family="v3", token_is_currency0=True, decimals0=18, decimals1=18)
    feed._pools["0xpool"] = pool
    raw = _v3v4_raw(amount0_signed=-2 * 10**18, amount1_signed=3 * 10**18)
    feed._handle_v3_swap("0xpool", [MagicMock(), MagicMock()], {"data": "0x" + raw.hex()})
    assert pool.buy_count == 1
    assert pool.sell_count == 0
    assert pool.undetermined_count == 0
    assert pool.buy_volume_quote == pytest.approx(3.0)
    assert pool.sell_volume_quote == pytest.approx(0.0)


def test_handle_v3_swap_buy_when_quote_raw_positive_currency0_false():
    """token_is_currency0=False -> quote leg is amount0."""
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    pool = _pool(family="v3", token_is_currency0=False, decimals0=18, decimals1=18)
    feed._pools["0xpool"] = pool
    raw = _v3v4_raw(amount0_signed=4 * 10**18, amount1_signed=-1 * 10**18)
    feed._handle_v3_swap("0xpool", [MagicMock(), MagicMock()], {"data": "0x" + raw.hex()})
    assert pool.buy_count == 1
    assert pool.sell_count == 0
    assert pool.buy_volume_quote == pytest.approx(4.0)


def test_handle_v3_swap_sell_when_quote_raw_negative_currency0_true():
    """Negative amount1 means the pool SENT quote out (trader received
    quote for the tracked token it sold) -> SELL."""
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    pool = _pool(family="v3", token_is_currency0=True, decimals0=18, decimals1=18)
    feed._pools["0xpool"] = pool
    raw = _v3v4_raw(amount0_signed=5 * 10**18, amount1_signed=-2 * 10**18)
    feed._handle_v3_swap("0xpool", [MagicMock(), MagicMock()], {"data": "0x" + raw.hex()})
    assert pool.sell_count == 1
    assert pool.buy_count == 0
    assert pool.undetermined_count == 0
    assert pool.sell_volume_quote == pytest.approx(2.0)
    assert pool.buy_volume_quote == pytest.approx(0.0)


def test_handle_v3_swap_sell_when_quote_raw_negative_currency0_false():
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    pool = _pool(family="v3", token_is_currency0=False, decimals0=18, decimals1=18)
    feed._pools["0xpool"] = pool
    raw = _v3v4_raw(amount0_signed=-3 * 10**18, amount1_signed=6 * 10**18)
    feed._handle_v3_swap("0xpool", [MagicMock(), MagicMock()], {"data": "0x" + raw.hex()})
    assert pool.sell_count == 1
    assert pool.buy_count == 0
    assert pool.sell_volume_quote == pytest.approx(3.0)


def test_handle_v3_swap_zero_quote_raw_is_undetermined():
    """quote_raw == 0 -- rounds to zero after decimal adjustment or a swap
    that genuinely didn't move the quote leg. Never forced into buy/sell."""
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    pool = _pool(family="v3", token_is_currency0=True, decimals0=18, decimals1=18)
    feed._pools["0xpool"] = pool
    raw = _v3v4_raw(amount0_signed=5 * 10**18, amount1_signed=0)
    feed._handle_v3_swap("0xpool", [MagicMock(), MagicMock()], {"data": "0x" + raw.hex()})
    assert pool.buy_count == 0
    assert pool.sell_count == 0
    assert pool.undetermined_count == 1
    # swap_count/cumulative_volume_quote (brique 1) unaffected.
    assert pool.swap_count == 1
    assert pool.cumulative_volume_quote == pytest.approx(0.0)


def test_handle_v4_swap_classifies_buy_sell_direction():
    """V4 shares _record_swap_amount with V3 -- confirms the classification
    is actually wired through the V4 dispatch path, not just decodable via
    V3 in isolation."""
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    pool_id_raw = bytes.fromhex("dd" * 32)
    pool = m._TrackedPool(
        dex_id="uniswap_v4", family="v4", token_is_currency0=True,
        decimals0=18, decimals1=18, quote_is_weth=False, quote_is_stable=False,
        pool_id_hex="0x" + pool_id_raw.hex(),
    )
    feed._pools["0x" + pool_id_raw.hex()] = pool
    sqrt_price = _sqrt_price_x96_for_price(1.0)
    amount0 = ((-2 * 10**18) % (1 << 256))
    amount1 = 3 * 10**18
    fee = 3000
    raw = (
        amount0.to_bytes(32, "big") + amount1.to_bytes(32, "big")
        + sqrt_price.to_bytes(32, "big") + (0).to_bytes(32, "big")
        + (0).to_bytes(32, "big") + fee.to_bytes(32, "big")
    )
    topics = [MagicMock(), _FakeTopic(pool_id_raw), _FakeTopic(bytes.fromhex("00" * 12 + "ee" * 20))]
    feed._handle_v4_swap(topics, {"data": "0x" + raw.hex()})
    assert pool.buy_count == 1
    assert pool.sell_count == 0
    assert pool.buy_volume_quote == pytest.approx(3.0)


def test_handle_v4_swap_uses_pool_id_from_topics_1_and_sender_from_topics_2():
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    pool_id_raw = bytes.fromhex("bb" * 32)
    pool = m._TrackedPool(
        dex_id="uniswap_v4", family="v4", token_is_currency0=False,
        decimals0=6, decimals1=18, quote_is_weth=False, quote_is_stable=False,
        pool_id_hex="0x" + pool_id_raw.hex(),
    )
    feed._pools["0x" + pool_id_raw.hex()] = pool
    sqrt_price = _sqrt_price_x96_for_price(1500.0)
    amount0 = (1000 * 10**6)
    amount1 = ((1 << 256) - 1 * 10**18)  # negative amount1, two's complement
    liquidity = 777
    tick = 0
    fee = 3000
    raw = (
        amount0.to_bytes(32, "big") + amount1.to_bytes(32, "big")
        + sqrt_price.to_bytes(32, "big") + liquidity.to_bytes(32, "big")
        + tick.to_bytes(32, "big") + fee.to_bytes(32, "big")
    )
    topics = [MagicMock(), _FakeTopic(pool_id_raw), _FakeTopic(bytes.fromhex("00" * 12 + "cc" * 20))]
    feed._handle_v4_swap(topics, {"data": "0x" + raw.hex()})
    assert pool.swap_count == 1
    assert pool.last_raw_liquidity == pytest.approx(float(liquidity))
    assert "0x" + "cc" * 20 in pool.distinct_traders
    assert len(pool.ticks) == 1


def test_handle_v4_swap_ignores_unmapped_pool_id():
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    raw = bytes(32 * 6)
    topics = [MagicMock(), _FakeTopic(bytes.fromhex("00" * 32)), _FakeTopic(bytes.fromhex("00" * 32))]
    feed._handle_v4_swap(topics, {"data": "0x" + raw.hex()})  # must not raise, no pool registered


# --- brique 3/5: Mint/Burn/ModifyLiquidity -- liquidity delta, a THIRD axis,
# never touching swap_count/buy_volume_quote/sell_volume_quote -----------------

def test_topic0_liquidity_values_are_all_distinct_and_from_swap_topics():
    liquidity_topics = {
        m._V2_MINT_TOPIC, m._V2_BURN_TOPIC, m._V3_MINT_TOPIC,
        m._V3_BURN_TOPIC, m._V4_MODIFY_LIQUIDITY_TOPIC,
    }
    assert len(liquidity_topics) == 5
    swap_topics = {m._SYNC_TOPIC, m._SYNC_TOPIC_AERODROME, m._V2_SWAP_TOPIC, m._V3_SWAP_TOPIC, m._V4_SWAP_TOPIC}
    assert liquidity_topics.isdisjoint(swap_topics)


def _v2_liquidity_data(amount0: int, amount1: int) -> str:
    return "0x" + (_u256(amount0) + _u256(amount1)).hex()


def test_handle_v2_mint_adds_liquidity_quote_currency0_true():
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    pool = _pool(family="v2", token_is_currency0=True, decimals0=18, decimals1=6)
    feed._pools["0xpool"] = pool
    # quote is currency1 (6 dec): 50 quote units added.
    data = _v2_liquidity_data(amount0=3 * 10**18, amount1=50 * 10**6)
    feed._handle_v2_mint("0xpool", {"data": data})
    assert pool.liquidity_added_quote == pytest.approx(50.0)
    assert pool.liquidity_removed_quote == pytest.approx(0.0)
    # non-contamination: swap_count/buy_volume/sell_volume untouched.
    assert pool.swap_count == 0
    assert pool.buy_count == 0
    assert pool.sell_count == 0


def test_handle_v2_mint_adds_liquidity_quote_currency0_false():
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    pool = _pool(family="v2", token_is_currency0=False, decimals0=6, decimals1=18)
    feed._pools["0xpool"] = pool
    # quote is currency0 (6 dec): 40 quote units added.
    data = _v2_liquidity_data(amount0=40 * 10**6, amount1=2 * 10**18)
    feed._handle_v2_mint("0xpool", {"data": data})
    assert pool.liquidity_added_quote == pytest.approx(40.0)


def test_handle_v2_burn_removes_liquidity_quote_currency0_true():
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    pool = _pool(family="v2", token_is_currency0=True, decimals0=18, decimals1=6)
    feed._pools["0xpool"] = pool
    data = _v2_liquidity_data(amount0=1 * 10**18, amount1=25 * 10**6)
    feed._handle_v2_burn("0xpool", {"data": data})
    assert pool.liquidity_removed_quote == pytest.approx(25.0)
    assert pool.liquidity_added_quote == pytest.approx(0.0)
    assert pool.swap_count == 0
    assert pool.buy_count == 0
    assert pool.sell_count == 0


def test_handle_v2_burn_removes_liquidity_quote_currency0_false():
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    pool = _pool(family="v2", token_is_currency0=False, decimals0=6, decimals1=18)
    feed._pools["0xpool"] = pool
    data = _v2_liquidity_data(amount0=15 * 10**6, amount1=1 * 10**18)
    feed._handle_v2_burn("0xpool", {"data": data})
    assert pool.liquidity_removed_quote == pytest.approx(15.0)


def test_handle_v2_mint_ignores_unknown_pool():
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    data = _v2_liquidity_data(1, 1)
    feed._handle_v2_mint("0xunknown", {"data": data})  # must not raise


def test_handle_v2_burn_ignores_unknown_pool():
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    data = _v2_liquidity_data(1, 1)
    feed._handle_v2_burn("0xunknown", {"data": data})  # must not raise


def _v3_mint_data(amount0: int, amount1: int, *, sender: int = 0, liquidity_l: int = 0) -> str:
    return "0x" + (_u256(sender) + _u256(liquidity_l) + _u256(amount0) + _u256(amount1)).hex()


def _v3_burn_data(amount0: int, amount1: int, *, liquidity_l: int = 0) -> str:
    return "0x" + (_u256(liquidity_l) + _u256(amount0) + _u256(amount1)).hex()


def test_handle_v3_mint_adds_liquidity_quote_currency0_true():
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    pool = _pool(family="v3", token_is_currency0=True, decimals0=18, decimals1=18)
    feed._pools["0xpool"] = pool
    data = _v3_mint_data(amount0=2 * 10**18, amount1=7 * 10**18)
    feed._handle_v3_mint("0xpool", {"data": data})
    assert pool.liquidity_added_quote == pytest.approx(7.0)
    assert pool.swap_count == 0
    assert pool.buy_count == 0
    assert pool.sell_count == 0


def test_handle_v3_mint_adds_liquidity_quote_currency0_false():
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    pool = _pool(family="v3", token_is_currency0=False, decimals0=18, decimals1=18)
    feed._pools["0xpool"] = pool
    data = _v3_mint_data(amount0=9 * 10**18, amount1=4 * 10**18)
    feed._handle_v3_mint("0xpool", {"data": data})
    assert pool.liquidity_added_quote == pytest.approx(9.0)


def test_handle_v3_burn_removes_liquidity_quote_currency0_true():
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    pool = _pool(family="v3", token_is_currency0=True, decimals0=18, decimals1=18)
    feed._pools["0xpool"] = pool
    data = _v3_burn_data(amount0=3 * 10**18, amount1=6 * 10**18)
    feed._handle_v3_burn("0xpool", {"data": data})
    assert pool.liquidity_removed_quote == pytest.approx(6.0)
    assert pool.liquidity_added_quote == pytest.approx(0.0)
    assert pool.swap_count == 0
    assert pool.buy_count == 0
    assert pool.sell_count == 0


def test_handle_v3_burn_removes_liquidity_quote_currency0_false():
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    pool = _pool(family="v3", token_is_currency0=False, decimals0=18, decimals1=18)
    feed._pools["0xpool"] = pool
    data = _v3_burn_data(amount0=8 * 10**18, amount1=1 * 10**18)
    feed._handle_v3_burn("0xpool", {"data": data})
    assert pool.liquidity_removed_quote == pytest.approx(8.0)


def test_handle_v3_mint_ignores_unknown_pool():
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    data = _v3_mint_data(1, 1)
    feed._handle_v3_mint("0xunknown", {"data": data})  # must not raise


def test_handle_v3_burn_ignores_unknown_pool():
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    data = _v3_burn_data(1, 1)
    feed._handle_v3_burn("0xunknown", {"data": data})  # must not raise


def _v4_modify_liquidity_data(liquidity_delta_signed: int, *, tick_lower: int = 0, tick_upper: int = 0, salt: int = 0) -> str:
    delta_u256 = liquidity_delta_signed % (1 << 256)
    return "0x" + (_u256(tick_lower) + _u256(tick_upper) + _u256(delta_u256) + _u256(salt)).hex()


def test_handle_v4_modify_liquidity_added_positive_delta():
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    pool_id_raw = bytes.fromhex("11" * 32)
    pool = m._TrackedPool(
        dex_id="uniswap_v4", family="v4", token_is_currency0=True,
        decimals0=18, decimals1=18, quote_is_weth=False, quote_is_stable=False,
        pool_id_hex="0x" + pool_id_raw.hex(),
    )
    feed._pools["0x" + pool_id_raw.hex()] = pool
    data = _v4_modify_liquidity_data(12_345)
    topics = [MagicMock(), _FakeTopic(pool_id_raw), _FakeTopic(bytes.fromhex("00" * 12 + "aa" * 20))]
    feed._handle_v4_modify_liquidity(topics, {"data": data})
    assert pool.liquidity_added_raw == pytest.approx(12_345.0)
    assert pool.liquidity_removed_raw == pytest.approx(0.0)
    assert pool.swap_count == 0
    assert pool.buy_count == 0
    assert pool.sell_count == 0


def test_handle_v4_modify_liquidity_removed_negative_delta():
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    pool_id_raw = bytes.fromhex("22" * 32)
    pool = m._TrackedPool(
        dex_id="uniswap_v4", family="v4", token_is_currency0=True,
        decimals0=18, decimals1=18, quote_is_weth=False, quote_is_stable=False,
        pool_id_hex="0x" + pool_id_raw.hex(),
    )
    feed._pools["0x" + pool_id_raw.hex()] = pool
    data = _v4_modify_liquidity_data(-6_789)
    topics = [MagicMock(), _FakeTopic(pool_id_raw), _FakeTopic(bytes.fromhex("00" * 12 + "bb" * 20))]
    feed._handle_v4_modify_liquidity(topics, {"data": data})
    assert pool.liquidity_removed_raw == pytest.approx(6_789.0)
    assert pool.liquidity_added_raw == pytest.approx(0.0)


def test_handle_v4_modify_liquidity_zero_delta_is_noop():
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    pool_id_raw = bytes.fromhex("33" * 32)
    pool = m._TrackedPool(
        dex_id="uniswap_v4", family="v4", token_is_currency0=True,
        decimals0=18, decimals1=18, quote_is_weth=False, quote_is_stable=False,
        pool_id_hex="0x" + pool_id_raw.hex(),
    )
    feed._pools["0x" + pool_id_raw.hex()] = pool
    data = _v4_modify_liquidity_data(0)
    topics = [MagicMock(), _FakeTopic(pool_id_raw), _FakeTopic(bytes.fromhex("00" * 32))]
    feed._handle_v4_modify_liquidity(topics, {"data": data})
    assert pool.liquidity_added_raw == pytest.approx(0.0)
    assert pool.liquidity_removed_raw == pytest.approx(0.0)


def test_handle_v4_modify_liquidity_ignores_unmapped_pool_id():
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    data = _v4_modify_liquidity_data(100)
    topics = [MagicMock(), _FakeTopic(bytes.fromhex("00" * 32)), _FakeTopic(bytes.fromhex("00" * 32))]
    feed._handle_v4_modify_liquidity(topics, {"data": data})  # must not raise, no pool registered


@pytest.mark.asyncio
async def test_resubscribe_includes_liquidity_topics_in_the_v2v3_filter():
    """Confirms the 4 new v2/v3 topic0s are actually wired into the real
    subscription filter, not just decodable in isolation."""
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    feed._w3 = MagicMock()
    feed._w3.eth.subscribe = AsyncMock(return_value="sub_v2v3")
    feed._pools["0xpool"] = m._TrackedPool(
        dex_id="uniswap_v2", family="v2", token_is_currency0=True,
        decimals0=18, decimals1=18, quote_is_weth=False, quote_is_stable=False,
    )
    await feed._resubscribe()
    filter_arg = feed._w3.eth.subscribe.call_args[0][1]
    for topic in (m._V2_MINT_TOPIC, m._V2_BURN_TOPIC, m._V3_MINT_TOPIC, m._V3_BURN_TOPIC):
        assert topic in filter_arg["topics"][0]


@pytest.mark.asyncio
async def test_resubscribe_includes_modify_liquidity_topic_in_the_v4_filter():
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    feed._w3 = MagicMock()
    feed._w3.eth.subscribe = AsyncMock(return_value="sub_v4")
    pool_id_raw = bytes.fromhex("44" * 32)
    feed._pools["0x" + pool_id_raw.hex()] = m._TrackedPool(
        dex_id="uniswap_v4", family="v4", token_is_currency0=True,
        decimals0=18, decimals1=18, quote_is_weth=False, quote_is_stable=False,
        pool_id_hex="0x" + pool_id_raw.hex(),
    )
    await feed._resubscribe()
    filter_arg = feed._w3.eth.subscribe.call_args[0][1]
    assert m._V4_MODIFY_LIQUIDITY_TOPIC in filter_arg["topics"][0]


def test_get_snapshot_exposes_liquidity_fields():
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    pool = _pool(family="v2", token_is_currency0=True, decimals0=18, decimals1=6)
    pool.ticks.append((time.monotonic(), 1.0))
    pool.liquidity_added_quote = 100.0
    pool.liquidity_removed_quote = 40.0
    pool.liquidity_added_raw = 500.0
    pool.liquidity_removed_raw = 200.0
    feed._pools["0xpool"] = pool
    feed._connected = True
    snap = feed.get_snapshot("0xpool")
    assert snap.liquidity_added_quote == pytest.approx(100.0)
    assert snap.liquidity_removed_quote == pytest.approx(40.0)
    assert snap.liquidity_added_raw == pytest.approx(500.0)
    assert snap.liquidity_removed_raw == pytest.approx(200.0)


def test_net_liquidity_properties_computed_not_stored():
    snap = m.EVMSwapSnapshot(
        available=True, liquidity_added_quote=100.0, liquidity_removed_quote=30.0,
        liquidity_added_raw=500.0, liquidity_removed_raw=800.0,
    )
    assert snap.net_liquidity_quote == pytest.approx(70.0)
    assert snap.net_liquidity_delta_raw == pytest.approx(-300.0)


def test_mixed_swap_mint_burn_sequence_never_contaminates_swap_or_buysell_counters():
    """Integration-level: a real interleaved sequence of a v3 swap, a v3
    mint, and a v3 burn on the same pool -- swap_count/buy_count/sell_count
    must stay exactly what the swap alone would have produced."""
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    pool = _pool(family="v3", token_is_currency0=True, decimals0=18, decimals1=18)
    feed._pools["0xpool"] = pool

    # 1) a real buy swap
    raw = _v3v4_raw(amount0_signed=-2 * 10**18, amount1_signed=3 * 10**18)
    feed._handle_v3_swap("0xpool", [MagicMock(), MagicMock()], {"data": "0x" + raw.hex()})
    # 2) a mint (liquidity added)
    feed._handle_v3_mint("0xpool", {"data": _v3_mint_data(amount0=1 * 10**18, amount1=5 * 10**18)})
    # 3) a burn (liquidity removed)
    feed._handle_v3_burn("0xpool", {"data": _v3_burn_data(amount0=1 * 10**18, amount1=2 * 10**18)})

    assert pool.swap_count == 1
    assert pool.buy_count == 1
    assert pool.sell_count == 0
    assert pool.buy_volume_quote == pytest.approx(3.0)
    assert pool.liquidity_added_quote == pytest.approx(5.0)
    assert pool.liquidity_removed_quote == pytest.approx(2.0)


# --- _handle_notification: topic0 dispatch, "0x" prefix normalization ------

def test_handle_notification_normalizes_missing_0x_prefix_and_dispatches_sync():
    """24/08 real bug: HexBytes.hex() has no '0x' prefix, unlike this
    module's own topic constants -- an unnormalized comparison silently
    matched nothing, ever. Locks in the fix."""
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    pool = m._TrackedPool(
        dex_id="uniswap_v2", family="v2", token_is_currency0=True,
        decimals0=18, decimals1=6, quote_is_weth=False, quote_is_stable=True,
    )
    feed._pools["0xpool"] = pool
    data = _u256(10 * 10**18) + _u256(20 * 10**6)
    bare_topic0 = _FakeTopic(bytes.fromhex(m._SYNC_TOPIC[2:]))  # no "0x" prefix, like real HexBytes
    payload = {"result": {
        "topics": [bare_topic0], "address": "0xpool", "data": "0x" + data.hex(),
    }}
    feed._handle_notification(payload)
    # 29/08: Sync alone no longer increments swap_count (moved to the real
    # V2 Swap event) -- the price update is what proves the dispatch fired.
    assert len(pool.ticks) == 1
    assert pool.ticks[-1][1] == pytest.approx(2.0)


def test_handle_notification_dispatches_v2_swap():
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    pool = _pool(family="v2", token_is_currency0=True, decimals0=18, decimals1=6)
    feed._pools["0xpool"] = pool
    data = _v2_swap_data(amount0_in=0, amount1_in=20 * 10**6, amount0_out=5 * 10**18, amount1_out=0)
    bare_topic0 = _FakeTopic(bytes.fromhex(m._V2_SWAP_TOPIC[2:]))
    sender_topic = _FakeTopic(bytes.fromhex("00" * 12 + "cc" * 20))
    payload = {"result": {
        "topics": [bare_topic0, sender_topic, MagicMock()], "address": "0xpool", "data": data,
    }}
    feed._handle_notification(payload)
    assert pool.swap_count == 1
    assert "0x" + "cc" * 20 in pool.distinct_traders


def test_handle_notification_ignores_empty_result():
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    feed._handle_notification({"result": None})  # must not raise
    feed._handle_notification({})  # must not raise


def test_handle_notification_never_raises_on_malformed_payload():
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    feed._handle_notification({"result": {"topics": [_FakeTopic(b"\x00")], "address": "0xpool", "data": "not_hex"}})


# --- add_pool / remove_pool: on-chain self-verification, mocked w3 ----------

@pytest.mark.asyncio
async def test_add_pool_v2v3_refuses_when_tracked_token_matches_neither_side():
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    fake_w3 = MagicMock()
    fake_w3.to_checksum_address = lambda a: a
    contract = MagicMock()
    contract.functions.token0.return_value.call = AsyncMock(return_value="0xtoken0")
    contract.functions.token1.return_value.call = AsyncMock(return_value="0xtoken1")
    fake_w3.eth.contract.return_value = contract
    feed._w3 = fake_w3
    ok = await feed.add_pool("0xpool", dex_id="uniswap_v3", token_address="0xsomethingelse")
    assert ok is False
    assert "0xpool" not in feed._pools


@pytest.mark.asyncio
async def test_add_pool_v2v3_registers_and_is_idempotent():
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    fake_w3 = MagicMock()
    fake_w3.to_checksum_address = lambda a: a
    contract = MagicMock()
    contract.functions.token0.return_value.call = AsyncMock(return_value="0xtoken0")
    contract.functions.token1.return_value.call = AsyncMock(return_value="0xtoken1")
    contract.functions.decimals.return_value.call = AsyncMock(return_value=18)
    fake_w3.eth.contract.return_value = contract
    fake_w3.eth.subscribe = AsyncMock(return_value="sub1")
    feed._w3 = fake_w3
    ok = await feed.add_pool("0xpool", dex_id="uniswap_v3", token_address="0xtoken0")
    assert ok is True
    assert "0xpool" in feed._pools
    assert feed._pools["0xpool"].token_is_currency0 is True

    # Re-adding the same pool must not re-verify / duplicate.
    fake_w3.eth.contract.reset_mock()
    ok_again = await feed.add_pool("0xpool", dex_id="uniswap_v3", token_address="0xtoken0")
    assert ok_again is True
    fake_w3.eth.contract.assert_not_called()


@pytest.mark.asyncio
async def test_add_pool_v2v3_sets_quote_is_btc_for_a_cbbtc_quoted_pool():
    """27/08 -- cbBTC quote-token support: add_pool must flag a cbBTC-quoted
    pool distinctly from a WETH-quoted one (different USD rate resolution)."""
    cbbtc = "0xcbb7c0000ab88b473b1f5afd9ef808440eed33bf"
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    fake_w3 = MagicMock()
    fake_w3.to_checksum_address = lambda a: a
    contract = MagicMock()
    contract.functions.token0.return_value.call = AsyncMock(return_value="0xtoken0")
    contract.functions.token1.return_value.call = AsyncMock(return_value=cbbtc)
    contract.functions.decimals.return_value.call = AsyncMock(return_value=8)
    fake_w3.eth.contract.return_value = contract
    fake_w3.eth.subscribe = AsyncMock(return_value="sub1")
    feed._w3 = fake_w3
    ok = await feed.add_pool("0xpool", dex_id="uniswap_v3", token_address="0xtoken0")
    assert ok is True
    pool = feed._pools["0xpool"]
    assert pool.quote_is_btc is True
    assert pool.quote_is_weth is False
    assert pool.quote_is_stable is False


@pytest.mark.asyncio
async def test_add_pool_v2v3_fetches_real_decimals_when_not_overridden():
    """24/08 real gap: the previous 18/18 default silently mispriced any pool
    where the tracked token isn't 18-decimal (the near-universal convention,
    but never guaranteed for a fresh meme token) -- decimals are now fetched
    on-chain per side, matching whichever token is token0 vs token1."""
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    fake_w3 = MagicMock()
    fake_w3.to_checksum_address = lambda a: a
    contract = MagicMock()
    contract.functions.token0.return_value.call = AsyncMock(return_value="0xtoken0")
    contract.functions.token1.return_value.call = AsyncMock(return_value="0xtoken1")

    def _fake_contract(address, abi):
        c = MagicMock()
        decimals_map = {"0xtoken0": 6, "0xtoken1": 18, "0xpool": None}
        if address in ("0xtoken0", "0xtoken1"):
            c.functions.decimals.return_value.call = AsyncMock(return_value=decimals_map[address])
        else:
            c.functions.token0.return_value.call = AsyncMock(return_value="0xtoken0")
            c.functions.token1.return_value.call = AsyncMock(return_value="0xtoken1")
        return c

    fake_w3.eth.contract.side_effect = _fake_contract
    fake_w3.eth.subscribe = AsyncMock(return_value="sub1")
    feed._w3 = fake_w3
    ok = await feed.add_pool("0xpool", dex_id="uniswap_v3", token_address="0xtoken0")
    assert ok is True
    assert feed._pools["0xpool"].decimals0 == 6
    assert feed._pools["0xpool"].decimals1 == 18


@pytest.mark.asyncio
async def test_add_pool_v2v3_defaults_to_18_when_decimals_call_fails():
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    fake_w3 = MagicMock()
    fake_w3.to_checksum_address = lambda a: a

    def _fake_contract(address, abi):
        c = MagicMock()
        if address in ("0xtoken0", "0xtoken1"):
            c.functions.decimals.return_value.call = AsyncMock(side_effect=RuntimeError("no decimals()"))
        else:
            c.functions.token0.return_value.call = AsyncMock(return_value="0xtoken0")
            c.functions.token1.return_value.call = AsyncMock(return_value="0xtoken1")
        return c

    fake_w3.eth.contract.side_effect = _fake_contract
    fake_w3.eth.subscribe = AsyncMock(return_value="sub1")
    feed._w3 = fake_w3
    ok = await feed.add_pool("0xpool", dex_id="uniswap_v3", token_address="0xtoken0")
    assert ok is True
    assert feed._pools["0xpool"].decimals0 == 18
    assert feed._pools["0xpool"].decimals1 == 18


@pytest.mark.asyncio
async def test_add_pool_v2v3_explicit_decimals_skip_the_rpc_call():
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    fake_w3 = MagicMock()
    fake_w3.to_checksum_address = lambda a: a
    contract = MagicMock()
    contract.functions.token0.return_value.call = AsyncMock(return_value="0xtoken0")
    contract.functions.token1.return_value.call = AsyncMock(return_value="0xtoken1")
    fake_w3.eth.contract.return_value = contract
    fake_w3.eth.subscribe = AsyncMock(return_value="sub1")
    feed._w3 = fake_w3
    ok = await feed.add_pool(
        "0xpool", dex_id="uniswap_v3", token_address="0xtoken0", decimals0=6, decimals1=18,
    )
    assert ok is True
    assert feed._pools["0xpool"].decimals0 == 6
    assert feed._pools["0xpool"].decimals1 == 18
    contract.functions.decimals.assert_not_called()


@pytest.mark.asyncio
async def test_add_pool_returns_false_for_unmapped_dex_id():
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    feed._w3 = MagicMock()
    ok = await feed.add_pool("0xpool", dex_id="some_random_dex", token_address="0xtoken0")
    assert ok is False


@pytest.mark.asyncio
async def test_add_pool_returns_false_when_not_yet_connected():
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    assert feed._w3 is None
    ok = await feed.add_pool("0xpool", dex_id="uniswap_v3", token_address="0xtoken0")
    assert ok is False


@pytest.mark.asyncio
async def test_add_pool_v2v3_never_raises_on_rpc_failure():
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    fake_w3 = MagicMock()
    fake_w3.to_checksum_address = lambda a: a
    contract = MagicMock()
    contract.functions.token0.return_value.call = AsyncMock(side_effect=RuntimeError("rpc down"))
    fake_w3.eth.contract.return_value = contract
    feed._w3 = fake_w3
    ok = await feed.add_pool("0xpool", dex_id="uniswap_v3", token_address="0xtoken0")
    assert ok is False  # never raises


@pytest.mark.asyncio
async def test_add_pool_v4_trusts_caller_supplied_currency_side():
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    fake_w3 = MagicMock()
    fake_w3.eth.subscribe = AsyncMock(return_value="sub1")
    feed._w3 = fake_w3
    ok = await feed.add_pool("0xabc123", dex_id="uniswap_v4", token_address="currency0")
    assert ok is True
    assert feed._pools["0xabc123"].token_is_currency0 is True
    assert feed._pools["0xabc123"].family == "v4"


# --- add_pool / _handle_notification: daily RU budget (24/08) --------------

@pytest.mark.asyncio
async def test_add_pool_refuses_when_daily_budget_exhausted():
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    fake_w3 = MagicMock()
    fake_w3.eth.subscribe = AsyncMock(return_value="sub1")
    feed._w3 = fake_w3
    chainstack_ru_budget.record_usage_fast("base", chainstack_ru_budget.cap_for("base"))
    ok = await feed.add_pool("0xabc123", dex_id="uniswap_v4", token_address="currency0")
    assert ok is False
    assert "0xabc123" not in feed._pools


@pytest.mark.asyncio
async def test_add_pool_unaffected_by_a_different_chains_exhausted_budget():
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    fake_w3 = MagicMock()
    fake_w3.eth.subscribe = AsyncMock(return_value="sub1")
    feed._w3 = fake_w3
    chainstack_ru_budget.record_usage_fast("robinhood", chainstack_ru_budget.cap_for("robinhood"))
    ok = await feed.add_pool("0xabc123", dex_id="uniswap_v4", token_address="currency0")
    assert ok is True  # base's own budget is untouched


@pytest.mark.asyncio
async def test_handle_notification_counts_one_ru_per_push_including_newheads():
    """24/08 -- every push is billed 1 RU regardless of content, confirmed
    live (see evm_swap_ws.py's own newHeads-keepalive fix docstring) --
    counted before the topics filter so newHeads itself (no topics) is
    counted too, not just decoded Sync/Swap events."""
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    feed._handle_notification({"result": {"number": "0x123"}})  # newHeads-shaped: no topics
    feed._handle_notification({"result": {"topics": []}})
    status = await chainstack_ru_budget.daily_status("base")
    assert status["used_units"] == 2


@pytest.mark.asyncio
async def test_handle_notification_never_counts_an_empty_payload():
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    feed._handle_notification({})  # no "result" key at all
    feed._handle_notification(None)
    status = await chainstack_ru_budget.daily_status("base")
    assert status["used_units"] == 0


# --- mid-day circuit breaker (24/08) ----------------------------------

@pytest.mark.asyncio
async def test_breaker_opens_and_unsubscribes_when_budget_exhausted():
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    fake_w3 = MagicMock()
    fake_w3.eth.subscribe = AsyncMock(return_value="sub1")
    fake_w3.eth.unsubscribe = AsyncMock()
    feed._w3 = fake_w3
    feed._pools["0xpool"] = _pool()
    feed._active_sub_ids = ["sub1"]
    chainstack_ru_budget.record_usage_fast("base", chainstack_ru_budget.cap_for("base"))

    await feed._check_budget_circuit_breaker()

    assert feed.breaker_open is True
    assert "0xpool" not in feed._pools
    assert "0xpool" in feed._evicted_pools
    fake_w3.eth.unsubscribe.assert_awaited_with("sub1")


@pytest.mark.asyncio
async def test_breaker_opens_even_with_nothing_tracked():
    """25/08, real bug found live: the original guard (`and self._pools`)
    meant the breaker never opened while nothing was tracked -- exactly the
    state the newHeads keepalive runs in (see _resubscribe()'s docstring).
    A 295k/200k daily overshoot on Robinhood in production never once
    triggered "CIRCUIT BREAKER OPEN" because of this. The breaker must open
    on budget exhaustion alone, whether or not there is a pool to evict --
    its job now includes shutting the keepalive off too."""
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    chainstack_ru_budget.record_usage_fast("base", chainstack_ru_budget.cap_for("base"))
    await feed._check_budget_circuit_breaker()
    assert feed.breaker_open is True


@pytest.mark.asyncio
async def test_breaker_closes_the_newheads_keepalive_with_nothing_else_tracked():
    """25/08, the actual real-world case: nothing tracked, only the newHeads
    keepalive open -- the single largest cost on a fast chain (Robinhood,
    ~100ms blocks, ~36k RU/hour for this alone). The breaker must unsubscribe
    it, not just leave it running because there was no pool to evict."""
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    fake_w3 = MagicMock()
    fake_w3.eth.subscribe = AsyncMock(return_value="newheads-sub")
    fake_w3.eth.unsubscribe = AsyncMock()
    feed._w3 = fake_w3
    feed._newheads_sub_id = "newheads-sub"  # already open, nothing else tracked
    chainstack_ru_budget.record_usage_fast("base", chainstack_ru_budget.cap_for("base"))

    await feed._check_budget_circuit_breaker()

    assert feed.breaker_open is True
    assert feed._newheads_sub_id is None
    fake_w3.eth.unsubscribe.assert_awaited_with("newheads-sub")


@pytest.mark.asyncio
async def test_resubscribe_never_reopens_newheads_while_breaker_is_open():
    """Twin of the test above, from _resubscribe()'s own side: even when
    called directly (e.g. add_pool()/remove_pool() racing the breaker check),
    it must never reopen the keepalive it just closed -- that would silently
    undo the breaker the next time anything touches the pool set."""
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    fake_w3 = MagicMock()
    fake_w3.eth.subscribe = AsyncMock(return_value="newheads-sub")
    fake_w3.eth.unsubscribe = AsyncMock()
    feed._w3 = fake_w3
    feed._breaker_open = True

    await feed._resubscribe()

    fake_w3.eth.subscribe.assert_not_awaited()
    assert feed._newheads_sub_id is None


@pytest.mark.asyncio
async def test_breaker_closes_and_restores_pools_once_budget_resets():
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    fake_w3 = MagicMock()
    fake_w3.eth.subscribe = AsyncMock(return_value="sub1")
    fake_w3.eth.unsubscribe = AsyncMock()
    feed._w3 = fake_w3
    feed._breaker_open = True
    feed._evicted_pools = {"0xpool": _pool()}

    await feed._check_budget_circuit_breaker()  # budget was never spent in this test -- can_spend is True

    assert feed.breaker_open is False
    assert "0xpool" in feed._pools
    assert feed._evicted_pools == {}
    fake_w3.eth.subscribe.assert_awaited()  # real re-subscription issued, not just a memory move


@pytest.mark.asyncio
async def test_breaker_never_reverifies_a_restored_pool_on_chain():
    """Restoring must reuse the ALREADY-VERIFIED _TrackedPool as-is -- no
    fresh token0()/token1()/decimals() RPC round-trip, which would both
    waste real RU and defeat the point of a budget guardrail."""
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    fake_w3 = MagicMock()
    fake_w3.eth.subscribe = AsyncMock(return_value="sub1")
    fake_w3.eth.contract = MagicMock(side_effect=AssertionError("must not re-verify on-chain"))
    feed._w3 = fake_w3
    original = _pool(decimals0=9, decimals1=6)
    feed._breaker_open = True
    feed._evicted_pools = {"0xpool": original}

    await feed._check_budget_circuit_breaker()

    assert feed._pools["0xpool"] is original


@pytest.mark.asyncio
async def test_breaker_open_then_close_is_idempotent_without_flapping():
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    fake_w3 = MagicMock()
    fake_w3.eth.subscribe = AsyncMock(return_value="sub1")
    fake_w3.eth.unsubscribe = AsyncMock()
    feed._w3 = fake_w3
    feed._pools["0xpool"] = _pool()
    chainstack_ru_budget.record_usage_fast("base", chainstack_ru_budget.cap_for("base"))

    await feed._check_budget_circuit_breaker()
    assert feed.breaker_open is True
    subscribe_calls_after_open = fake_w3.eth.subscribe.await_count

    await feed._check_budget_circuit_breaker()  # still exhausted -- must not re-evict/re-open
    assert feed.breaker_open is True
    assert fake_w3.eth.subscribe.await_count == subscribe_calls_after_open  # no redundant churn


@pytest.mark.asyncio
async def test_remove_pool_drops_an_evicted_pool_without_resubscribing():
    """A position closing while the breaker is open must not sit in
    _evicted_pools to be pointlessly re-subscribed once the budget resets,
    and closing an already-unsubscribed pool needs no real _resubscribe()
    call (nothing was subscribed for it in the first place)."""
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    fake_w3 = MagicMock()
    fake_w3.eth.subscribe = AsyncMock(return_value="sub1")
    feed._w3 = fake_w3
    feed._breaker_open = True
    feed._evicted_pools = {"0xpool": _pool()}

    await feed.remove_pool("0xpool")

    assert "0xpool" not in feed._evicted_pools
    fake_w3.eth.subscribe.assert_not_awaited()


@pytest.mark.asyncio
async def test_start_and_stop_manage_the_breaker_task_lifecycle():
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    feed._run = AsyncMock(side_effect=asyncio.CancelledError)  # never actually connect
    await feed.start()
    assert feed._breaker_task is not None
    await feed.stop()
    assert feed._breaker_task is None


@pytest.mark.asyncio
async def test_remove_pool_drops_tracking_and_is_a_noop_if_absent():
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    fake_w3 = MagicMock()
    fake_w3.eth.subscribe = AsyncMock(return_value="sub1")
    feed._w3 = fake_w3
    feed._pools["0xpool"] = _pool()
    await feed.remove_pool("0xpool")
    assert "0xpool" not in feed._pools
    await feed.remove_pool("0xpool")  # second call, must not raise


# --- _resubscribe: v4 pools route through the shared PoolManager address ---

@pytest.mark.asyncio
async def test_resubscribe_returns_none_without_a_connection():
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    assert await feed._resubscribe() is None


@pytest.mark.asyncio
async def test_resubscribe_opens_newheads_keepalive_with_zero_pools():
    """24/08 fix: with no real pool tracked, process_subscriptions() would
    exit immediately without SOME active subscription -- newHeads is billed
    1 RU/push same as any other subscription, so it must still be opened
    here rather than left as a separate always-on call (the pre-fix
    behaviour, which cost real money on fast chains like Robinhood Chain's
    100ms block time even once real pools were tracked)."""
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    feed._w3 = MagicMock()
    feed._w3.eth.subscribe = AsyncMock(return_value="sub_newheads")
    assert await feed._resubscribe() == "sub_newheads"
    feed._w3.eth.subscribe.assert_called_once_with("newHeads")
    assert feed._newheads_sub_id == "sub_newheads"


@pytest.mark.asyncio
async def test_resubscribe_closes_newheads_keepalive_once_a_real_pool_is_tracked():
    """The keepalive is pure waste once a real logs subscription exists to
    keep the generator alive on its own -- must be closed, not left running
    alongside it."""
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    feed._w3 = MagicMock()
    feed._w3.eth.subscribe = AsyncMock(side_effect=["sub_newheads", "sub_v3"])
    feed._w3.eth.unsubscribe = AsyncMock(return_value=True)
    await feed._resubscribe()  # zero pools -- opens the keepalive
    assert feed._newheads_sub_id == "sub_newheads"

    feed._pools["0xv3pool"] = m._TrackedPool(
        dex_id="uniswap_v3", family="v3", token_is_currency0=True,
        decimals0=18, decimals1=18, quote_is_weth=False, quote_is_stable=False,
    )
    await feed._resubscribe()
    feed._w3.eth.unsubscribe.assert_called_once_with("sub_newheads")
    assert feed._newheads_sub_id is None
    assert feed._active_sub_ids == ["sub_v3"]


@pytest.mark.asyncio
async def test_resubscribe_reopens_newheads_keepalive_after_last_pool_removed():
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    feed._w3 = MagicMock()
    feed._w3.eth.subscribe = AsyncMock(side_effect=["sub_v3", "sub_newheads2"])
    feed._pools["0xv3pool"] = m._TrackedPool(
        dex_id="uniswap_v3", family="v3", token_is_currency0=True,
        decimals0=18, decimals1=18, quote_is_weth=False, quote_is_stable=False,
    )
    await feed._resubscribe()
    assert feed._newheads_sub_id is None

    feed._pools.pop("0xv3pool")
    await feed._resubscribe()
    assert feed._newheads_sub_id == "sub_newheads2"
    assert feed._active_sub_ids == []


@pytest.mark.asyncio
async def test_resubscribe_includes_pool_manager_address_for_v4_pools():
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    feed._w3 = MagicMock()
    feed._w3.eth.subscribe = AsyncMock(return_value="sub1")
    feed._pools["0xpoolid"] = m._TrackedPool(
        dex_id="uniswap_v4", family="v4", token_is_currency0=True,
        decimals0=18, decimals1=18, quote_is_weth=False, quote_is_stable=False,
        pool_id_hex="0xpoolid",
    )
    await feed._resubscribe()
    call_args = feed._w3.eth.subscribe.call_args
    filter_arg = call_args[0][1]
    assert m.POOL_MANAGER_ADDRESS in filter_arg["address"]


@pytest.mark.asyncio
async def test_resubscribe_uses_robinhoods_own_pool_manager_address_not_bases():
    """25/08 real bug fix: v4 subscriptions on Robinhood must use Robinhood's
    OWN PoolManager, never Base's (doppler.POOL_MANAGER_ADDRESS is explicitly
    Base-only per its own docstring) -- the pre-fix code always used the Base
    constant regardless of ``self.chain``, silently pointing every Robinhood
    v4 subscription at the wrong contract."""
    feed = m.EVMSwapWebSocketFeed(chain="robinhood", ws_url="wss://test.invalid", chain_id=4663)
    feed._w3 = MagicMock()
    feed._w3.eth.subscribe = AsyncMock(return_value="sub_v4")
    feed._pools["0xpoolid"] = m._TrackedPool(
        dex_id="uniswap_v4", family="v4", token_is_currency0=True,
        decimals0=18, decimals1=18, quote_is_weth=False, quote_is_stable=False,
        pool_id_hex="0xpoolid",
    )
    await feed._resubscribe()
    filter_arg = feed._w3.eth.subscribe.call_args[0][1]
    assert filter_arg["address"] == [m._POOL_MANAGER_BY_CHAIN["robinhood"]]
    assert filter_arg["address"] != [m.POOL_MANAGER_ADDRESS]


@pytest.mark.asyncio
async def test_resubscribe_restricts_v4_filter_to_tracked_pool_ids():
    """24/08 real incident: the PoolManager is a SINGLETON shared by every
    v4 pool on the chain -- an address-only filter received every swap on
    every v4 pool on Base, not just the tracked ones, discarded only after
    being received and billed (measured live: a real Alchemy CU spike).
    topics[1] must carry the tracked poolIds so the RPC node itself does
    the filtering, never this module after the fact."""
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    feed._w3 = MagicMock()
    feed._w3.eth.subscribe = AsyncMock(return_value="sub_v4")
    feed._pools["0xpoolid"] = m._TrackedPool(
        dex_id="uniswap_v4", family="v4", token_is_currency0=True,
        decimals0=18, decimals1=18, quote_is_weth=False, quote_is_stable=False,
        pool_id_hex="0xpoolid",
    )
    await feed._resubscribe()
    filter_arg = feed._w3.eth.subscribe.call_args[0][1]
    # 29/08, brique 3/5 -- topics[0] also carries _V4_MODIFY_LIQUIDITY_TOPIC
    # now (same already-open filter, zero new subscription); poolIds
    # restriction (topics[1]) is the real invariant this test guards and
    # stays unchanged.
    assert filter_arg["topics"] == [[m._V4_SWAP_TOPIC, m._V4_MODIFY_LIQUIDITY_TOPIC], ["0xpoolid"]]


@pytest.mark.asyncio
async def test_resubscribe_issues_two_separate_subscriptions_for_v2v3_and_v4():
    """A v2/v3 pool's own address filter would be broken by a shared
    topics[1] restriction meant for v4's poolId -- eth_subscribe's topics
    list is positional across every address in the SAME filter, so v2/v3
    and v4 cannot share one subscription once v4 gets a topics[1] filter."""
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    feed._w3 = MagicMock()
    feed._w3.eth.subscribe = AsyncMock(side_effect=["sub_v2v3", "sub_v4"])
    feed._pools["0xv3pool"] = m._TrackedPool(
        dex_id="uniswap_v3", family="v3", token_is_currency0=True,
        decimals0=18, decimals1=18, quote_is_weth=False, quote_is_stable=False,
    )
    feed._pools["0xpoolid"] = m._TrackedPool(
        dex_id="uniswap_v4", family="v4", token_is_currency0=True,
        decimals0=18, decimals1=18, quote_is_weth=False, quote_is_stable=False,
        pool_id_hex="0xpoolid",
    )
    await feed._resubscribe()
    assert feed._w3.eth.subscribe.call_count == 2
    v2v3_filter = feed._w3.eth.subscribe.call_args_list[0][0][1]
    v4_filter = feed._w3.eth.subscribe.call_args_list[1][0][1]
    assert v2v3_filter["address"] == ["0xv3pool"]
    assert v4_filter["address"] == [m.POOL_MANAGER_ADDRESS]
    assert feed._active_sub_ids == ["sub_v2v3", "sub_v4"]


@pytest.mark.asyncio
async def test_resubscribe_closes_previous_subscriptions_before_reissuing():
    """24/08 real incident: no unsubscribe was ever called, so every
    add_pool()/remove_pool() left the PREVIOUS subscription alive alongside
    the new one -- ~100 pools added one at a time (the early-discovery
    experiment) left ~100 overlapping subscriptions, each separately
    re-delivering every matching event. This is the fix's core assertion."""
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    feed._w3 = MagicMock()
    feed._w3.eth.subscribe = AsyncMock(side_effect=["sub1", "sub2"])
    feed._w3.eth.unsubscribe = AsyncMock(return_value=True)
    feed._pools["0xv3pool"] = m._TrackedPool(
        dex_id="uniswap_v3", family="v3", token_is_currency0=True,
        decimals0=18, decimals1=18, quote_is_weth=False, quote_is_stable=False,
    )
    await feed._resubscribe()
    assert feed._active_sub_ids == ["sub1"]

    feed._pools["0xv3pool2"] = m._TrackedPool(
        dex_id="uniswap_v3", family="v3", token_is_currency0=True,
        decimals0=18, decimals1=18, quote_is_weth=False, quote_is_stable=False,
    )
    await feed._resubscribe()
    feed._w3.eth.unsubscribe.assert_called_once_with("sub1")
    assert feed._active_sub_ids == ["sub2"]


@pytest.mark.asyncio
async def test_resubscribe_never_raises_when_unsubscribe_fails():
    """A subscription id from a connection that already dropped is
    meaningless to unsubscribe -- best-effort, never blocks re-subscribing."""
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    feed._w3 = MagicMock()
    feed._w3.eth.subscribe = AsyncMock(return_value="sub_new")
    feed._w3.eth.unsubscribe = AsyncMock(side_effect=RuntimeError("stale subscription"))
    feed._active_sub_ids = ["sub_stale"]
    feed._pools["0xv3pool"] = m._TrackedPool(
        dex_id="uniswap_v3", family="v3", token_is_currency0=True,
        decimals0=18, decimals1=18, quote_is_weth=False, quote_is_stable=False,
    )
    await feed._resubscribe()  # must not raise
    assert feed._active_sub_ids == ["sub_new"]


# --- proactive idle newHeads close (25/08) ----------------------------

@pytest.mark.asyncio
async def test_check_idle_newheads_is_a_noop_when_newheads_not_open():
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    feed._w3 = MagicMock()
    feed._w3.eth.unsubscribe = AsyncMock()
    feed._pools_empty_since = time.monotonic() - 1000.0  # idle for ages
    await feed._check_idle_newheads()
    feed._w3.eth.unsubscribe.assert_not_awaited()


@pytest.mark.asyncio
async def test_check_idle_newheads_is_a_noop_before_the_window_elapses():
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    feed._w3 = MagicMock()
    feed._w3.eth.unsubscribe = AsyncMock()
    feed._newheads_sub_id = "newheads-sub"
    feed._pools_empty_since = time.monotonic() - 10.0  # well under the 120s window
    await feed._check_idle_newheads()
    feed._w3.eth.unsubscribe.assert_not_awaited()
    assert feed._newheads_sub_id == "newheads-sub"


@pytest.mark.asyncio
async def test_check_idle_newheads_closes_the_keepalive_once_the_window_elapses():
    """25/08, the actual point of this feature: proactively shut the
    keepalive off once nothing has been tracked for _IDLE_NEWHEADS_CLOSE_
    SECONDS, without ever needing the daily RU budget to be exhausted first."""
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    fake_w3 = MagicMock()
    fake_w3.eth.subscribe = AsyncMock(return_value="newheads-sub")
    fake_w3.eth.unsubscribe = AsyncMock()
    feed._w3 = fake_w3
    feed._newheads_sub_id = "newheads-sub"
    feed._pools_empty_since = time.monotonic() - (m._IDLE_NEWHEADS_CLOSE_SECONDS + 5.0)

    await feed._check_idle_newheads()

    fake_w3.eth.unsubscribe.assert_awaited_with("newheads-sub")
    assert feed._newheads_sub_id is None


@pytest.mark.asyncio
async def test_resubscribe_reopens_newheads_promptly_once_pools_go_idle_but_recent():
    """A pool closing must not instantly cut the keepalive -- only after the
    full idle window elapses (see the two tests above). Right after going
    idle, _resubscribe() must still (re)open it normally."""
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    fake_w3 = MagicMock()
    fake_w3.eth.subscribe = AsyncMock(return_value="newheads-sub")
    fake_w3.eth.unsubscribe = AsyncMock()
    feed._w3 = fake_w3

    await feed._resubscribe()  # pools already empty, freshly so

    fake_w3.eth.subscribe.assert_awaited_with("newHeads")
    assert feed._newheads_sub_id == "newheads-sub"
    assert feed._pools_empty_since is not None


@pytest.mark.asyncio
async def test_resubscribe_clears_idle_tracking_once_a_pool_is_tracked_again():
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    fake_w3 = MagicMock()
    fake_w3.eth.subscribe = AsyncMock(return_value="sub1")
    fake_w3.eth.unsubscribe = AsyncMock()
    feed._w3 = fake_w3
    feed._pools_empty_since = time.monotonic() - 500.0  # was idle a while
    feed._pools["0xv3pool"] = m._TrackedPool(
        dex_id="uniswap_v3", family="v3", token_is_currency0=True,
        decimals0=18, decimals1=18, quote_is_weth=False, quote_is_stable=False,
    )

    await feed._resubscribe()

    assert feed._pools_empty_since is None


# --- specs/015-robinhood-chainstack-only: provenance (tx_hash/block_number) -

def test_handle_sync_records_tx_hash_and_block_number_from_the_event():
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    pool = m._TrackedPool(
        dex_id="uniswap_v2", family="v2", token_is_currency0=True,
        decimals0=18, decimals1=6, quote_is_weth=False, quote_is_stable=True,
    )
    feed._pools["0xpool"] = pool
    feed._connected = True
    data = _u256(10 * 10**18) + _u256(20 * 10**6)
    feed._handle_sync("0xpool", {
        "data": "0x" + data.hex(), "transactionHash": "abc123", "blockNumber": "0x2a",
    })
    assert pool.last_tx_hash == "0xabc123"
    assert pool.last_block_number == 42
    snapshot = feed.get_snapshot("0xpool")
    assert snapshot.tx_hash == "0xabc123"
    assert snapshot.block_number == 42


def test_handle_v3_swap_records_tx_hash_and_block_number_from_the_event():
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    pool = m._TrackedPool(
        dex_id="uniswap_v3", family="v3", token_is_currency0=True,
        decimals0=18, decimals1=18, quote_is_weth=False, quote_is_stable=False,
    )
    feed._pools["0xpool"] = pool
    sqrt_price = _sqrt_price_x96_for_price(2.0)
    data = _u256(1) + _u256(1) + _u256(sqrt_price) + _u256(500) + _u256(0)
    sender_topic = _FakeTopic(bytes.fromhex("00" * 12 + "aa" * 20))
    feed._handle_v3_swap("0xpool", [MagicMock(), sender_topic], {
        "data": "0x" + data.hex(), "transactionHash": "def456", "blockNumber": 99,
    })
    assert pool.last_tx_hash == "0xdef456"
    assert pool.last_block_number == 99


def test_get_snapshot_tx_hash_and_block_number_default_none_when_never_set():
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    pool = m._TrackedPool(
        dex_id="uniswap_v2", family="v2", token_is_currency0=True,
        decimals0=18, decimals1=6, quote_is_weth=False, quote_is_stable=True,
    )
    pool.ticks.append((time.monotonic(), 2.0))
    feed._pools["0xpool"] = pool
    feed._connected = True
    snapshot = feed.get_snapshot("0xpool")
    assert snapshot.tx_hash is None
    assert snapshot.block_number is None


# --- specs/015-robinhood-chainstack-only: resolve_cold (targeted eth_call) --

@pytest.mark.asyncio
async def test_resolve_cold_v2_full_read_matches_the_live_decoder_formula():
    """Same reserve0/reserve1/decimals as test_handle_sync_computes_price_
    and_exact_reserve_usd_for_stable_quote -- proves the cold-read path
    produces the IDENTICAL price/reserve_usd the live Sync decoder would,
    per the operator's own vigilance point (never a re-derived formula)."""
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    pool = m._TrackedPool(
        dex_id="uniswap_v2", family="v2", token_is_currency0=True,
        decimals0=18, decimals1=6, quote_is_weth=False, quote_is_stable=True,
    )
    feed._pools["0xpool"] = pool
    fake_w3 = MagicMock()
    fake_w3.to_checksum_address = lambda a: a
    contract = MagicMock()
    contract.functions.getReserves.return_value.call = AsyncMock(
        return_value=(10 * 10**18, 20 * 10**6, 0)
    )
    fake_w3.eth.contract.return_value = contract
    feed._w3 = fake_w3

    snapshot = await feed.resolve_cold("0xpool")

    assert snapshot.available is True
    assert snapshot.price_quote == pytest.approx(2.0)
    assert snapshot.price_usd == pytest.approx(2.0)
    assert snapshot.reserve_usd == pytest.approx(40.0)
    assert snapshot.quote_reserve_raw == pytest.approx(20.0)
    assert snapshot.tx_hash is None  # cold read, never a decoded event
    assert snapshot.block_number is None


@pytest.mark.asyncio
async def test_resolve_cold_v2_weth_quote_leaves_reserve_usd_none_but_exposes_raw():
    """28/08, specs/015 -- mirrors test_handle_sync_records_quote_reserve_
    raw_for_a_weth_quote for the cold-read path: reserve_usd stays None
    (never fabricated without a real ETH rate), but quote_reserve_raw is
    still populated so the caller (check_candidates) can convert it."""
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    pool = m._TrackedPool(
        dex_id="uniswap_v2", family="v2", token_is_currency0=True,
        decimals0=18, decimals1=18, quote_is_weth=True, quote_is_stable=False,
    )
    feed._pools["0xpool"] = pool
    fake_w3 = MagicMock()
    fake_w3.to_checksum_address = lambda a: a
    contract = MagicMock()
    contract.functions.getReserves.return_value.call = AsyncMock(
        return_value=(10 * 10**18, 5 * 10**18, 0)
    )
    fake_w3.eth.contract.return_value = contract
    feed._w3 = fake_w3

    snapshot = await feed.resolve_cold("0xpool")

    assert snapshot.available is True
    assert snapshot.reserve_usd is None
    assert snapshot.price_usd is None
    assert snapshot.quote_reserve_raw == pytest.approx(5.0)
    assert snapshot.quote_is_weth is True


@pytest.mark.asyncio
async def test_resolve_cold_v2_zero_reserve_is_unavailable_never_fabricated():
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    pool = m._TrackedPool(
        dex_id="uniswap_v2", family="v2", token_is_currency0=True,
        decimals0=18, decimals1=6, quote_is_weth=False, quote_is_stable=True,
    )
    feed._pools["0xpool"] = pool
    fake_w3 = MagicMock()
    fake_w3.to_checksum_address = lambda a: a
    contract = MagicMock()
    contract.functions.getReserves.return_value.call = AsyncMock(return_value=(0, 20 * 10**6, 0))
    fake_w3.eth.contract.return_value = contract
    feed._w3 = fake_w3

    snapshot = await feed.resolve_cold("0xpool")

    assert snapshot.available is False


@pytest.mark.asyncio
async def test_resolve_cold_v3_full_read_matches_the_live_decoder_formula():
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    pool = m._TrackedPool(
        dex_id="uniswap_v3", family="v3", token_is_currency0=True,
        decimals0=18, decimals1=18, quote_is_weth=False, quote_is_stable=True,
    )
    feed._pools["0xpool"] = pool
    sqrt_price = _sqrt_price_x96_for_price(2.0)
    fake_w3 = MagicMock()
    fake_w3.to_checksum_address = lambda a: a
    contract = MagicMock()
    contract.functions.slot0.return_value.call = AsyncMock(
        return_value=(sqrt_price, 0, 0, 0, 0, 0, True)
    )
    contract.functions.liquidity.return_value.call = AsyncMock(return_value=123456)
    fake_w3.eth.contract.return_value = contract
    feed._w3 = fake_w3

    snapshot = await feed.resolve_cold("0xpool")

    assert snapshot.available is True
    assert snapshot.price_quote == pytest.approx(2.0, rel=1e-4)
    assert snapshot.raw_liquidity == pytest.approx(123456.0)


@pytest.mark.asyncio
async def test_resolve_cold_v3_partial_read_never_falls_through_as_priceable():
    """Operator's explicit vigilance point: reserve/liquidity resolving while
    price fails (or vice versa) must NEVER produce an available=True
    snapshot -- this is the exact 'subtler fabrication' class this feature
    exists to close."""
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    pool = m._TrackedPool(
        dex_id="uniswap_v3", family="v3", token_is_currency0=True,
        decimals0=18, decimals1=18, quote_is_weth=False, quote_is_stable=True,
    )
    feed._pools["0xpool"] = pool
    sqrt_price = _sqrt_price_x96_for_price(2.0)
    fake_w3 = MagicMock()
    fake_w3.to_checksum_address = lambda a: a
    contract = MagicMock()
    contract.functions.slot0.return_value.call = AsyncMock(
        return_value=(sqrt_price, 0, 0, 0, 0, 0, True)
    )
    contract.functions.liquidity.return_value.call = AsyncMock(side_effect=Exception("rpc timeout"))
    fake_w3.eth.contract.return_value = contract
    feed._w3 = fake_w3

    snapshot = await feed.resolve_cold("0xpool")

    assert snapshot.available is False


@pytest.mark.asyncio
async def test_resolve_cold_v4_is_never_supported_returns_unavailable():
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    feed._pools["0xv4pool"] = m._TrackedPool(
        dex_id="uniswap_v4", family="v4", token_is_currency0=True,
        decimals0=18, decimals1=18, quote_is_weth=False, quote_is_stable=False,
    )
    feed._w3 = MagicMock()

    snapshot = await feed.resolve_cold("0xv4pool")

    assert snapshot.available is False


@pytest.mark.asyncio
async def test_resolve_cold_registers_an_untracked_pool_before_reading():
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    fake_w3 = MagicMock()
    fake_w3.to_checksum_address = lambda a: a
    contract = MagicMock()
    contract.functions.token0.return_value.call = AsyncMock(return_value="0xtoken0")
    contract.functions.token1.return_value.call = AsyncMock(return_value="0xtoken1")
    contract.functions.decimals.return_value.call = AsyncMock(return_value=18)
    contract.functions.getReserves.return_value.call = AsyncMock(
        return_value=(10 * 10**18, 20 * 10**18, 0)
    )
    fake_w3.eth.contract.return_value = contract
    fake_w3.eth.subscribe = AsyncMock(return_value="sub1")
    feed._w3 = fake_w3

    snapshot = await feed.resolve_cold("0xpool", dex_id="uniswap_v2", token_address="0xtoken0")

    assert snapshot.available is True
    assert "0xpool" in feed._pools


@pytest.mark.asyncio
async def test_resolve_cold_returns_unavailable_when_untracked_and_no_registration_args():
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    feed._w3 = MagicMock()
    snapshot = await feed.resolve_cold("0xneverseen")
    assert snapshot.available is False


# --- specs/015-robinhood-chainstack-only: resolve_token_symbol (cosmetic) ---

@pytest.mark.asyncio
async def test_resolve_token_symbol_returns_the_real_symbol():
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    fake_w3 = MagicMock()
    fake_w3.to_checksum_address = lambda a: a
    contract = MagicMock()
    contract.functions.symbol.return_value.call = AsyncMock(return_value="PUMP")
    fake_w3.eth.contract.return_value = contract
    feed._w3 = fake_w3
    symbol = await feed.resolve_token_symbol("0xtoken")
    assert symbol == "PUMP"


@pytest.mark.asyncio
async def test_resolve_token_symbol_returns_none_on_failure_never_raises():
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    fake_w3 = MagicMock()
    fake_w3.to_checksum_address = lambda a: a
    contract = MagicMock()
    contract.functions.symbol.return_value.call = AsyncMock(side_effect=Exception("no symbol()"))
    fake_w3.eth.contract.return_value = contract
    feed._w3 = fake_w3
    symbol = await feed.resolve_token_symbol("0xtoken")
    assert symbol is None


# --- brique 2/5: net_flow_quote, mixed sequences, invariants, get_snapshot --

def test_net_flow_quote_is_buy_minus_sell():
    snapshot = m.EVMSwapSnapshot(available=True, buy_volume_quote=30.0, sell_volume_quote=12.0)
    assert snapshot.net_flow_quote == pytest.approx(18.0)


def test_net_flow_quote_defaults_to_zero_when_no_swaps():
    snapshot = m.EVMSwapSnapshot(available=True)
    assert snapshot.net_flow_quote == pytest.approx(0.0)


def test_mixed_sequence_of_buys_and_sells_keeps_counters_consistent():
    """A real mixed sequence -- buy, sell, buy -- on the same v2 pool.
    Integration-level, not just one isolated event."""
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    pool = _pool(family="v2", token_is_currency0=True, decimals0=18, decimals1=6)
    feed._pools["0xpool"] = pool
    buy1 = _v2_swap_data(amount0_in=0, amount1_in=10 * 10**6, amount0_out=1 * 10**18, amount1_out=0)
    sell1 = _v2_swap_data(amount0_in=1 * 10**18, amount1_in=0, amount0_out=0, amount1_out=4 * 10**6)
    buy2 = _v2_swap_data(amount0_in=0, amount1_in=5 * 10**6, amount0_out=1 * 10**17, amount1_out=0)
    for data in (buy1, sell1, buy2):
        feed._handle_v2_swap("0xpool", [MagicMock(), MagicMock()], {"data": data})
    assert pool.swap_count == 3
    assert pool.buy_count == 2
    assert pool.sell_count == 1
    assert pool.undetermined_count == 0
    assert pool.buy_volume_quote == pytest.approx(15.0)
    assert pool.sell_volume_quote == pytest.approx(4.0)
    assert pool.buy_count + pool.sell_count + pool.undetermined_count == pool.swap_count
    assert pool.buy_volume_quote + pool.sell_volume_quote + pool.undetermined_volume_quote == pytest.approx(
        pool.cumulative_volume_quote
    )


def test_invariant_buy_sell_undetermined_sums_to_swap_count_and_volume_with_an_undetermined_swap():
    """The invariant must hold even when an undetermined swap is mixed in --
    it is always-true by construction, never an approximation that only
    works on a 100% clean snapshot."""
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    pool = _pool(family="v2", token_is_currency0=True, decimals0=18, decimals1=6)
    feed._pools["0xpool"] = pool
    buy1 = _v2_swap_data(amount0_in=0, amount1_in=10 * 10**6, amount0_out=1 * 10**18, amount1_out=0)
    ambiguous = _v2_swap_data(amount0_in=0, amount1_in=3 * 10**6, amount0_out=0, amount1_out=3 * 10**6)
    for data in (buy1, ambiguous):
        feed._handle_v2_swap("0xpool", [MagicMock(), MagicMock()], {"data": data})
    assert pool.swap_count == 2
    assert pool.buy_count == 1
    assert pool.sell_count == 0
    assert pool.undetermined_count == 1
    assert pool.buy_count + pool.sell_count + pool.undetermined_count == pool.swap_count
    assert pool.buy_volume_quote + pool.sell_volume_quote + pool.undetermined_volume_quote == pytest.approx(
        pool.cumulative_volume_quote
    )


def test_get_snapshot_exposes_buy_sell_fields():
    feed = m.EVMSwapWebSocketFeed(chain="base", ws_url="wss://test.invalid", chain_id=8453)
    pool = _pool(family="v2", token_is_currency0=True, decimals0=18, decimals1=6)
    feed._pools["0xpool"] = pool
    feed._connected = True
    data = _v2_swap_data(amount0_in=0, amount1_in=10 * 10**6, amount0_out=1 * 10**18, amount1_out=0)
    feed._handle_v2_swap("0xpool", [MagicMock(), MagicMock()], {"data": data})
    pool.ticks.append((time.monotonic(), 1.0))
    snap = feed.get_snapshot("0xpool")
    assert snap.buy_count == 1
    assert snap.sell_count == 0
    assert snap.undetermined_count == 0
    assert snap.buy_volume_quote == pytest.approx(10.0)
    assert snap.net_flow_quote == pytest.approx(10.0)
