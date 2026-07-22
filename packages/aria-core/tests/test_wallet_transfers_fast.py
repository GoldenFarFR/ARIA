"""Fournisseurs RAPIDES de transferts de wallet (Alchemy + Moralis, 22/07) --
aucun appel réseau réel, tout est mocké au niveau httpx.AsyncClient (même
patron que test_dune_client.py). Vérifié séparément par de vrais appels
authentifiés en conditions réelles avant ce fichier (cf. docs/HANDOFF_WALLET_SCORING.md)
-- ces tests couvrent la logique (conversion, cascade, dôme), pas le schéma
externe lui-même (déjà confirmé par les vrais appels)."""
from __future__ import annotations

import pytest

from aria_core.services import wallet_transfers_fast as wtf

WALLET = "0x" + "a" * 40


class FakeResponse:
    def __init__(self, status_code: int, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class FakeClient:
    """``responses``/``calls`` PARTAGÉS entre toutes les instances créées par
    une même ``_patch_client`` -- une séquence de retry/pagination revoit
    sinon la même première réponse en boucle (même correctif que
    test_dune_client.py)."""

    def __init__(self, responses: list, calls: list):
        self._responses = responses
        self.calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def get(self, url, *, params=None, timeout=None):
        self.calls.append(("GET", url, params))
        return self._responses.pop(0)

    async def post(self, url, *, json=None, timeout=None):
        self.calls.append(("POST", url, json))
        return self._responses.pop(0)


def _patch_client(monkeypatch, responses, *, headers_capture: list | None = None):
    shared_responses = list(responses)
    shared_calls: list = []

    def factory(**kw):
        if headers_capture is not None:
            headers_capture.append(kw.get("headers"))
        return FakeClient(shared_responses, shared_calls)

    monkeypatch.setattr(wtf.httpx, "AsyncClient", factory)
    return shared_calls


# ── conversion Alchemy → TokenTransfer ────────────────────────────────────────

def test_alchemy_conversion_maps_fields_correctly():
    item = {
        "hash": "0xabc", "from": "0x111", "to": "0x222",
        "rawContract": {"address": "0xtoken"},
        "asset": "USDC", "value": 42.5,
        "metadata": {"blockTimestamp": "2026-07-22T00:00:00.000Z"},
    }
    t = wtf._alchemy_transfer_to_token_transfer(item)
    assert t.tx_hash == "0xabc"
    assert t.from_address == "0x111"
    assert t.to_address == "0x222"
    assert t.token_address == "0xtoken"
    assert t.token_symbol == "USDC"
    assert t.token_name is None  # jamais fourni par cet endpoint Alchemy -- jamais inventé
    assert t.amount == 42.5
    assert t.timestamp == "2026-07-22T00:00:00.000Z"


def test_alchemy_conversion_skips_malformed_item():
    assert wtf._alchemy_transfer_to_token_transfer({"hash": "0xabc"}) is None  # from/to manquants


# ── conversion Moralis → TokenTransfer ────────────────────────────────────────

def test_moralis_conversion_maps_fields_correctly():
    item = {
        "transaction_hash": "0xdef", "from_address": "0x111", "to_address": "0x222",
        "address": "0xtoken", "token_symbol": "cbBTC", "token_name": "Coinbase Wrapped BTC",
        "value_decimal": "0.032", "block_timestamp": "2026-07-22T00:00:00.000Z",
    }
    t = wtf._moralis_transfer_to_token_transfer(item)
    assert t.tx_hash == "0xdef"
    assert t.token_symbol == "cbBTC"
    assert t.token_name == "Coinbase Wrapped BTC"
    assert t.amount == 0.032
    assert t.timestamp == "2026-07-22T00:00:00.000Z"


def test_moralis_conversion_skips_malformed_item():
    assert wtf._moralis_transfer_to_token_transfer({"transaction_hash": "0xdef"}) is None


# ── _alchemy_get_token_transfers ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_alchemy_no_key_unavailable(monkeypatch):
    monkeypatch.delenv("ALCHEMY_API_KEY", raising=False)
    result = await wtf._alchemy_get_token_transfers(WALLET, limit=10, max_pages=1)
    assert result.available is False


@pytest.mark.asyncio
async def test_alchemy_happy_path(monkeypatch):
    monkeypatch.setenv("ALCHEMY_API_KEY", "k")
    _patch_client(monkeypatch, [
        FakeResponse(200, {"result": {"transfers": [
            {"hash": "0x1", "from": "0xa", "to": "0xb", "rawContract": {"address": "0xt"}, "asset": "USDC", "value": 1.0},
        ]}}),
    ])
    result = await wtf._alchemy_get_token_transfers(WALLET, limit=10, max_pages=1)
    assert result.available is True
    assert len(result.transfers) == 1
    assert result.transfers[0].tx_hash == "0x1"


@pytest.mark.asyncio
async def test_alchemy_paginates_with_page_key(monkeypatch):
    monkeypatch.setenv("ALCHEMY_API_KEY", "k")
    calls = _patch_client(monkeypatch, [
        FakeResponse(200, {"result": {"transfers": [
            {"hash": "0x1", "from": "0xa", "to": "0xb", "rawContract": {"address": "0xt"}, "asset": "USDC", "value": 1.0},
        ], "pageKey": "cursor-1"}}),
        FakeResponse(200, {"result": {"transfers": [
            {"hash": "0x2", "from": "0xa", "to": "0xb", "rawContract": {"address": "0xt"}, "asset": "USDC", "value": 2.0},
        ]}}),
    ])
    result = await wtf._alchemy_get_token_transfers(WALLET, limit=10, max_pages=5)
    assert result.available is True
    assert [t.tx_hash for t in result.transfers] == ["0x1", "0x2"]
    assert result.truncated is False
    # 2e appel doit inclure le pageKey reçu du 1er
    assert calls[1][2]["params"][0]["pageKey"] == "cursor-1"


@pytest.mark.asyncio
async def test_alchemy_stops_at_limit_marks_truncated(monkeypatch):
    monkeypatch.setenv("ALCHEMY_API_KEY", "k")
    _patch_client(monkeypatch, [
        FakeResponse(200, {"result": {"transfers": [
            {"hash": f"0x{i}", "from": "0xa", "to": "0xb", "rawContract": {"address": "0xt"}, "asset": "USDC", "value": 1.0}
            for i in range(5)
        ], "pageKey": "cursor-1"}}),
    ])
    result = await wtf._alchemy_get_token_transfers(WALLET, limit=3, max_pages=5)
    assert len(result.transfers) == 3
    assert result.truncated is True  # pageKey encore présent mais limite atteinte


@pytest.mark.asyncio
async def test_alchemy_error_on_first_page_is_unavailable(monkeypatch):
    monkeypatch.setenv("ALCHEMY_API_KEY", "k")
    _patch_client(monkeypatch, [FakeResponse(500), FakeResponse(500)])
    result = await wtf._alchemy_get_token_transfers(WALLET, limit=10, max_pages=1)
    assert result.available is False


# ── _moralis_get_token_transfers ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_moralis_no_key_unavailable(monkeypatch):
    monkeypatch.delenv("MORALIS_API_KEY", raising=False)
    result = await wtf._moralis_get_token_transfers(WALLET, limit=10, max_pages=1)
    assert result.available is False


@pytest.mark.asyncio
async def test_moralis_happy_path_sends_api_key_header(monkeypatch):
    monkeypatch.setenv("MORALIS_API_KEY", "secret-key")
    headers_capture: list = []
    _patch_client(monkeypatch, [
        FakeResponse(200, {"result": [
            {"transaction_hash": "0x1", "from_address": "0xa", "to_address": "0xb",
             "address": "0xt", "token_symbol": "USDC", "value_decimal": "1.0"},
        ]}),
    ], headers_capture=headers_capture)
    result = await wtf._moralis_get_token_transfers(WALLET, limit=10, max_pages=1)
    assert result.available is True
    assert len(result.transfers) == 1
    assert headers_capture[0] == {"X-API-Key": "secret-key"}  # le bug oublié corrigé avant tout test


@pytest.mark.asyncio
async def test_moralis_paginates_with_cursor(monkeypatch):
    monkeypatch.setenv("MORALIS_API_KEY", "k")
    calls = _patch_client(monkeypatch, [
        FakeResponse(200, {"result": [
            {"transaction_hash": "0x1", "from_address": "0xa", "to_address": "0xb",
             "address": "0xt", "token_symbol": "USDC", "value_decimal": "1.0"},
        ], "cursor": "cursor-1"}),
        FakeResponse(200, {"result": [
            {"transaction_hash": "0x2", "from_address": "0xa", "to_address": "0xb",
             "address": "0xt", "token_symbol": "USDC", "value_decimal": "2.0"},
        ]}),
    ])
    result = await wtf._moralis_get_token_transfers(WALLET, limit=10, max_pages=5)
    assert [t.tx_hash for t in result.transfers] == ["0x1", "0x2"]
    assert calls[1][2]["cursor"] == "cursor-1"


# ── get_fast_token_transfers (cascade + gates) ────────────────────────────────

@pytest.mark.asyncio
async def test_gate_off_unavailable_no_network_call(monkeypatch):
    monkeypatch.delenv("ARIA_WALLET_TRANSFERS_FAST_PROVIDER_ENABLED", raising=False)
    monkeypatch.setenv("ALCHEMY_API_KEY", "k")
    calls = _patch_client(monkeypatch, [])
    result = await wtf.get_fast_token_transfers(WALLET, "base")
    assert result.available is False
    assert calls == []


@pytest.mark.asyncio
async def test_non_base_chain_unavailable_no_network_call(monkeypatch):
    monkeypatch.setenv("ARIA_WALLET_TRANSFERS_FAST_PROVIDER_ENABLED", "1")
    monkeypatch.setenv("ALCHEMY_API_KEY", "k")
    calls = _patch_client(monkeypatch, [])
    result = await wtf.get_fast_token_transfers(WALLET, "ethereum")
    assert result.available is False
    assert calls == []


@pytest.mark.asyncio
async def test_cascade_uses_alchemy_when_available(monkeypatch):
    monkeypatch.setenv("ARIA_WALLET_TRANSFERS_FAST_PROVIDER_ENABLED", "1")
    monkeypatch.setenv("ALCHEMY_API_KEY", "k")
    monkeypatch.setenv("MORALIS_API_KEY", "k")
    _patch_client(monkeypatch, [
        FakeResponse(200, {"result": {"transfers": [
            {"hash": "0x1", "from": "0xa", "to": "0xb", "rawContract": {"address": "0xt"}, "asset": "USDC", "value": 1.0},
        ]}}),
    ])
    result = await wtf.get_fast_token_transfers(WALLET, "base")
    assert result.available is True
    assert result.transfers[0].tx_hash == "0x1"


@pytest.mark.asyncio
async def test_cascade_falls_back_to_moralis_when_alchemy_unavailable(monkeypatch):
    monkeypatch.setenv("ARIA_WALLET_TRANSFERS_FAST_PROVIDER_ENABLED", "1")
    monkeypatch.delenv("ALCHEMY_API_KEY", raising=False)  # Alchemy indisponible (pas de clé)
    monkeypatch.setenv("MORALIS_API_KEY", "k")
    _patch_client(monkeypatch, [
        FakeResponse(200, {"result": [
            {"transaction_hash": "0x2", "from_address": "0xa", "to_address": "0xb",
             "address": "0xt", "token_symbol": "cbBTC", "value_decimal": "1.0"},
        ]}),
    ])
    result = await wtf.get_fast_token_transfers(WALLET, "base")
    assert result.available is True
    assert result.transfers[0].tx_hash == "0x2"


@pytest.mark.asyncio
async def test_cascade_unavailable_when_both_fail(monkeypatch):
    monkeypatch.setenv("ARIA_WALLET_TRANSFERS_FAST_PROVIDER_ENABLED", "1")
    monkeypatch.delenv("ALCHEMY_API_KEY", raising=False)
    monkeypatch.delenv("MORALIS_API_KEY", raising=False)
    result = await wtf.get_fast_token_transfers(WALLET, "base")
    assert result.available is False
