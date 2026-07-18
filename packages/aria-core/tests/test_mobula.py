"""Tests du client Mobula (#212, 18/07) -- 3e étage de la cascade OHLCV
momentum. Aucun appel réseau réel, tout est mocké (même patron que
test_dexscreener_client.py/test_rugcheck.py)."""
from __future__ import annotations

import pytest

from aria_core.services.mobula import get_ohlcv, mobula_configured

CONTRACT = "0x4200000000000000000000000000000000000006"


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
        return False

    async def get(self, url, params=None, headers=None):
        queue = self._responses[url]
        if isinstance(queue, list):
            return queue.pop(0)
        return queue


def _patch_client(monkeypatch, responses: dict):
    monkeypatch.setattr(
        "aria_core.services.mobula.httpx.AsyncClient",
        lambda **kw: FakeClient(responses),
    )


def _patch_no_sleep(monkeypatch):
    async def _fake_sleep(_seconds):
        return None

    monkeypatch.setattr("aria_core.services.mobula.asyncio.sleep", _fake_sleep)


def _url() -> str:
    return "https://api.mobula.io/api/2/token/ohlcv-history"


# ── mobula_configured ─────────────────────────────────────────────────────────

def test_mobula_configured_false_when_no_key(monkeypatch):
    monkeypatch.delenv("MOBULA_API_KEY", raising=False)
    assert mobula_configured() is False


def test_mobula_configured_true_when_key_present(monkeypatch):
    monkeypatch.setenv("MOBULA_API_KEY", "test-key")
    assert mobula_configured() is True


# ── get_ohlcv -- pas de clé : aucun appel réseau (18/07, vérifié en direct : ──
# Mobula renvoie 429 "create an API key" même sur le tier Free sans elle) ────

@pytest.mark.asyncio
async def test_get_ohlcv_unavailable_without_key_no_network_call(monkeypatch):
    monkeypatch.delenv("MOBULA_API_KEY", raising=False)
    called = {"network": False}

    def _fail_if_called(**kw):
        called["network"] = True
        raise AssertionError("ne doit jamais appeler le réseau sans clé")

    monkeypatch.setattr("aria_core.services.mobula.httpx.AsyncClient", _fail_if_called)

    result = await get_ohlcv(CONTRACT, blockchain="base")
    assert result.available is False
    assert called["network"] is False


# ── get_ohlcv -- cas clean (schéma vérifié en direct, 18/07 : WETH Base) ──────

@pytest.mark.asyncio
async def test_get_ohlcv_parses_real_shape(monkeypatch):
    monkeypatch.setenv("MOBULA_API_KEY", "test-key")
    _patch_no_sleep(monkeypatch)
    payload = {
        "data": [
            {"v": 71546377.76, "o": 1773.66, "h": 1892.34, "l": 1772.22, "c": 1888.52, "t": 1783987200000},
            {"v": 69138197.06, "o": 1888.52, "h": 1941.30, "l": 1864.72, "c": 1916.29, "t": 1784073600000},
        ]
    }
    _patch_client(monkeypatch, {_url(): FakeResponse(200, payload)})

    result = await get_ohlcv(CONTRACT, blockchain="base")

    assert result.available is True
    assert len(result.candles) == 2
    assert result.candles[0].ts == 1783987200  # ms -> s
    assert result.candles[0].close == 1888.52
    assert result.candles[0].volume == 71546377.76


@pytest.mark.asyncio
async def test_get_ohlcv_sorted_by_timestamp(monkeypatch):
    monkeypatch.setenv("MOBULA_API_KEY", "test-key")
    _patch_no_sleep(monkeypatch)
    payload = {
        "data": [
            {"v": 1.0, "o": 1.0, "h": 1.0, "l": 1.0, "c": 1.0, "t": 2000000000000},
            {"v": 1.0, "o": 1.0, "h": 1.0, "l": 1.0, "c": 1.0, "t": 1000000000000},
        ]
    }
    _patch_client(monkeypatch, {_url(): FakeResponse(200, payload)})

    result = await get_ohlcv(CONTRACT, blockchain="base")
    assert [c.ts for c in result.candles] == [1000000000, 2000000000]


@pytest.mark.asyncio
async def test_get_ohlcv_unavailable_on_empty_data(monkeypatch):
    monkeypatch.setenv("MOBULA_API_KEY", "test-key")
    _patch_no_sleep(monkeypatch)
    _patch_client(monkeypatch, {_url(): FakeResponse(200, {"data": []})})

    result = await get_ohlcv(CONTRACT, blockchain="base")
    assert result.available is False
    assert result.candles == []


@pytest.mark.asyncio
async def test_get_ohlcv_unavailable_on_malformed_rows(monkeypatch):
    """Vérifié en direct : un mauvais nom de paramètre renvoie une erreur de
    schéma zod exploitable -- ce test couvre le cas où `data` existe mais les
    lignes elles-mêmes sont illisibles (jamais une bougie inventée)."""
    monkeypatch.setenv("MOBULA_API_KEY", "test-key")
    _patch_no_sleep(monkeypatch)
    _patch_client(monkeypatch, {_url(): FakeResponse(200, {"data": [{"unexpected": "shape"}]})})

    result = await get_ohlcv(CONTRACT, blockchain="base")
    assert result.available is False


@pytest.mark.asyncio
async def test_get_ohlcv_unavailable_on_network_failure(monkeypatch):
    monkeypatch.setenv("MOBULA_API_KEY", "test-key")
    _patch_no_sleep(monkeypatch)
    _patch_client(
        monkeypatch,
        {_url(): [FakeResponse(429), FakeResponse(429), FakeResponse(429)]},
    )

    result = await get_ohlcv(CONTRACT, blockchain="base")
    assert result.available is False
    assert result.candles == []


@pytest.mark.asyncio
async def test_get_ohlcv_429_retries_then_succeeds(monkeypatch):
    monkeypatch.setenv("MOBULA_API_KEY", "test-key")
    _patch_no_sleep(monkeypatch)
    payload = {"data": [{"v": 1.0, "o": 1.0, "h": 1.0, "l": 1.0, "c": 1.0, "t": 1784073600000}]}
    _patch_client(
        monkeypatch,
        {_url(): [FakeResponse(429), FakeResponse(200, payload)]},
    )

    result = await get_ohlcv(CONTRACT, blockchain="base")
    assert result.available is True
    assert len(result.candles) == 1


@pytest.mark.asyncio
async def test_get_ohlcv_forwards_blockchain_and_address_params(monkeypatch):
    """Vérifié en direct (18/07) : le paramètre s'appelle `address`, pas `asset`
    (une 1ère tentative avec `asset` a renvoyé une erreur de schéma explicite) --
    verrouille ce nom de paramètre précis contre une régression silencieuse."""
    monkeypatch.setenv("MOBULA_API_KEY", "test-key")
    _patch_no_sleep(monkeypatch)
    captured = {}

    class _CapturingClient(FakeClient):
        async def get(self, url, params=None, headers=None):
            captured.update(params or {})
            return FakeResponse(200, {"data": [{"v": 1.0, "o": 1.0, "h": 1.0, "l": 1.0, "c": 1.0, "t": 1000000000000}]})

    monkeypatch.setattr(
        "aria_core.services.mobula.httpx.AsyncClient",
        lambda **kw: _CapturingClient({}),
    )

    await get_ohlcv(CONTRACT, blockchain="solana")

    assert captured["address"] == CONTRACT
    assert captured["blockchain"] == "solana"
