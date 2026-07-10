"""Tests du client Blockchain.com (historique BTC long) — aucun appel reseau reel."""
from __future__ import annotations

import pytest

from aria_core.services.blockchain_info import BlockchainInfoClient


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
        "aria_core.services.blockchain_info.httpx.AsyncClient",
        lambda **kw: FakeClient(response),
    )


@pytest.mark.asyncio
async def test_fetch_success_converts_seconds_to_ms(monkeypatch):
    payload = {"values": [{"x": 1230940800, "y": 0.0}, {"x": 1783555200, "y": 62249.52}]}
    _patch_client(monkeypatch, FakeResponse(200, payload))

    client = BlockchainInfoClient()
    result = await client.fetch_btc_market_price_history()

    assert result.available is True
    assert result.prices == [(1230940800000, 0.0), (1783555200000, 62249.52)]
    assert result.error is None


@pytest.mark.asyncio
async def test_fetch_http_error_never_invents_data(monkeypatch):
    _patch_client(monkeypatch, FakeResponse(500))

    client = BlockchainInfoClient()
    result = await client.fetch_btc_market_price_history()

    assert result.available is False
    assert result.prices == []
    assert result.error


@pytest.mark.asyncio
async def test_fetch_empty_values_fails_closed(monkeypatch):
    _patch_client(monkeypatch, FakeResponse(200, {"values": []}))

    client = BlockchainInfoClient()
    result = await client.fetch_btc_market_price_history()

    assert result.available is False
    assert result.prices == []


@pytest.mark.asyncio
async def test_fetch_malformed_points_are_skipped_not_crashed(monkeypatch):
    payload = {"values": [{"x": "oops"}, {"x": 1783555200, "y": 62249.52}]}
    _patch_client(monkeypatch, FakeResponse(200, payload))

    client = BlockchainInfoClient()
    result = await client.fetch_btc_market_price_history()

    assert result.available is True
    assert result.prices == [(1783555200000, 62249.52)]
