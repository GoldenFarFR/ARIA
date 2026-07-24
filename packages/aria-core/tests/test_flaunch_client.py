"""Tests for the Flaunch discovery client (services/flaunch.py) -- no real
network call, everything mocked.

The "FUCK U HOODIE" on-chain fixture is a REAL PoolCreated log, decoded live
by Blockscout on 24/07. The API fixture is a REAL response from Flaunch's V2
API (same token, confirmed live once a real key was provisioned). See the
module docstring in services/flaunch.py for the full diligence trail."""

import pytest

from aria_core.services.blockscout import ContractLogsResult, DecodedLog
from aria_core.services.flaunch import (
    POSITION_MANAGER_ADDRESS,
    FlaunchClient,
    FlaunchToken,
    _parse_api_coin,
    _parse_pool_created,
)


def _no_api_key(monkeypatch):
    """Forces the on-chain fallback path -- most existing tests target that
    path specifically and shouldn't accidentally hit the API branch."""
    monkeypatch.setattr("aria_core.services.flaunch.flaunch_api_key", lambda: "")


def _real_pool_created_log(**overrides) -> DecodedLog:
    """Real PoolCreated log for "FUCK U HOODIE" (FUH), decoded live via
    Blockscout on 24/07 -- the exact `_params` tuple shape (11 fields,
    positional list, name/symbol/tokenUri always first) confirmed against
    the actual on-chain event, not guessed."""
    params = {
        "_poolId": "0x6e53c793aceaf4c88f7e10957df53f1d64a812886aa0c84bf7b366ce48ccc1fd",
        "_memecoin": "0xE9BB849A9f0e3441E1909914Af96Fb4BcF43A88B",
        "_memecoinTreasury": "0x163efD93e277D148D7938F3e0DE8D5B2E70e1616",
        "_tokenId": "113715",
        "_currencyFlipped": "false",
        "_flaunchFee": "0",
        "_params": [
            "FUCK U HOODIE",
            "FUH",
            "ipfs://QmcyH3KSkrzTZ9xHYFJz4WCzYLezDam75L191V69QzQ1wj",
            "0",
            "0",
            "0",
            "0x9EdbfA7e62E50bd56f8433FFbCb050Af620004c3",
            "10000",
            "0",
            "0x000000000000000000000000000000000000000000000000000002540be400",
            "0x",
        ],
    }
    params.update(overrides.pop("parameters", {}))
    return DecodedLog(
        method_call=(
            "PoolCreated(bytes32 indexed _poolId, address _memecoin, address _memecoinTreasury, "
            "uint256 _tokenId, bool _currencyFlipped, uint256 _flaunchFee, "
            "(string,string,string,uint256,uint256,uint256,address,uint24,uint256,bytes,bytes) _params)"
        ),
        parameters=params,
        block_number=overrides.get("block_number", 49029518),
        timestamp=overrides.get("timestamp", "2026-07-23T23:13:03.000000Z"),
        tx_hash=overrides.get("tx_hash", "0x253ca23e2b43e49a9408828a1d74105ba87934378a2f87b847b41a200eafcb25"),
    )


def _real_api_coin_payload(**overrides) -> dict:
    """Real entry from GET /v2/base/coins/top?sort=new, captured live 24/07
    (same "FUCK U HOODIE" token as the on-chain fixture above)."""
    payload = {
        "tokenAddress": "0xE9BB849A9f0e3441E1909914Af96Fb4BcF43A88B",
        "image": "https://i.flaunch.gg/token/0xE9BB849A9f0e3441E1909914Af96Fb4BcF43A88B",
        "symbol": "FUH",
        "name": "FUCK U HOODIE",
        "priceETH": "0.000000000053138399",
        "priceUSD": "0.00000010047621036516",
        "twentyFourHourChangePercentage": 0,
        "twentyFourHourVolume": "0",
        "twentyFourHourVolumeUSD": "0",
        "feesEarned": "0",
        "feesEarnedUSD": "0",
        "marketCapETH": "5.3138399",
        "marketCapUSD": "10047.6210365159986395156",
        "royaltyMembers": [{"address": "0x9EdbfA7e62E50bd56f8433FFbCb050Af620004c3", "percentage": 100}],
        "hourData": [],
    }
    payload.update(overrides)
    return payload


# ----------------------------------------------------------------------
# _parse_pool_created (on-chain path)
# ----------------------------------------------------------------------
def test_parse_pool_created_real_shape():
    log = _real_pool_created_log()
    token = _parse_pool_created(log)

    assert isinstance(token, FlaunchToken)
    assert token.contract == "0xE9BB849A9f0e3441E1909914Af96Fb4BcF43A88B"
    assert token.name == "FUCK U HOODIE"
    assert token.symbol == "FUH"
    assert token.token_uri == "ipfs://QmcyH3KSkrzTZ9xHYFJz4WCzYLezDam75L191V69QzQ1wj"
    assert token.treasury == "0x163efD93e277D148D7938F3e0DE8D5B2E70e1616"
    assert token.pool_id == "0x6e53c793aceaf4c88f7e10957df53f1d64a812886aa0c84bf7b366ce48ccc1fd"
    assert token.block_number == 49029518
    assert token.tx_hash == "0x253ca23e2b43e49a9408828a1d74105ba87934378a2f87b847b41a200eafcb25"


def test_parse_pool_created_no_memecoin_returns_none():
    log = DecodedLog(method_call="PoolCreated(...)", parameters={})
    assert _parse_pool_created(log) is None


def test_parse_pool_created_tolerates_shorter_params_tuple():
    """Older contract versions (V1.0) have a 10-field tuple instead of 11 --
    parsing must still work since only the first 3 fields are read (see
    services/flaunch.py's module docstring on why later fields aren't read)."""
    log = DecodedLog(
        method_call="PoolCreated(...)",
        parameters={
            "_memecoin": "0xTOKEN",
            "_params": ["Old Version Token", "OLD", "ipfs://old"],
        },
    )
    token = _parse_pool_created(log)
    assert token.name == "Old Version Token"
    assert token.symbol == "OLD"


def test_parse_pool_created_missing_params_tuple_degrades_gracefully():
    log = DecodedLog(method_call="PoolCreated(...)", parameters={"_memecoin": "0xTOKEN"})
    token = _parse_pool_created(log)
    assert token.contract == "0xTOKEN"
    assert token.name is None
    assert token.symbol is None


# ----------------------------------------------------------------------
# _parse_api_coin (V2 API path)
# ----------------------------------------------------------------------
def test_parse_api_coin_real_shape():
    token = _parse_api_coin(_real_api_coin_payload())

    assert isinstance(token, FlaunchToken)
    assert token.contract == "0xE9BB849A9f0e3441E1909914Af96Fb4BcF43A88B"
    assert token.name == "FUCK U HOODIE"
    assert token.symbol == "FUH"
    assert token.price_usd == pytest.approx(0.00000010047621036516)
    assert token.market_cap_usd == pytest.approx(10047.6210365159986395156)
    assert token.volume24h_usd == pytest.approx(0.0)


def test_parse_api_coin_no_token_address_returns_none():
    assert _parse_api_coin({"name": "no address"}) is None


def test_parse_api_coin_not_a_dict_returns_none():
    assert _parse_api_coin(["not", "a", "dict"]) is None
    assert _parse_api_coin(None) is None


def test_parse_api_coin_bad_numeric_fields_degrade_to_none():
    payload = _real_api_coin_payload(priceUSD="not-a-number", marketCapUSD=None)
    token = _parse_api_coin(payload)
    assert token.price_usd is None
    assert token.market_cap_usd is None


# ----------------------------------------------------------------------
# FlaunchClient.fetch_recent -- on-chain fallback path (no API key configured)
# ----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_fetch_recent_onchain_filters_to_pool_created_only(monkeypatch):
    _no_api_key(monkeypatch)
    other_log = DecodedLog(method_call="PoolSwap(uint256 amount)", parameters={"amount": "1"})
    result = ContractLogsResult(logs=[other_log, _real_pool_created_log()], available=True)

    class _FakeBlockscout:
        async def get_contract_logs_bounded(self, address, *, max_pages):
            assert address == POSITION_MANAGER_ADDRESS
            return result

    monkeypatch.setattr(
        "aria_core.services.blockscout.get_blockscout_client", lambda chain: _FakeBlockscout()
    )

    tokens = await FlaunchClient().fetch_recent(limit=50)
    assert len(tokens) == 1
    assert tokens[0].symbol == "FUH"


@pytest.mark.asyncio
async def test_fetch_recent_onchain_dedupes_by_contract(monkeypatch):
    _no_api_key(monkeypatch)
    log_a = _real_pool_created_log()
    log_b = _real_pool_created_log()  # same _memecoin -> should dedupe
    result = ContractLogsResult(logs=[log_a, log_b], available=True)

    class _FakeBlockscout:
        async def get_contract_logs_bounded(self, address, *, max_pages):
            return result

    monkeypatch.setattr(
        "aria_core.services.blockscout.get_blockscout_client", lambda chain: _FakeBlockscout()
    )

    tokens = await FlaunchClient().fetch_recent(limit=50)
    assert len(tokens) == 1


@pytest.mark.asyncio
async def test_fetch_recent_onchain_respects_limit(monkeypatch):
    _no_api_key(monkeypatch)
    logs = [
        _real_pool_created_log(parameters={"_memecoin": f"0xTOKEN{i}"}) for i in range(5)
    ]
    result = ContractLogsResult(logs=logs, available=True)

    class _FakeBlockscout:
        async def get_contract_logs_bounded(self, address, *, max_pages):
            return result

    monkeypatch.setattr(
        "aria_core.services.blockscout.get_blockscout_client", lambda chain: _FakeBlockscout()
    )

    tokens = await FlaunchClient().fetch_recent(limit=2)
    assert len(tokens) == 2


@pytest.mark.asyncio
async def test_fetch_recent_onchain_unavailable_returns_empty(monkeypatch):
    _no_api_key(monkeypatch)

    class _FakeBlockscout:
        async def get_contract_logs_bounded(self, address, *, max_pages):
            return ContractLogsResult(available=False, error="down")

    monkeypatch.setattr(
        "aria_core.services.blockscout.get_blockscout_client", lambda chain: _FakeBlockscout()
    )

    assert await FlaunchClient().fetch_recent() == []


@pytest.mark.asyncio
async def test_fetch_recent_onchain_network_error_degrades_gracefully(monkeypatch):
    _no_api_key(monkeypatch)

    class _Boom:
        async def get_contract_logs_bounded(self, address, *, max_pages):
            raise RuntimeError("network blocked")

    monkeypatch.setattr(
        "aria_core.services.blockscout.get_blockscout_client", lambda chain: _Boom()
    )

    with pytest.raises(RuntimeError):
        # fetch_recent's on-chain fallback doesn't itself catch -- the caller
        # (launchpad_discovery's _discover_flaunch_direct) is the one
        # responsible for the try/except, same contract as clanker_client.
        await FlaunchClient().fetch_recent()


# ----------------------------------------------------------------------
# FlaunchClient.fetch_recent -- V2 API path (key configured)
# ----------------------------------------------------------------------
class _FakeAPIResponse:
    def __init__(self, status_code: int, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx

            raise httpx.HTTPStatusError("error", request=None, response=self)


class _FakeAPIClient:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def get(self, url, params=None, headers=None):
        assert headers == {"X-Api-Key": "fl_live_test_key"}
        # Confirmed live 24/07: Authorization: Bearer is rejected server-side
        # (validated as a Google OAuth token) -- the client must never send it.
        assert "Authorization" not in (headers or {})
        return self._response


def _patch_api(monkeypatch, response):
    monkeypatch.setattr("aria_core.services.flaunch.flaunch_api_key", lambda: "fl_live_test_key")
    monkeypatch.setattr(
        "aria_core.services.flaunch.httpx.AsyncClient", lambda **kw: _FakeAPIClient(response)
    )


@pytest.mark.asyncio
async def test_fetch_recent_uses_api_when_key_configured(monkeypatch):
    _patch_api(monkeypatch, _FakeAPIResponse(200, {"data": [_real_api_coin_payload()]}))

    tokens = await FlaunchClient().fetch_recent(limit=50)
    assert len(tokens) == 1
    assert tokens[0].symbol == "FUH"
    assert tokens[0].market_cap_usd == pytest.approx(10047.6210365159986395156)


@pytest.mark.asyncio
async def test_fetch_recent_falls_back_to_onchain_when_api_key_missing(monkeypatch):
    _no_api_key(monkeypatch)
    result = ContractLogsResult(logs=[_real_pool_created_log()], available=True)

    class _FakeBlockscout:
        async def get_contract_logs_bounded(self, address, *, max_pages):
            return result

    monkeypatch.setattr(
        "aria_core.services.blockscout.get_blockscout_client", lambda chain: _FakeBlockscout()
    )

    tokens = await FlaunchClient().fetch_recent(limit=50)
    assert len(tokens) == 1
    assert tokens[0].symbol == "FUH"


@pytest.mark.asyncio
async def test_fetch_recent_falls_back_to_onchain_on_api_401(monkeypatch):
    """A configured-but-rejected key (e.g. revoked) degrades to the on-chain
    path -- never an exception, never a blocked cycle."""
    _patch_api(monkeypatch, _FakeAPIResponse(401, {"error": "Unauthorized"}))
    result = ContractLogsResult(logs=[_real_pool_created_log()], available=True)

    class _FakeBlockscout:
        async def get_contract_logs_bounded(self, address, *, max_pages):
            return result

    monkeypatch.setattr(
        "aria_core.services.blockscout.get_blockscout_client", lambda chain: _FakeBlockscout()
    )

    tokens = await FlaunchClient().fetch_recent(limit=50)
    assert len(tokens) == 1
    assert tokens[0].symbol == "FUH"


@pytest.mark.asyncio
async def test_fetch_recent_falls_back_to_onchain_on_api_500(monkeypatch):
    _patch_api(monkeypatch, _FakeAPIResponse(500))
    result = ContractLogsResult(logs=[], available=True)

    class _FakeBlockscout:
        async def get_contract_logs_bounded(self, address, *, max_pages):
            return result

    monkeypatch.setattr(
        "aria_core.services.blockscout.get_blockscout_client", lambda chain: _FakeBlockscout()
    )

    assert await FlaunchClient().fetch_recent(limit=50) == []


@pytest.mark.asyncio
async def test_fetch_recent_falls_back_to_onchain_on_network_error(monkeypatch):
    monkeypatch.setattr("aria_core.services.flaunch.flaunch_api_key", lambda: "fl_live_test_key")

    class _TimeoutClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, params=None, headers=None):
            import httpx

            raise httpx.ConnectError("network blocked")

    monkeypatch.setattr(
        "aria_core.services.flaunch.httpx.AsyncClient", lambda **kw: _TimeoutClient()
    )
    result = ContractLogsResult(logs=[_real_pool_created_log()], available=True)

    class _FakeBlockscout:
        async def get_contract_logs_bounded(self, address, *, max_pages):
            return result

    monkeypatch.setattr(
        "aria_core.services.blockscout.get_blockscout_client", lambda chain: _FakeBlockscout()
    )

    tokens = await FlaunchClient().fetch_recent(limit=50)
    assert len(tokens) == 1
