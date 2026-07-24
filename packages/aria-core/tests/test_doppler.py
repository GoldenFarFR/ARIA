"""Doppler protocol on-chain price reads (07/24) -- no real RPC/HTTP call, w3
and httpx.AsyncClient injected/monkeypatched (same doctrine as
test_basenames.py/test_tangem_bridge.py).

price_from_sqrt_price_x96's formula was cross-checked against a REAL on-chain
data point before this module was written (never trusted on textbook formula
alone): CLOWNS's initialization event carried both sqrtPriceX96
(10764248344314596577690877662916079) and tick (236400) in the SAME log --
squaring (sqrtPriceX96/2**96) and comparing to 1.0001**tick matched to within
floating-point rounding. The real bug this cross-check caught before commit:
the token_is_currency0 branch was originally inverted, producing a $31
trillion price for CLOWNS instead of ~$0.00000011 -- the regression test
below locks in the corrected direction."""
from __future__ import annotations

import httpx
import pytest

from aria_core.services import doppler

CLOWNS = "0x8DfBb49b689644454cf9D924fD426E1df53c2bA3"
WETH = doppler.WETH_ADDRESS
REAL_SQRT_PRICE_X96_AT_INIT = 10764248344314596577690877662916079
REAL_TICK_AT_INIT = 236400


# ── price_from_sqrt_price_x96 (pure math, no mock) ──────────────────────────

def test_price_matches_independent_tick_calculation():
    """Cross-check against 1.0001**tick from the SAME real on-chain event --
    the two must agree to within floating-point rounding."""
    price_currency1_per_currency0 = doppler.price_from_sqrt_price_x96(
        REAL_SQRT_PRICE_X96_AT_INIT, token_is_currency0=False,
    )
    # token_is_currency0=False returns the INVERSE (currency0 per currency1)
    # -- invert back to compare against the raw currency1/currency0 ratio.
    raw_ratio = 1.0 / price_currency1_per_currency0
    price_from_tick = 1.0001 ** REAL_TICK_AT_INIT
    assert raw_ratio == pytest.approx(price_from_tick, rel=1e-9)


def test_clowns_regression_price_is_tiny_not_trillions():
    """Regression lock for the real inversion bug found before commit: CLOWNS
    (currency1, WETH=currency0) must price at a fraction of a cent, never
    $31 trillion."""
    price_weth_per_token = doppler.price_from_sqrt_price_x96(
        REAL_SQRT_PRICE_X96_AT_INIT, token_is_currency0=False,
    )
    assert price_weth_per_token == pytest.approx(5.417410556253322e-11, rel=1e-9)


def test_token_as_currency0_returns_uninverted_ratio():
    # sqrtPriceX96 such that raw_ratio == 4.0 exactly (sqrt(4)=2, 2*2**96).
    sqrt_price_x96 = 2 * doppler._Q96
    price = doppler.price_from_sqrt_price_x96(sqrt_price_x96, token_is_currency0=True)
    assert price == pytest.approx(4.0)


def test_decimals_adjustment_applied():
    # currency0 (token) has 6 decimals, currency1 (WETH) has 18 -- a 10**12 gap.
    sqrt_price_x96 = 2 * doppler._Q96  # raw_ratio == 4.0
    price = doppler.price_from_sqrt_price_x96(
        sqrt_price_x96, token_is_currency0=True, decimals0=6, decimals1=18,
    )
    assert price == pytest.approx(4.0 * (10 ** (6 - 18)))


# ── find_pool (fake w3) ──────────────────────────────────────────────────────

class _FakeLog:
    def __init__(self, args):
        self.args = args

    def __getitem__(self, key):
        if key == "args":
            return self.args
        raise KeyError(key)


class _FakeInitializeEvent:
    def __init__(self, logs_by_arg):
        self._logs_by_arg = logs_by_arg
        self.captured_calls = []

    def get_logs(self, *, from_block, to_block, argument_filters):
        self.captured_calls.append((from_block, to_block, argument_filters))
        (arg_name, arg_value), = argument_filters.items()
        return self._logs_by_arg.get((arg_name, arg_value), [])


class _FakePoolManagerContract:
    def __init__(self, event):
        self.events = type("Events", (), {"Initialize": lambda self_: event})()


class _FakeEth:
    def __init__(self, *, initialize_event=None):
        self._initialize_event = initialize_event

    def contract(self, address, abi):
        fn_names = {f.get("name") for f in abi}
        if "Initialize" in fn_names:
            return _FakePoolManagerContract(self._initialize_event)
        raise AssertionError(f"unexpected ABI in test fake: {fn_names}")


class _FakeW3:
    def __init__(self, *, initialize_event=None):
        self.eth = _FakeEth(initialize_event=initialize_event)

    def to_checksum_address(self, addr):
        return addr


def test_find_pool_matches_on_currency1():
    log = _FakeLog({
        "id": b"\x01" * 32, "currency0": WETH, "currency1": CLOWNS, "hooks": "0xHOOK",
    })
    event = _FakeInitializeEvent({("currency1", CLOWNS): [log]})
    w3 = _FakeW3(initialize_event=event)

    pool = doppler.find_pool(CLOWNS, w3=w3)
    assert pool == {"pool_id": b"\x01" * 32, "currency0": WETH, "currency1": CLOWNS, "hooks": "0xHOOK"}


def test_find_pool_matches_on_currency0():
    """Never assumes the token is currency1 -- also checks currency0."""
    log = _FakeLog({
        "id": b"\x02" * 32, "currency0": CLOWNS, "currency1": WETH, "hooks": "0xHOOK",
    })
    event = _FakeInitializeEvent({("currency0", CLOWNS): [log]})
    w3 = _FakeW3(initialize_event=event)

    pool = doppler.find_pool(CLOWNS, w3=w3)
    assert pool == {"pool_id": b"\x02" * 32, "currency0": CLOWNS, "currency1": WETH, "hooks": "0xHOOK"}


def test_find_pool_returns_none_when_no_pool_exists():
    event = _FakeInitializeEvent({})
    w3 = _FakeW3(initialize_event=event)
    assert doppler.find_pool(CLOWNS, w3=w3) is None


def test_find_pool_degrades_on_rpc_error():
    class _RaisingEth:
        def contract(self, address, abi):
            raise RuntimeError("RPC down")

    class _RaisingW3:
        eth = _RaisingEth()

        def to_checksum_address(self, addr):
            return addr

    assert doppler.find_pool(CLOWNS, w3=_RaisingW3()) is None


# ── read_slot0 (fake w3) ─────────────────────────────────────────────────────

class _FakeSlot0Call:
    def __init__(self, value):
        self._value = value

    def call(self):
        return self._value


class _FakeStateViewFunctions:
    def __init__(self, slot0_value):
        self._slot0_value = slot0_value

    def getSlot0(self, pool_id):
        return _FakeSlot0Call(self._slot0_value)


class _FakeStateViewContract:
    def __init__(self, functions):
        self.functions = functions


class _FakeEthStateView:
    def __init__(self, slot0_value):
        self._slot0_value = slot0_value

    def contract(self, address, abi):
        fn_names = {f.get("name") for f in abi}
        if "getSlot0" in fn_names:
            return _FakeStateViewContract(_FakeStateViewFunctions(self._slot0_value))
        raise AssertionError(f"unexpected ABI in test fake: {fn_names}")


class _FakeW3StateView:
    def __init__(self, slot0_value):
        self.eth = _FakeEthStateView(slot0_value)

    def to_checksum_address(self, addr):
        return addr


def test_read_slot0_returns_price_and_tick():
    w3 = _FakeW3StateView((REAL_SQRT_PRICE_X96_AT_INIT, REAL_TICK_AT_INIT, 0, 500))
    result = doppler.read_slot0(b"\x01" * 32, w3=w3)
    assert result == (REAL_SQRT_PRICE_X96_AT_INIT, REAL_TICK_AT_INIT)


def test_read_slot0_degrades_on_rpc_error():
    class _RaisingEth:
        def contract(self, address, abi):
            raise RuntimeError("RPC down")

    class _RaisingW3:
        eth = _RaisingEth()

        def to_checksum_address(self, addr):
            return addr

    assert doppler.read_slot0(b"\x01" * 32, w3=_RaisingW3()) is None


# ── _find_launch_block_via_blockscout (fake httpx) ──────────────────────────

class _FakeResponse:
    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json = json_data or {}

    def json(self):
        return self._json


class _FakeAsyncClient:
    _pages = []  # list of (status_code, json_data), consumed in order

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, params=None):
        if not type(self)._pages:
            return _FakeResponse(status_code=500)
        status_code, json_data = type(self)._pages.pop(0)
        return _FakeResponse(status_code=status_code, json_data=json_data)


@pytest.fixture
def _fresh_httpx(monkeypatch):
    _FakeAsyncClient._pages = []
    monkeypatch.setattr(doppler.httpx, "AsyncClient", _FakeAsyncClient)
    return _FakeAsyncClient


@pytest.mark.asyncio
async def test_find_launch_block_single_page(_fresh_httpx):
    _fresh_httpx._pages = [
        (200, {"items": [{"block_number": 48806300}, {"block_number": 48806237}], "next_page_params": None}),
    ]
    block = await doppler._find_launch_block_via_blockscout(CLOWNS)
    assert block == 48806237


@pytest.mark.asyncio
async def test_find_launch_block_follows_pagination(_fresh_httpx):
    _fresh_httpx._pages = [
        (200, {"items": [{"block_number": 48900000}], "next_page_params": {"cursor": "abc"}}),
        (200, {"items": [{"block_number": 48806237}], "next_page_params": None}),
    ]
    block = await doppler._find_launch_block_via_blockscout(CLOWNS)
    assert block == 48806237


@pytest.mark.asyncio
async def test_find_launch_block_no_transfers_returns_none(_fresh_httpx):
    _fresh_httpx._pages = [(200, {"items": [], "next_page_params": None})]
    assert await doppler._find_launch_block_via_blockscout(CLOWNS) is None


@pytest.mark.asyncio
async def test_find_launch_block_http_error_returns_none(_fresh_httpx):
    _fresh_httpx._pages = [(500, {})]
    assert await doppler._find_launch_block_via_blockscout(CLOWNS) is None


@pytest.mark.asyncio
async def test_find_launch_block_capped_at_max_pages(_fresh_httpx, monkeypatch):
    monkeypatch.setattr(doppler, "_LAUNCH_BLOCK_MAX_PAGES", 2)
    _fresh_httpx._pages = [
        (200, {"items": [{"block_number": 3}], "next_page_params": {"cursor": "a"}}),
        (200, {"items": [{"block_number": 2}], "next_page_params": {"cursor": "b"}}),
        (200, {"items": [{"block_number": 1}], "next_page_params": None}),
    ]
    # Only 2 pages consumed (cap) -- the oldest block seen within that cap.
    block = await doppler._find_launch_block_via_blockscout(CLOWNS)
    assert block == 2


# ── eth_usd_rate (mock coingecko) ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_eth_usd_rate_success(monkeypatch):
    from aria_core.services import coingecko

    class _FakeResult:
        available = True
        prices = {"ethereum": {"usd": 1859.16}}

    async def _fake_get_simple_price(self, coin_ids, *, vs_currencies=None):
        assert coin_ids == ["ethereum"]
        return _FakeResult()

    monkeypatch.setattr(coingecko.CoinGeckoClient, "get_simple_price", _fake_get_simple_price)
    rate = await doppler.eth_usd_rate()
    assert rate == pytest.approx(1859.16)


@pytest.mark.asyncio
async def test_eth_usd_rate_unavailable_returns_none(monkeypatch):
    from aria_core.services import coingecko

    class _FakeResult:
        available = False
        prices = {}

    async def _fake_get_simple_price(self, coin_ids, *, vs_currencies=None):
        return _FakeResult()

    monkeypatch.setattr(coingecko.CoinGeckoClient, "get_simple_price", _fake_get_simple_price)
    assert await doppler.eth_usd_rate() is None


@pytest.mark.asyncio
async def test_eth_usd_rate_network_error_returns_none(monkeypatch):
    from aria_core.services import coingecko

    async def _fake_get_simple_price(self, coin_ids, *, vs_currencies=None):
        raise RuntimeError("network down")

    monkeypatch.setattr(coingecko.CoinGeckoClient, "get_simple_price", _fake_get_simple_price)
    assert await doppler.eth_usd_rate() is None


# ── get_token_price_usd (end-to-end, everything mocked) ─────────────────────

@pytest.mark.asyncio
async def test_get_token_price_usd_end_to_end(monkeypatch, _fresh_httpx):
    _fresh_httpx._pages = [
        (200, {"items": [{"block_number": 48806237}], "next_page_params": None}),
    ]
    log = _FakeLog({"id": b"\x01" * 32, "currency0": WETH, "currency1": CLOWNS, "hooks": "0xHOOK"})
    event = _FakeInitializeEvent({("currency1", CLOWNS): [log]})

    class _FakeEthCombined:
        def contract(self, address, abi):
            fn_names = {f.get("name") for f in abi}
            if "Initialize" in fn_names:
                return _FakePoolManagerContract(event)
            return _FakeStateViewContract(
                _FakeStateViewFunctions((REAL_SQRT_PRICE_X96_AT_INIT, REAL_TICK_AT_INIT, 0, 500))
            )

    class _FakeW3Combined:
        eth = _FakeEthCombined()

        def to_checksum_address(self, addr):
            return addr

    async def _fake_eth_usd_rate():
        return 1859.16

    monkeypatch.setattr(doppler, "eth_usd_rate", _fake_eth_usd_rate)

    price = await doppler.get_token_price_usd(CLOWNS, w3=_FakeW3Combined())
    assert price == pytest.approx(5.417410556253322e-11 * 1859.16, rel=1e-6)


@pytest.mark.asyncio
async def test_get_token_price_usd_none_when_launch_block_not_found(_fresh_httpx):
    _fresh_httpx._pages = [(200, {"items": [], "next_page_params": None})]
    assert await doppler.get_token_price_usd(CLOWNS) is None


@pytest.mark.asyncio
async def test_get_token_price_usd_none_when_numeraire_is_not_weth(monkeypatch, _fresh_httpx):
    _fresh_httpx._pages = [
        (200, {"items": [{"block_number": 48806237}], "next_page_params": None}),
    ]
    other_stable = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"  # USDC on Base
    log = _FakeLog({"id": b"\x01" * 32, "currency0": other_stable, "currency1": CLOWNS, "hooks": "0xHOOK"})
    event = _FakeInitializeEvent({("currency1", CLOWNS): [log]})
    w3 = _FakeW3(initialize_event=event)

    price = await doppler.get_token_price_usd(CLOWNS, w3=w3)
    assert price is None
