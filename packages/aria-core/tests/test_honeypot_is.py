"""Tests du client Honeypot.is (Item #212 follow-up, 29/07) -- second avis
TEMPORAIRE Base/Ethereum pendant l'épuisement du quota GoPlus. Aucun appel
réseau réel, tout est mocké (même patron que test_rugcheck.py)."""
from __future__ import annotations

import pytest

from aria_core.services.honeypot_is import HoneypotIsResult, check_token

CONTRACT = "0x" + "a" * 40


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

    async def get(self, url, **kwargs):
        queue = self._responses[url]
        if isinstance(queue, list):
            return queue.pop(0)
        return queue


def _patch_client(monkeypatch, responses: dict):
    monkeypatch.setattr(
        "aria_core.services.honeypot_is.httpx.AsyncClient",
        lambda **kw: FakeClient(responses),
    )


def _patch_no_sleep(monkeypatch):
    async def _fake_sleep(_seconds):
        return None

    monkeypatch.setattr("aria_core.services.honeypot_is.asyncio.sleep", _fake_sleep)


def _patch_no_throttle(monkeypatch):
    async def _fake_throttle():
        return None

    monkeypatch.setattr("aria_core.services.honeypot_is._throttle", _fake_throttle)


_URL = "https://api.honeypot.is/v2/IsHoneypot"


# ── check_token -- cas clean (format réel vérifié en direct sur WETH Base) ──

@pytest.mark.asyncio
async def test_confirmed_clean_on_base(monkeypatch):
    _patch_no_sleep(monkeypatch)
    _patch_no_throttle(monkeypatch)
    payload = {
        "honeypotResult": {"isHoneypot": False},
        "simulationResult": {"buyTax": 0, "sellTax": 0, "transferTax": 0},
        "chain": {"id": "8453", "name": "Base"},
    }
    _patch_client(monkeypatch, {_URL: FakeResponse(200, payload)})

    result = await check_token(CONTRACT, chain="base")
    assert result.available is True
    assert result.is_honeypot is False
    assert result.confirmed_clean is True
    assert result.buy_tax == 0.0
    assert result.sell_tax == 0.0


@pytest.mark.asyncio
async def test_confirmed_honeypot_with_real_taxes(monkeypatch):
    _patch_no_sleep(monkeypatch)
    _patch_no_throttle(monkeypatch)
    payload = {
        "honeypotResult": {"isHoneypot": True, "honeypotReason": "high sell tax"},
        "simulationResult": {"buyTax": 5, "sellTax": 99, "transferTax": 0},
    }
    _patch_client(monkeypatch, {_URL: FakeResponse(200, payload)})

    result = await check_token(CONTRACT, chain="base")
    assert result.available is True
    assert result.is_honeypot is True
    assert result.confirmed_clean is False
    assert result.sell_tax == pytest.approx(0.99)


@pytest.mark.asyncio
async def test_ethereum_chain_id_translated(monkeypatch):
    _patch_no_sleep(monkeypatch)
    _patch_no_throttle(monkeypatch)
    payload = {"honeypotResult": {"isHoneypot": False}, "simulationResult": {"buyTax": 0, "sellTax": 0}}
    _patch_client(monkeypatch, {_URL: FakeResponse(200, payload)})

    result = await check_token(CONTRACT, chain="ethereum")
    assert result.available is True
    assert result.is_honeypot is False


@pytest.mark.asyncio
async def test_unsupported_chain_short_circuits_without_network_call():
    result = await check_token(CONTRACT, chain="solana")
    assert result.available is False
    assert "non couverte" in result.error.lower()


@pytest.mark.asyncio
async def test_empty_address_short_circuits_without_network_call():
    result = await check_token("", chain="base")
    assert result.available is False
    assert result.error == "adresse vide"


# ── indisponibilité -- jamais confondu avec "clean" (fail-closed) ──

@pytest.mark.asyncio
async def test_unavailable_on_repeated_429_never_confirmed_clean(monkeypatch):
    _patch_no_sleep(monkeypatch)
    _patch_no_throttle(monkeypatch)
    _patch_client(
        monkeypatch,
        {_URL: [FakeResponse(429), FakeResponse(429), FakeResponse(429)]},
    )

    result = await check_token(CONTRACT, chain="base")
    assert result.available is False
    assert result.confirmed_clean is False
    assert result.error is not None


@pytest.mark.asyncio
async def test_429_retries_then_succeeds(monkeypatch):
    _patch_no_sleep(monkeypatch)
    _patch_no_throttle(monkeypatch)
    payload = {"honeypotResult": {"isHoneypot": False}, "simulationResult": {"buyTax": 0, "sellTax": 0}}
    _patch_client(
        monkeypatch,
        {_URL: [FakeResponse(429), FakeResponse(200, payload)]},
    )

    result = await check_token(CONTRACT, chain="base")
    assert result.available is True
    assert result.confirmed_clean is True


@pytest.mark.asyncio
async def test_missing_honeypot_result_field_stays_unknown_not_clean(monkeypatch):
    """Un champ isHoneypot manquant/malformé ne doit jamais être interprété
    comme "clean par défaut" -- fail-closed cohérent avec le reste du dôme."""
    _patch_no_sleep(monkeypatch)
    _patch_no_throttle(monkeypatch)
    _patch_client(monkeypatch, {_URL: FakeResponse(200, {"summary": {"risk": "low"}})})

    result = await check_token(CONTRACT, chain="base")
    assert result.available is True
    assert result.is_honeypot is None
    assert result.confirmed_clean is False


def test_result_defaults_are_fail_closed():
    r = HoneypotIsResult(address=CONTRACT)
    assert r.confirmed_clean is False
