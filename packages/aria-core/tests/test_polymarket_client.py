"""Tests du client Polymarket (signal macro, #59) — aucun appel reseau reel."""
from __future__ import annotations

import json

import pytest

from aria_core.services.polymarket import PolymarketClient


class FakeResponse:
    def __init__(self, status_code: int, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class FakeClient:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def get(self, url):
        return self._response


def _patch_client(monkeypatch, response):
    monkeypatch.setattr(
        "aria_core.services.polymarket.httpx.AsyncClient", lambda **kw: FakeClient(response),
    )


def _event_payload(markets):
    return [
        {
            "title": "How many Fed rate cuts in 2026?",
            "slug": "how-many-fed-rate-cuts-in-2026",
            "volume": "123456.78",
            "markets": markets,
        }
    ]


@pytest.mark.asyncio
async def test_fetch_success_parses_json_encoded_prices(monkeypatch):
    # outcomePrices est une CHAINE JSON sur cet endpoint (verifie en direct le 10/07),
    # pas une vraie liste -- le test verrouille ce format exact.
    markets = [
        {"question": "Will no Fed rate cuts happen in 2026?", "outcomePrices": json.dumps(["0.7845", "0.2155"])},
        {"question": "Will 1 Fed rate cut happen in 2026?", "outcomePrices": json.dumps(["0.145", "0.855"])},
    ]
    _patch_client(monkeypatch, FakeResponse(200, _event_payload(markets)))

    client = PolymarketClient()
    result = await client.fetch_top_event_by_tag("fed-rates")

    assert result.available is True
    assert result.title == "How many Fed rate cuts in 2026?"
    assert result.volume_usd == 123456.78
    assert result.outcomes == [
        pytest_outcome("Will no Fed rate cuts happen in 2026?", 0.7845),
        pytest_outcome("Will 1 Fed rate cut happen in 2026?", 0.145),
    ]


def pytest_outcome(label, probability):
    from aria_core.services.polymarket import PolymarketOutcome

    return PolymarketOutcome(label=label, probability=probability)


@pytest.mark.asyncio
async def test_fetch_http_error_never_invents_data(monkeypatch):
    _patch_client(monkeypatch, FakeResponse(500))

    client = PolymarketClient()
    result = await client.fetch_top_event_by_tag("fed-rates")

    assert result.available is False
    assert result.outcomes == []
    assert result.error


@pytest.mark.asyncio
async def test_fetch_no_events_for_tag_fails_closed(monkeypatch):
    _patch_client(monkeypatch, FakeResponse(200, []))

    client = PolymarketClient()
    result = await client.fetch_top_event_by_tag("obscure-tag-xyz")

    assert result.available is False
    assert result.outcomes == []


@pytest.mark.asyncio
async def test_fetch_malformed_market_prices_are_skipped_not_crashed(monkeypatch):
    markets = [
        {"question": "Malformed", "outcomePrices": "not json"},
        {"question": "Valid", "outcomePrices": json.dumps(["0.5", "0.5"])},
    ]
    _patch_client(monkeypatch, FakeResponse(200, _event_payload(markets)))

    client = PolymarketClient()
    result = await client.fetch_top_event_by_tag("fed-rates")

    assert result.available is True
    assert len(result.outcomes) == 1
    assert result.outcomes[0].label == "Valid"


@pytest.mark.asyncio
async def test_fetch_no_exploitable_prices_fails_closed(monkeypatch):
    markets = [{"question": "Malformed", "outcomePrices": "not json"}]
    _patch_client(monkeypatch, FakeResponse(200, _event_payload(markets)))

    client = PolymarketClient()
    result = await client.fetch_top_event_by_tag("fed-rates")

    assert result.available is False
