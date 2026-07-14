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
        queue = self._responses[url]
        if isinstance(queue, list):
            return queue.pop(0)
        return queue


def _patch_client(monkeypatch, responses: dict):
    monkeypatch.setattr(
        "aria_core.services.geckoterminal.httpx.AsyncClient",
        lambda **kw: FakeClient(responses),
    )


def _patch_no_sleep(monkeypatch):
    async def _fake_sleep(_seconds):
        return None

    monkeypatch.setattr("aria_core.services.geckoterminal.asyncio.sleep", _fake_sleep)


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
async def test_get_ohlcv_delegates_to_wide_ohlcv_client(monkeypatch):
    # #157, correction 14/07 : get_ohlcv ne fait plus sa propre requête HTTP
    # (fenêtre fixe ~8 jours, trop étroite -- root cause confirmée des jambes
    # "sans prix" persistant après le fix retry/429) -- délègue désormais à
    # services.ohlcv.ohlcv_client (échelle jour->4h->1h, déjà éprouvée en prod).
    from aria_core.services import ohlcv as ohlcv_module
    from aria_core.skills.ta_levels import Candle

    async def _fake_wide_get_ohlcv(pool_address, *, network="base"):
        assert pool_address == "0xpool"
        return ohlcv_module.OHLCVResult(
            pool_address=pool_address,
            network=network,
            candles=[
                Candle(ts=100, open=1.0, high=1.5, low=0.9, close=1.2, volume=1000.0),
                Candle(ts=200, open=2.0, high=2.5, low=1.9, close=2.2, volume=500.0),
            ],
            timeframe="1D",
            available=True,
        )

    monkeypatch.setattr(ohlcv_module.ohlcv_client, "get_ohlcv", _fake_wide_get_ohlcv)

    client = GeckoTerminalClient()
    result = await client.get_ohlcv("0xpool")

    assert result.available is True
    assert [c.ts for c in result.candles] == [100, 200]  # trié


@pytest.mark.asyncio
async def test_get_ohlcv_unavailable_when_wide_client_has_nothing(monkeypatch):
    from aria_core.services import ohlcv as ohlcv_module

    async def _fake_wide_get_ohlcv(pool_address, *, network="base"):
        return ohlcv_module.OHLCVResult(pool_address=pool_address, network=network, error="pool introuvable sur GeckoTerminal")

    monkeypatch.setattr(ohlcv_module.ohlcv_client, "get_ohlcv", _fake_wide_get_ohlcv)

    client = GeckoTerminalClient()
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
        _patch_no_sleep(monkeypatch)
        _patch_client(monkeypatch, {url: FakeResponse(429)})

        result = await client.resolve_primary_pool("0xtoken")

        assert result.available is False

    @pytest.mark.asyncio
    async def test_429_retries_then_succeeds(self, monkeypatch):
        """#157, correction 14/07 -- un 429 isolé ne doit plus abandonner net."""
        client = GeckoTerminalClient()
        url = f"{client.base_url}/networks/base/tokens/0xtoken/pools"
        _patch_no_sleep(monkeypatch)
        _patch_client(
            monkeypatch,
            {
                url: [
                    FakeResponse(429),
                    FakeResponse(
                        200,
                        {"data": [{"attributes": {"address": "0xpool_a", "reserve_in_usd": "10"}}]},
                    ),
                ]
            },
        )

        result = await client.resolve_primary_pool("0xtoken")

        assert result.available is True
        assert result.pool_address == "0xpool_a"

    @pytest.mark.asyncio
    async def test_429_gives_up_after_max_retries(self, monkeypatch):
        client = GeckoTerminalClient()
        url = f"{client.base_url}/networks/base/tokens/0xtoken/pools"
        _patch_no_sleep(monkeypatch)
        _patch_client(monkeypatch, {url: [FakeResponse(429), FakeResponse(429), FakeResponse(429)]})

        result = await client.resolve_primary_pool("0xtoken")

        assert result.available is False
        assert "rate limit" in result.error
