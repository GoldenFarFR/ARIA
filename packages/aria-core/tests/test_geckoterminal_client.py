"""Tests du client GeckoTerminal (lecture seule, #157) — aucun appel réseau
réel, tout est mocké."""

import pytest

from aria_core.services.geckoterminal import GeckoTerminalClient


class FakeResponse:
    def __init__(self, status_code: int, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx

            raise httpx.HTTPStatusError("error", request=None, response=self)


class FakeClient:
    def __init__(self, responses: dict):
        self._responses = responses

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def get(self, url, params=None, headers=None):
        return self._responses[url]


def _patch_client(monkeypatch, responses: dict):
    monkeypatch.setattr(
        "aria_core.services.geckoterminal.httpx.AsyncClient",
        lambda **kw: FakeClient(responses),
    )


@pytest.mark.asyncio
async def test_get_pool_created_at_parses_timestamp(monkeypatch):
    client = GeckoTerminalClient()
    url = f"{client.base_url}/networks/base/pools/0xpool"
    _patch_client(
        monkeypatch,
        {url: FakeResponse(200, {"data": {"attributes": {"pool_created_at": "2026-07-02T13:07:59Z"}}})},
    )

    result = await client.get_pool_created_at("0xpool")

    assert result.available is True
    assert result.created_at.year == 2026
    assert result.created_at.month == 7


@pytest.mark.asyncio
async def test_get_pool_created_at_missing_date_unavailable(monkeypatch):
    client = GeckoTerminalClient()
    url = f"{client.base_url}/networks/base/pools/0xpool"
    _patch_client(monkeypatch, {url: FakeResponse(200, {"data": {"attributes": {}}})})

    result = await client.get_pool_created_at("0xpool")

    assert result.available is False
    assert "indisponible" in result.error


@pytest.mark.asyncio
async def test_get_ohlcv_parses_and_sorts_candles(monkeypatch):
    client = GeckoTerminalClient()
    url = f"{client.base_url}/networks/base/pools/0xpool/ohlcv/hour"
    _patch_client(
        monkeypatch,
        {
            url: FakeResponse(
                200,
                {
                    "data": {
                        "attributes": {
                            "ohlcv_list": [
                                [200, 2.0, 2.5, 1.9, 2.2, 500.0],
                                [100, 1.0, 1.5, 0.9, 1.2, 1000.0],
                            ]
                        }
                    }
                },
            )
        },
    )

    result = await client.get_ohlcv("0xpool")

    assert result.available is True
    assert [c.ts for c in result.candles] == [100, 200]  # trié


@pytest.mark.asyncio
async def test_get_ohlcv_404_unavailable_not_empty_list_silently(monkeypatch):
    client = GeckoTerminalClient()
    url = f"{client.base_url}/networks/base/pools/0xpool/ohlcv/hour"
    _patch_client(monkeypatch, {url: FakeResponse(404)})

    result = await client.get_ohlcv("0xpool")

    assert result.available is False
    assert result.candles == []


class TestResolvePrimaryPool:
    """#157, correction 14/07 : `get_pool_created_at`/`get_ohlcv` attendent une
    adresse de POOL, pas un contrat de TOKEN -- `resolve_primary_pool` corrige un
    bug latent où le code appelant passait directement l'adresse du token."""

    @pytest.mark.asyncio
    async def test_picks_pool_with_highest_liquidity(self, monkeypatch):
        client = GeckoTerminalClient()
        url = f"{client.base_url}/networks/base/tokens/0xtoken/pools"
        _patch_client(
            monkeypatch,
            {
                url: FakeResponse(
                    200,
                    {
                        "data": [
                            {
                                "attributes": {
                                    "address": "0xpool_low",
                                    "reserve_in_usd": "10.0",
                                    "pool_created_at": "2026-01-01T00:00:00Z",
                                }
                            },
                            {
                                "attributes": {
                                    "address": "0xpool_high",
                                    "reserve_in_usd": "50000.0",
                                    "pool_created_at": "2026-02-01T00:00:00Z",
                                }
                            },
                        ]
                    },
                )
            },
        )

        result = await client.resolve_primary_pool("0xtoken")

        assert result.available is True
        assert result.pool_address == "0xpool_high"
        assert result.created_at.month == 2

    @pytest.mark.asyncio
    async def test_no_pools_found_unavailable_never_guesses_token_as_pool(self, monkeypatch):
        client = GeckoTerminalClient()
        url = f"{client.base_url}/networks/base/tokens/0xtoken/pools"
        _patch_client(monkeypatch, {url: FakeResponse(200, {"data": []})})

        result = await client.resolve_primary_pool("0xtoken")

        assert result.available is False
        assert result.pool_address == "0xtoken"  # jamais présenté comme un vrai pool résolu (available=False)

    @pytest.mark.asyncio
    async def test_malformed_reserve_treated_as_zero_not_a_crash(self, monkeypatch):
        client = GeckoTerminalClient()
        url = f"{client.base_url}/networks/base/tokens/0xtoken/pools"
        _patch_client(
            monkeypatch,
            {
                url: FakeResponse(
                    200,
                    {
                        "data": [
                            {"attributes": {"address": "0xpool_a", "reserve_in_usd": "not-a-number"}},
                            {"attributes": {"address": "0xpool_b", "reserve_in_usd": "1.0"}},
                        ]
                    },
                )
            },
        )

        result = await client.resolve_primary_pool("0xtoken")

        assert result.available is True
        assert result.pool_address == "0xpool_b"

    @pytest.mark.asyncio
    async def test_error_response_propagates_unavailable(self, monkeypatch):
        client = GeckoTerminalClient()
        url = f"{client.base_url}/networks/base/tokens/0xtoken/pools"
        _patch_client(monkeypatch, {url: FakeResponse(429)})

        result = await client.resolve_primary_pool("0xtoken")

        assert result.available is False
