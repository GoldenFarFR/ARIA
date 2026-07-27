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


# ── circuit breaker (Item #125, 27/07) -- one entry per provider since
#    Alchemy/Moralis can fail independently ─────────────────────────────────

@pytest.fixture(autouse=True)
def _reset_circuit_breaker():
    wtf._consecutive_failures.clear()
    wtf._circuit_open_until.clear()
    yield
    wtf._consecutive_failures.clear()
    wtf._circuit_open_until.clear()


def _patch_no_sleep(monkeypatch):
    async def _fake_sleep(_seconds):
        return None

    monkeypatch.setattr(wtf.asyncio, "sleep", _fake_sleep)


@pytest.mark.asyncio
async def test_alchemy_circuit_breaker_opens_and_skips_directly_to_moralis(monkeypatch):
    """3 distinct Alchemy failures must open its breaker -- a 4th candidate
    skips Alchemy entirely (no HTTP call queued for it -- would raise if the
    breaker didn't intervene) and goes straight to Moralis. MORALIS_API_KEY
    intentionally absent during the 3 failures (Moralis degrades before
    touching the network, so it never consumes the shared fake response
    queue meant for Alchemy) -- only set once the breaker is confirmed open."""
    _patch_no_sleep(monkeypatch)
    monkeypatch.setenv("ARIA_WALLET_TRANSFERS_FAST_PROVIDER_ENABLED", "1")
    monkeypatch.setenv("ALCHEMY_API_KEY", "k")
    monkeypatch.delenv("MORALIS_API_KEY", raising=False)

    for _ in range(3):
        _patch_client(monkeypatch, [FakeResponse(500), FakeResponse(500)])
        result = await wtf.get_fast_token_transfers(WALLET, "base")
        assert result.available is False

    assert wtf._in_cooldown("alchemy") is True

    monkeypatch.setenv("MORALIS_API_KEY", "k")
    calls = _patch_client(monkeypatch, [
        FakeResponse(200, {"result": [
            {"transaction_hash": "0x1", "from_address": "0xa", "to_address": "0xb",
             "address": "0xt", "token_symbol": "USDC", "value_decimal": "1.0"},
        ]}),
    ])
    result = await wtf.get_fast_token_transfers(WALLET, "base")
    assert result.available is True
    assert len(calls) == 1  # only the Moralis call -- Alchemy skipped entirely
    assert calls[0][0] == "GET"  # Moralis uses GET, Alchemy uses POST


@pytest.mark.asyncio
async def test_circuit_breaker_recovers_after_success(monkeypatch):
    """2 failures then a success must reset the counter -- 2+1+2 never opens it."""
    _patch_no_sleep(monkeypatch)
    monkeypatch.setenv("ARIA_WALLET_TRANSFERS_FAST_PROVIDER_ENABLED", "1")
    monkeypatch.setenv("ALCHEMY_API_KEY", "k")
    monkeypatch.delenv("MORALIS_API_KEY", raising=False)

    for _ in range(2):
        _patch_client(monkeypatch, [FakeResponse(500), FakeResponse(500), FakeResponse(500)])
        await wtf.get_fast_token_transfers(WALLET, "base")

    _patch_client(monkeypatch, [FakeResponse(200, {"result": {"transfers": []}})])
    ok = await wtf.get_fast_token_transfers(WALLET, "base")
    assert ok.available is True

    for _ in range(2):
        _patch_client(monkeypatch, [FakeResponse(500), FakeResponse(500), FakeResponse(500)])
        await wtf.get_fast_token_transfers(WALLET, "base")

    assert wtf._in_cooldown("alchemy") is False


@pytest.mark.asyncio
async def test_circuit_breaker_expires_after_cooldown(monkeypatch):
    _patch_no_sleep(monkeypatch)
    monkeypatch.setenv("ARIA_WALLET_TRANSFERS_FAST_PROVIDER_ENABLED", "1")
    monkeypatch.setenv("ALCHEMY_API_KEY", "k")
    monkeypatch.delenv("MORALIS_API_KEY", raising=False)

    for _ in range(3):
        _patch_client(monkeypatch, [FakeResponse(500), FakeResponse(500), FakeResponse(500)])
        await wtf.get_fast_token_transfers(WALLET, "base")
    assert wtf._in_cooldown("alchemy") is True

    wtf._circuit_open_until["alchemy"] = 0.0  # simulate cooldown elapsed
    _patch_client(monkeypatch, [FakeResponse(200, {"result": {"transfers": []}})])
    result = await wtf.get_fast_token_transfers(WALLET, "base")
    assert result.available is True


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
