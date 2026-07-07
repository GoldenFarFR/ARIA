"""Tests du client CoinGecko (lecture seule) — aucun appel réseau réel, tout est mocké."""

import pytest

from aria_core.services.coingecko import CoinGeckoClient, UNAVAILABLE


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

    async def get(self, url, params=None):
        queue = self._responses[url]
        if isinstance(queue, list):
            return queue.pop(0)
        return queue


def _patch_client(monkeypatch, responses: dict):
    monkeypatch.setattr(
        "aria_core.services.coingecko.httpx.AsyncClient",
        lambda **kw: FakeClient(responses),
    )


def _patch_no_sleep(monkeypatch):
    async def _fake_sleep(_seconds):
        return None

    monkeypatch.setattr("aria_core.services.coingecko.asyncio.sleep", _fake_sleep)


@pytest.mark.asyncio
async def test_get_token_fundamentals_success(monkeypatch):
    client = CoinGeckoClient()
    url = f"{client.base_url}/coins/base/contract/0xabc"
    _patch_client(
        monkeypatch,
        {
            url: FakeResponse(
                200,
                {
                    "id": "some-token",
                    "name": "Some Token",
                    "symbol": "SOME",
                    "categories": ["Meme", "Base Ecosystem", "", None],
                    "genesis_date": "2026-01-01",
                    "links": {"homepage": ["https://example.com", ""], "whitepaper": "https://example.com/wp.pdf"},
                    "market_data": {
                        "market_cap": {"usd": 1_000_000},
                        "fully_diluted_valuation": {"usd": 4_000_000},
                        "circulating_supply": 250_000,
                        "total_supply": 1_000_000,
                        "max_supply": 1_000_000,
                    },
                },
            )
        },
    )

    fundamentals = await client.get_token_fundamentals("0xabc")

    assert fundamentals.available is True
    assert fundamentals.error is None
    assert fundamentals.coingecko_id == "some-token"
    assert fundamentals.name == "Some Token"
    assert fundamentals.market_cap_usd == pytest.approx(1_000_000)
    assert fundamentals.fully_diluted_valuation_usd == pytest.approx(4_000_000)
    assert fundamentals.circulating_supply == pytest.approx(250_000)
    assert fundamentals.categories == ["Meme", "Base Ecosystem"]
    assert fundamentals.homepage == "https://example.com"
    assert fundamentals.whitepaper == "https://example.com/wp.pdf"
    assert fundamentals.genesis_date == "2026-01-01"


@pytest.mark.asyncio
async def test_get_token_fundamentals_not_listed(monkeypatch):
    client = CoinGeckoClient()
    url = f"{client.base_url}/coins/base/contract/0xdead"
    _patch_client(monkeypatch, {url: FakeResponse(404, None)})

    fundamentals = await client.get_token_fundamentals("0xdead")

    assert fundamentals.available is False
    assert "non listé" in fundamentals.error


@pytest.mark.asyncio
async def test_missing_market_data_fields_no_guessing(monkeypatch):
    client = CoinGeckoClient()
    url = f"{client.base_url}/coins/base/contract/0xabc"
    _patch_client(monkeypatch, {url: FakeResponse(200, {"id": "x", "market_data": {}})})

    fundamentals = await client.get_token_fundamentals("0xabc")

    assert fundamentals.available is True
    assert fundamentals.market_cap_usd is None
    assert fundamentals.fully_diluted_valuation_usd is None
    assert fundamentals.circulating_supply is None


@pytest.mark.asyncio
async def test_rate_limit_gives_up_after_three_attempts(monkeypatch):
    _patch_no_sleep(monkeypatch)
    client = CoinGeckoClient()
    url = f"{client.base_url}/coins/base/contract/0xabc"
    _patch_client(monkeypatch, {url: [FakeResponse(429), FakeResponse(429), FakeResponse(429)]})

    fundamentals = await client.get_token_fundamentals("0xabc")

    assert fundamentals.available is False
    assert UNAVAILABLE in fundamentals.error
    assert "rate limit" in fundamentals.error


@pytest.mark.asyncio
async def test_timeout_retries_once_then_fallback(monkeypatch):
    _patch_no_sleep(monkeypatch)
    client = CoinGeckoClient()

    import httpx

    calls = {"count": 0}

    class TimeoutClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, params=None):
            calls["count"] += 1
            raise httpx.TimeoutException("boom")

    monkeypatch.setattr(
        "aria_core.services.coingecko.httpx.AsyncClient",
        lambda **kw: TimeoutClient(),
    )

    fundamentals = await client.get_token_fundamentals("0xabc")

    assert fundamentals.available is False
    assert UNAVAILABLE in fundamentals.error
    assert calls["count"] == 2
