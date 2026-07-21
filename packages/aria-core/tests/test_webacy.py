"""Tests du client Webacy (21/07) -- 2e avis de sécurité contrat, complément à
GoPlus. Aucun appel réseau réel, tout est mocké (même patron que
test_mobula.py/test_goplus.py). Chemin exact (``/api/v1/risk-score/contract/
{address}``) et schéma de réponse (``score``/``tags``/``categories``) confirmés
via l'OpenAPI officiel (docs.webacy.com/openapi.json) -- reste néanmoins PAS
confirmé contre un vrai appel (aucune clé API disponible au moment de
l'écriture), cf. docstring du module."""
from __future__ import annotations

import pytest

from aria_core.services.webacy import get_contract_risk, webacy_configured

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
        "aria_core.services.webacy.httpx.AsyncClient",
        lambda **kw: FakeClient(responses),
    )


def _patch_no_sleep(monkeypatch):
    async def _fake_sleep(_seconds):
        return None

    monkeypatch.setattr("aria_core.services.webacy.asyncio.sleep", _fake_sleep)


def _url() -> str:
    return f"https://api.webacy.com/api/v1/risk-score/contract/{CONTRACT}"


# ── webacy_configured ───────────────────────────────────────────────────────

def test_webacy_configured_false_when_no_key(monkeypatch):
    monkeypatch.delenv("WEBACY_API_KEY", raising=False)
    assert webacy_configured() is False


def test_webacy_configured_true_when_key_present(monkeypatch):
    monkeypatch.setenv("WEBACY_API_KEY", "test-key")
    assert webacy_configured() is True


# ── get_contract_risk -- pas de clé : aucun appel réseau ────────────────────

@pytest.mark.asyncio
async def test_get_contract_risk_unavailable_without_key_no_network_call(monkeypatch):
    monkeypatch.delenv("WEBACY_API_KEY", raising=False)
    called = {"network": False}

    def _fail_if_called(**kw):
        called["network"] = True
        raise AssertionError("ne doit jamais appeler le réseau sans clé")

    monkeypatch.setattr("aria_core.services.webacy.httpx.AsyncClient", _fail_if_called)

    result = await get_contract_risk(CONTRACT, chain="base")
    assert result.available is False
    assert called["network"] is False


# ── chaîne non couverte -- jamais une URL devinée ────────────────────────────

@pytest.mark.asyncio
async def test_get_contract_risk_unavailable_on_unknown_chain(monkeypatch):
    monkeypatch.setenv("WEBACY_API_KEY", "test-key")
    called = {"network": False}

    def _fail_if_called(**kw):
        called["network"] = True
        raise AssertionError("ne doit jamais appeler le réseau sur une chaîne inconnue")

    monkeypatch.setattr("aria_core.services.webacy.httpx.AsyncClient", _fail_if_called)

    result = await get_contract_risk(CONTRACT, chain="fantom")
    assert result.available is False
    assert called["network"] is False


# ── cas clean (schéma basé sur la doc officielle, pas encore confirmé en direct) ──

@pytest.mark.asyncio
async def test_get_contract_risk_parses_clean_token(monkeypatch):
    monkeypatch.setenv("WEBACY_API_KEY", "test-key")
    _patch_no_sleep(monkeypatch)
    payload = {"score": 5, "tags": [], "categories": {}}
    _patch_client(monkeypatch, {_url(): FakeResponse(200, payload)})

    result = await get_contract_risk(CONTRACT, chain="base")

    assert result.available is True
    assert result.score == 5
    assert result.is_drainer is False


@pytest.mark.asyncio
async def test_get_contract_risk_detects_drainer_category(monkeypatch):
    monkeypatch.setenv("WEBACY_API_KEY", "test-key")
    _patch_no_sleep(monkeypatch)
    payload = {
        "score": 92,
        "tags": ["centralized_risk_high"],
        "categories": {
            "contract_possible_drainer": {
                "name": "Centralized Risk High",
                "description": "exploitable logic that can be used to drain funds",
            }
        },
    }
    _patch_client(monkeypatch, {_url(): FakeResponse(200, payload)})

    result = await get_contract_risk(CONTRACT, chain="base")

    assert result.available is True
    assert result.is_drainer is True
    assert "contract_possible_drainer" in result.categories


@pytest.mark.asyncio
async def test_get_contract_risk_forwards_translated_chain_param(monkeypatch):
    monkeypatch.setenv("WEBACY_API_KEY", "test-key")
    _patch_no_sleep(monkeypatch)
    captured = {}

    class _CapturingClient(FakeClient):
        async def get(self, url, params=None, headers=None):
            captured["params"] = params
            captured["headers"] = headers
            return FakeResponse(200, {"score": 1, "tags": [], "categories": {}})

    monkeypatch.setattr(
        "aria_core.services.webacy.httpx.AsyncClient",
        lambda **kw: _CapturingClient({}),
    )

    await get_contract_risk(CONTRACT, chain="solana")

    assert captured["params"]["chain"] == "sol"
    assert captured["headers"]["x-api-key"] == "test-key"


# ── erreurs / dégradation ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_contract_risk_unavailable_on_401(monkeypatch):
    monkeypatch.setenv("WEBACY_API_KEY", "bad-key")
    _patch_no_sleep(monkeypatch)
    _patch_client(monkeypatch, {_url(): FakeResponse(401)})

    result = await get_contract_risk(CONTRACT, chain="base")
    assert result.available is False


@pytest.mark.asyncio
async def test_get_contract_risk_unavailable_after_3x_429(monkeypatch):
    monkeypatch.setenv("WEBACY_API_KEY", "test-key")
    _patch_no_sleep(monkeypatch)
    _patch_client(
        monkeypatch,
        {_url(): [FakeResponse(429), FakeResponse(429), FakeResponse(429)]},
    )

    result = await get_contract_risk(CONTRACT, chain="base")
    assert result.available is False


@pytest.mark.asyncio
async def test_get_contract_risk_429_retries_then_succeeds(monkeypatch):
    monkeypatch.setenv("WEBACY_API_KEY", "test-key")
    _patch_no_sleep(monkeypatch)
    payload = {"score": 10, "tags": [], "categories": {}}
    _patch_client(
        monkeypatch,
        {_url(): [FakeResponse(429), FakeResponse(200, payload)]},
    )

    result = await get_contract_risk(CONTRACT, chain="base")
    assert result.available is True
    assert result.score == 10


@pytest.mark.asyncio
async def test_get_contract_risk_unavailable_on_malformed_response(monkeypatch):
    monkeypatch.setenv("WEBACY_API_KEY", "test-key")
    _patch_no_sleep(monkeypatch)
    _patch_client(monkeypatch, {_url(): FakeResponse(200, ["unexpected", "list"])})

    result = await get_contract_risk(CONTRACT, chain="base")
    assert result.available is False
