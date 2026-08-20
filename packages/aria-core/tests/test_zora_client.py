"""Tests for the Zora Coins discovery client (services/zora.py) -- no real
network call, everything mocked.

The fixture below is a REAL response captured live 20/08 from
``GET https://api-sdk.zora.engineering/explore?listType=NEW&count=3``, no API
key sent -- confirmed the endpoint works unauthenticated. See the module
docstring in services/zora.py for the full diligence trail."""

import pytest

from aria_core.services.zora import ZoraClient, ZoraCoin, _parse_node


def _real_explore_node(**overrides) -> dict:
    """Real ``exploreList.edges[].node`` entry, captured live 20/08 (token
    "activity"/ACTIVITY, created minutes before the capture)."""
    node = {
        "address": "0xb507565c144c23454ad8fd578bdd6c31bf08f8d6",
        "name": "activity",
        "symbol": "activity",
        "coinType": "CONTENT",
        "totalSupply": "1000000000",
        "totalVolume": "0.0",
        "volume24h": "0.0",
        "createdAt": "2026-08-20T18:01:25+00:00",
        "creatorAddress": "0x58c5bcba9f880b4b53a35dcd42626b0e566dd73c",
        "marketCap": "0",
        "marketCapDelta24h": "0.0",
        "chainId": 8453,
    }
    node.update(overrides)
    return node


# ----------------------------------------------------------------------
# _parse_node
# ----------------------------------------------------------------------
def test_parse_node_real_shape():
    coin = _parse_node(_real_explore_node())

    assert isinstance(coin, ZoraCoin)
    assert coin.contract == "0xb507565c144c23454ad8fd578bdd6c31bf08f8d6"
    assert coin.name == "activity"
    assert coin.symbol == "activity"
    assert coin.coin_type == "CONTENT"
    assert coin.market_cap_usd == pytest.approx(0.0)
    assert coin.volume24h_usd == pytest.approx(0.0)
    assert coin.chain_id == 8453
    assert coin.created_at == "2026-08-20T18:01:25+00:00"


def test_parse_node_no_address_returns_none():
    assert _parse_node({"name": "no address"}) is None


def test_parse_node_not_a_dict_returns_none():
    assert _parse_node(["not", "a", "dict"]) is None
    assert _parse_node(None) is None


def test_parse_node_bad_numeric_fields_degrade_to_none():
    node = _real_explore_node(marketCap="not-a-number", volume24h=None)
    coin = _parse_node(node)
    assert coin.market_cap_usd is None
    assert coin.volume24h_usd is None


# ----------------------------------------------------------------------
# ZoraClient.fetch_recent
# ----------------------------------------------------------------------
class _FakeResponse:
    def __init__(self, status_code: int, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx

            raise httpx.HTTPStatusError("error", request=None, response=self)


class _FakeClient:
    def __init__(self, response, *, expected_headers=None):
        self._response = response
        self._expected_headers = expected_headers

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def get(self, url, params=None, headers=None):
        if self._expected_headers is not None:
            assert headers == self._expected_headers
        return self._response


@pytest.mark.asyncio
async def test_fetch_recent_real_shape_no_api_key(monkeypatch):
    """Confirmed live 20/08: no key sent at all still works."""
    monkeypatch.setattr("aria_core.services.zora.zora_api_key", lambda: "")
    payload = {"exploreList": {"edges": [{"node": _real_explore_node()}]}}
    monkeypatch.setattr(
        "aria_core.services.zora.httpx.AsyncClient",
        lambda **kw: _FakeClient(_FakeResponse(200, payload), expected_headers={"Accept": "application/json"}),
    )

    tokens = await ZoraClient().fetch_recent(limit=50)
    assert len(tokens) == 1
    assert tokens[0].symbol == "activity"
    assert tokens[0].contract == "0xb507565c144c23454ad8fd578bdd6c31bf08f8d6"


@pytest.mark.asyncio
async def test_fetch_recent_sends_api_key_header_when_configured(monkeypatch):
    monkeypatch.setattr("aria_core.services.zora.zora_api_key", lambda: "zora_live_test_key")
    payload = {"exploreList": {"edges": [{"node": _real_explore_node()}]}}
    monkeypatch.setattr(
        "aria_core.services.zora.httpx.AsyncClient",
        lambda **kw: _FakeClient(
            _FakeResponse(200, payload),
            expected_headers={"Accept": "application/json", "api-key": "zora_live_test_key"},
        ),
    )

    tokens = await ZoraClient().fetch_recent(limit=50)
    assert len(tokens) == 1


@pytest.mark.asyncio
async def test_fetch_recent_dedup_not_needed_but_respects_limit(monkeypatch):
    monkeypatch.setattr("aria_core.services.zora.zora_api_key", lambda: "")
    nodes = [_real_explore_node(address=f"0xTOKEN{i}") for i in range(5)]
    payload = {"exploreList": {"edges": [{"node": n} for n in nodes]}}
    monkeypatch.setattr(
        "aria_core.services.zora.httpx.AsyncClient",
        lambda **kw: _FakeClient(_FakeResponse(200, payload)),
    )

    tokens = await ZoraClient().fetch_recent(limit=2)
    assert len(tokens) == 2


@pytest.mark.asyncio
async def test_fetch_recent_malformed_payload_returns_empty(monkeypatch):
    monkeypatch.setattr("aria_core.services.zora.zora_api_key", lambda: "")
    monkeypatch.setattr(
        "aria_core.services.zora.httpx.AsyncClient",
        lambda **kw: _FakeClient(_FakeResponse(200, {"unexpected": "shape"})),
    )

    assert await ZoraClient().fetch_recent() == []


@pytest.mark.asyncio
async def test_fetch_recent_429_degrades_gracefully(monkeypatch):
    monkeypatch.setattr("aria_core.services.zora.zora_api_key", lambda: "")
    monkeypatch.setattr(
        "aria_core.services.zora.httpx.AsyncClient",
        lambda **kw: _FakeClient(_FakeResponse(429)),
    )

    assert await ZoraClient().fetch_recent() == []


@pytest.mark.asyncio
async def test_fetch_recent_500_degrades_gracefully(monkeypatch):
    monkeypatch.setattr("aria_core.services.zora.zora_api_key", lambda: "")
    monkeypatch.setattr(
        "aria_core.services.zora.httpx.AsyncClient",
        lambda **kw: _FakeClient(_FakeResponse(500)),
    )

    assert await ZoraClient().fetch_recent() == []


@pytest.mark.asyncio
async def test_fetch_recent_network_error_degrades_gracefully(monkeypatch):
    monkeypatch.setattr("aria_core.services.zora.zora_api_key", lambda: "")

    class _Boom:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, params=None, headers=None):
            import httpx

            raise httpx.ConnectError("network blocked")

    monkeypatch.setattr("aria_core.services.zora.httpx.AsyncClient", lambda **kw: _Boom())

    assert await ZoraClient().fetch_recent() == []


@pytest.mark.asyncio
async def test_fetch_recent_consecutive_failures_warn_but_never_raise(monkeypatch):
    monkeypatch.setattr("aria_core.services.zora.zora_api_key", lambda: "")
    monkeypatch.setattr(
        "aria_core.services.zora.httpx.AsyncClient",
        lambda **kw: _FakeClient(_FakeResponse(500)),
    )

    client = ZoraClient()
    for _ in range(5):
        assert await client.fetch_recent() == []
    assert client._consecutive_failures == 5
