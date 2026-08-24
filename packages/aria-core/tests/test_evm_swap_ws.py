"""services/evm_swap_ws.py -- decode logic (pure, offset-verified) and the
live feed's in-memory snapshot/state logic. Never a real network call; the
websocket connection is never exercised here (start()/_run() are covered
implicitly by the module's own defensive try/except, same posture as
pumpswap_ws.py). Same rigor as every other service test in this dome."""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from aria_core.services import evm_swap_ws as m


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
    assert pool.swap_count == 1
    assert pool.last_reserve_usd == pytest.approx(40.0)  # 2 * quote_reserve (20)
    assert pool.ticks[-1][1] == pytest.approx(2.0)


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
    assert pool.swap_count == 1


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
    assert filter_arg["topics"] == [[m._V4_SWAP_TOPIC], ["0xpoolid"]]


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
