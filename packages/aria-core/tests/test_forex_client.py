"""Tests du client Frankfurter (taux de change, lecture seule) — aucun appel réseau
réel, tout mocké. Fetch direct bloqué en 403 dans le sandbox cloud (cf. avertissement
en tête de services/forex.py) : forme confirmée par recherche croisée, pas en direct."""

import pytest

from aria_core.services.forex import UNAVAILABLE, ExchangeRateResult, ForexClient


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

    async def get(self, url, headers=None, params=None):
        queue = self._responses[url]
        if isinstance(queue, list):
            return queue.pop(0)
        return queue


def _patch_client(monkeypatch, responses: dict):
    monkeypatch.setattr(
        "aria_core.services.forex.httpx.AsyncClient",
        lambda **kw: FakeClient(responses),
    )


def _patch_no_sleep(monkeypatch):
    async def _fake_sleep(_seconds):
        return None

    monkeypatch.setattr("aria_core.services.forex.asyncio.sleep", _fake_sleep)


@pytest.mark.asyncio
async def test_get_latest_rates_success(monkeypatch):
    client = ForexClient()
    url = "https://api.frankfurter.dev/v1/latest?base=USD&symbols=EUR"
    _patch_client(
        monkeypatch,
        {url: FakeResponse(200, {"amount": 1, "base": "USD", "date": "2026-07-10", "rates": {"EUR": 0.92}})},
    )

    result = await client.get_latest_rates("usd", ["eur"])

    assert isinstance(result, ExchangeRateResult)
    assert result.available is True
    assert result.base == "USD"
    assert result.rates["EUR"] == pytest.approx(0.92)
    assert result.date == "2026-07-10"


@pytest.mark.asyncio
async def test_get_latest_rates_multiple_symbols(monkeypatch):
    client = ForexClient()
    url = "https://api.frankfurter.dev/v1/latest?base=USD&symbols=EUR,GBP"
    _patch_client(
        monkeypatch,
        {url: FakeResponse(200, {"amount": 1, "base": "USD", "date": "2026-07-10", "rates": {"EUR": 0.92, "GBP": 0.79}})},
    )

    result = await client.get_latest_rates("USD", ["EUR", "GBP"])
    assert result.rates == {"EUR": pytest.approx(0.92), "GBP": pytest.approx(0.79)}


@pytest.mark.asyncio
async def test_get_latest_rates_empty_inputs_no_call():
    client = ForexClient()
    assert (await client.get_latest_rates("", ["EUR"])).available is False
    assert (await client.get_latest_rates("USD", [])).available is False


@pytest.mark.asyncio
async def test_get_latest_rates_unknown_currency_returns_unavailable(monkeypatch):
    client = ForexClient()
    url = "https://api.frankfurter.dev/v1/latest?base=ZZZ&symbols=EUR"
    _patch_client(monkeypatch, {url: FakeResponse(404)})

    result = await client.get_latest_rates("ZZZ", ["EUR"])
    assert result.available is False
    assert result.error == f"{UNAVAILABLE} (devise inconnue)"


@pytest.mark.asyncio
async def test_get_latest_rates_network_error_returns_unavailable_not_raise(monkeypatch):
    _patch_no_sleep(monkeypatch)
    client = ForexClient()

    import httpx

    class TimeoutClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, headers=None, params=None):
            raise httpx.ConnectError("network blocked")

    monkeypatch.setattr(
        "aria_core.services.forex.httpx.AsyncClient",
        lambda **kw: TimeoutClient(),
    )

    result = await client.get_latest_rates("USD", ["EUR"])
    assert result.available is False


@pytest.mark.asyncio
async def test_get_latest_rates_rate_limited_never_invents_a_rate(monkeypatch):
    _patch_no_sleep(monkeypatch)
    client = ForexClient()
    url = "https://api.frankfurter.dev/v1/latest?base=USD&symbols=EUR"
    _patch_client(monkeypatch, {url: [FakeResponse(429), FakeResponse(429), FakeResponse(429)]})

    result = await client.get_latest_rates("USD", ["EUR"])
    assert result.available is False
    assert result.rates == {}


def test_unavailable_message_exposed():
    assert isinstance(UNAVAILABLE, str) and UNAVAILABLE
