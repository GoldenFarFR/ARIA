"""Client Birdeye -- découverte en masse de tokens Base (21/07). Vérifie :
absence de clé (dôme), pagination, dégradation sur panne HTTP/JSON, plafond
anti-boucle-infinie, throttle."""
from __future__ import annotations

import pytest

from aria_core.services import birdeye


@pytest.fixture(autouse=True)
def _reset_module_state(monkeypatch):
    monkeypatch.setattr(birdeye, "_last_call_at", 0.0)
    yield


def test_birdeye_available_false_without_key(monkeypatch):
    monkeypatch.delenv("BIRDEYE_API_KEY", raising=False)
    assert birdeye.birdeye_available() is False
    assert birdeye.birdeye_api_key() is None


def test_birdeye_available_true_with_key(monkeypatch):
    monkeypatch.setenv("BIRDEYE_API_KEY", "test-key")
    assert birdeye.birdeye_available() is True


@pytest.mark.asyncio
async def test_discover_returns_empty_without_key(monkeypatch):
    monkeypatch.delenv("BIRDEYE_API_KEY", raising=False)
    result = await birdeye.discover_base_tokens_bulk()
    assert result == []


class _FakeResponse:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        return self._payload


def _page(addresses):
    return _FakeResponse(200, {"data": {"tokens": [{"address": a} for a in addresses]}})


@pytest.mark.asyncio
async def test_discover_single_page_under_limit_stops_pagination(monkeypatch):
    monkeypatch.setenv("BIRDEYE_API_KEY", "test-key")
    monkeypatch.setattr(birdeye, "_MIN_INTERVAL_S", 0.0)

    calls = []

    async def fake_get(self, url, params=None, headers=None):
        calls.append(params["offset"])
        return _page([f"0x{i:040d}" for i in range(20)])  # < _PAGE_LIMIT (100)

    monkeypatch.setattr("httpx.AsyncClient.get", fake_get)
    result = await birdeye.discover_base_tokens_bulk()
    assert len(result) == 20
    assert calls == [0]  # une seule page -- 20 < 100 -> arrêt


@pytest.mark.asyncio
async def test_discover_paginates_across_full_pages(monkeypatch):
    monkeypatch.setenv("BIRDEYE_API_KEY", "test-key")
    monkeypatch.setattr(birdeye, "_MIN_INTERVAL_S", 0.0)

    pages = [
        [f"0xA{i:039d}" for i in range(100)],
        [f"0xB{i:039d}" for i in range(100)],
        [f"0xC{i:039d}" for i in range(20)],
    ]
    call_count = {"n": 0}

    async def fake_get(self, url, params=None, headers=None):
        idx = call_count["n"]
        call_count["n"] += 1
        return _page(pages[idx])

    monkeypatch.setattr("httpx.AsyncClient.get", fake_get)
    result = await birdeye.discover_base_tokens_bulk()
    assert len(result) == 220
    assert call_count["n"] == 3


@pytest.mark.asyncio
async def test_discover_degrades_on_non_200(monkeypatch):
    monkeypatch.setenv("BIRDEYE_API_KEY", "test-key")
    monkeypatch.setattr(birdeye, "_MIN_INTERVAL_S", 0.0)

    async def fake_get(self, url, params=None, headers=None):
        return _FakeResponse(429, text="rate limited")

    monkeypatch.setattr("httpx.AsyncClient.get", fake_get)
    result = await birdeye.discover_base_tokens_bulk()
    assert result == []


@pytest.mark.asyncio
async def test_discover_degrades_on_network_exception(monkeypatch):
    monkeypatch.setenv("BIRDEYE_API_KEY", "test-key")
    monkeypatch.setattr(birdeye, "_MIN_INTERVAL_S", 0.0)

    async def fake_get(self, url, params=None, headers=None):
        raise RuntimeError("panne réseau")

    monkeypatch.setattr("httpx.AsyncClient.get", fake_get)
    result = await birdeye.discover_base_tokens_bulk()
    assert result == []


@pytest.mark.asyncio
async def test_discover_degrades_on_malformed_json(monkeypatch):
    monkeypatch.setenv("BIRDEYE_API_KEY", "test-key")
    monkeypatch.setattr(birdeye, "_MIN_INTERVAL_S", 0.0)

    async def fake_get(self, url, params=None, headers=None):
        return _FakeResponse(200, payload=["not", "a", "dict"])

    monkeypatch.setattr("httpx.AsyncClient.get", fake_get)
    result = await birdeye.discover_base_tokens_bulk()
    assert result == []


@pytest.mark.asyncio
async def test_discover_respects_max_pages_cap(monkeypatch):
    """Anti-boucle-infinie -- même si l'API renvoie toujours des pages pleines,
    ne dépasse jamais _MAX_PAGES appels."""
    monkeypatch.setenv("BIRDEYE_API_KEY", "test-key")
    monkeypatch.setattr(birdeye, "_MIN_INTERVAL_S", 0.0)
    monkeypatch.setattr(birdeye, "_MAX_PAGES", 3)

    call_count = {"n": 0}

    async def fake_get(self, url, params=None, headers=None):
        call_count["n"] += 1
        return _page([f"0x{i:040d}" for i in range(100)])  # toujours pleine

    monkeypatch.setattr("httpx.AsyncClient.get", fake_get)
    result = await birdeye.discover_base_tokens_bulk()
    assert call_count["n"] == 3
    assert len(result) == 300


@pytest.mark.asyncio
async def test_discover_passes_through_liquidity_and_volume_thresholds(monkeypatch):
    monkeypatch.setenv("BIRDEYE_API_KEY", "test-key")
    monkeypatch.setattr(birdeye, "_MIN_INTERVAL_S", 0.0)

    captured = {}

    async def fake_get(self, url, params=None, headers=None):
        captured.update(params)
        return _FakeResponse(200, {"data": {"tokens": []}})

    monkeypatch.setattr("httpx.AsyncClient.get", fake_get)
    await birdeye.discover_base_tokens_bulk(min_liquidity_usd=75_000.0, min_volume_24h_usd=1_234.0)
    assert captured["min_liquidity"] == 75_000.0
    assert captured["min_volume_24h_usd"] == 1_234.0


@pytest.mark.asyncio
async def test_discover_sends_base_chain_header(monkeypatch):
    monkeypatch.setenv("BIRDEYE_API_KEY", "test-key")
    monkeypatch.setattr(birdeye, "_MIN_INTERVAL_S", 0.0)

    captured_headers = {}

    async def fake_get(self, url, params=None, headers=None):
        captured_headers.update(headers or {})
        return _FakeResponse(200, {"data": {"tokens": []}})

    monkeypatch.setattr("httpx.AsyncClient.get", fake_get)
    await birdeye.discover_base_tokens_bulk()
    assert captured_headers.get("x-chain") == "base"
    assert captured_headers.get("X-API-KEY") == "test-key"
