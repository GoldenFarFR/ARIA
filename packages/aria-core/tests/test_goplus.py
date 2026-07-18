"""GoPlus (détection honeypot) — parsing du client, absorption dans le contexte de scan,
et barrières du filtre de sécurité. 100 % hors-ligne (aucun appel réseau réel)."""
from __future__ import annotations

import time

import httpx
import pytest

from aria_core.services.goplus import UNAVAILABLE, GoPlusClient, TokenSecurity, _tax, _tri
from aria_core.skills.acp_onchain_scan import (
    PairSnapshot,
    TokenScanContext,
    _apply_honeypot_signals,
)
from aria_core.skills.safety_screen import safety_screen

ADDR = "0x" + "b" * 40


# ── helpers de parsing ────────────────────────────────────────────────────────

def test_tri_parsing():
    assert _tri("1") is True
    assert _tri("0") is False
    assert _tri("") is None
    assert _tri(None) is None
    assert _tri("2") is None


def test_tax_parsing():
    assert _tax("0.05") == 0.05
    assert _tax("0") == 0.0
    assert _tax("") is None
    assert _tax(None) is None
    assert _tax("pas un nombre") is None


def _client_returning(payload):
    c = GoPlusClient()

    async def fake_get_json(path, *, params=None):
        return payload, None

    c._get_json = fake_get_json  # type: ignore[method-assign]
    return c


# ── retry sur le rate-limit déguisé en HTTP 200 (code 4029), 17/07 ────────────

class _FakeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx
            raise httpx.HTTPStatusError("error", request=None, response=self)


class _FakeHttpClient:
    def __init__(self, responses):
        self._responses = responses

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def get(self, url, params=None, headers=None):
        queue = self._responses[url]
        return queue.pop(0) if isinstance(queue, list) else queue


def _patch_goplus_http(monkeypatch, responses: dict):
    monkeypatch.setattr(
        "aria_core.services.goplus.httpx.AsyncClient", lambda **kw: _FakeHttpClient(responses),
    )


def _patch_goplus_no_sleep(monkeypatch):
    async def _fake_sleep(_seconds):
        return None

    monkeypatch.setattr("aria_core.services.goplus.asyncio.sleep", _fake_sleep)


@pytest.mark.asyncio
async def test_code_4029_disguised_as_http_200_is_retried_then_succeeds(monkeypatch):
    """Bug réel trouvé le 17/07 en creusant le faible débit d'achats du test 1M$ :
    GoPlus signale son rate-limit via un HTTP 200 avec code=4029 dans le corps, pas
    un vrai HTTP 429 -- sans ce correctif, ce candidat tombait silencieusement en
    "aucune donnée pour ce contrat" au lieu de retenter."""
    _patch_goplus_no_sleep(monkeypatch)
    client = GoPlusClient()
    url = f"{client.base_url}/token_security/8453"
    ok_payload = {"code": 1, "message": "OK", "result": {ADDR.lower(): {"is_honeypot": "0"}}}
    _patch_goplus_http(
        monkeypatch,
        {
            url: [
                _FakeResponse(200, {"code": 4029, "message": "too many requests"}),
                _FakeResponse(200, {"code": 4029, "message": "too many requests"}),
                _FakeResponse(200, ok_payload),
            ]
        },
    )

    security = await client.get_token_security(ADDR, chain_id="8453")

    assert security.available is True
    assert security.is_honeypot is False


@pytest.mark.asyncio
async def test_code_4029_gives_up_after_three_attempts(monkeypatch):
    _patch_goplus_no_sleep(monkeypatch)
    client = GoPlusClient()
    url = f"{client.base_url}/token_security/8453"
    _patch_goplus_http(
        monkeypatch,
        {
            url: [
                _FakeResponse(200, {"code": 4029, "message": "too many requests"}),
                _FakeResponse(200, {"code": 4029, "message": "too many requests"}),
                _FakeResponse(200, {"code": 4029, "message": "too many requests"}),
            ]
        },
    )

    security = await client.get_token_security(ADDR, chain_id="8453")

    assert security.available is False
    assert UNAVAILABLE in security.error
    assert "rate limit" in security.error


# ── authentification optionnelle app_key/app_secret (#207, 18/07) ────────────
# Sépare le quota d'ARIA de la limite anonyme par IP (~30 req/min), cause directe
# des code 4029 observés le 17-18/07. Sans identifiants -> comportement historique
# inchangé (chemin public sans clé).

class _FakeAuthHttpClient:
    """Fake httpx.AsyncClient supportant get() ET post() -- nécessaire pour tester
    le renouvellement de l'access_token (POST /token) suivi d'un appel authentifié
    (GET token_security). Les listes d'appels sont partagées entre instances (une
    nouvelle instance est créée à chaque `async with httpx.AsyncClient(...)`)."""

    def __init__(self, *, post_response=None, post_raises=None, get_responses=None, post_calls, get_calls):
        self._post_response = post_response
        self._post_raises = post_raises
        self._get_responses = get_responses or {}
        self._post_calls = post_calls
        self._get_calls = get_calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def post(self, url, data=None):
        self._post_calls.append({"url": url, "data": data})
        if self._post_raises is not None:
            raise self._post_raises
        return self._post_response

    async def get(self, url, params=None, headers=None):
        self._get_calls.append({"url": url, "params": params, "headers": headers})
        queue = self._get_responses[url]
        return queue.pop(0) if isinstance(queue, list) else queue


def _patch_goplus_auth_http(monkeypatch, *, post_response=None, post_raises=None, get_responses=None):
    post_calls: list = []
    get_calls: list = []
    monkeypatch.setattr(
        "aria_core.services.goplus.httpx.AsyncClient",
        lambda **kw: _FakeAuthHttpClient(
            post_response=post_response,
            post_raises=post_raises,
            get_responses=get_responses,
            post_calls=post_calls,
            get_calls=get_calls,
        ),
    )
    return post_calls, get_calls


def test_goplus_authenticated_reflects_env_vars(monkeypatch):
    from aria_core.services.goplus import goplus_authenticated

    monkeypatch.delenv("GOPLUS_APP_KEY", raising=False)
    monkeypatch.delenv("GOPLUS_APP_SECRET", raising=False)
    assert goplus_authenticated() is False

    monkeypatch.setenv("GOPLUS_APP_KEY", "test-key")
    assert goplus_authenticated() is False  # une seule des deux valeurs ne suffit pas

    monkeypatch.setenv("GOPLUS_APP_SECRET", "test-secret")
    assert goplus_authenticated() is True


@pytest.mark.asyncio
async def test_ensure_access_token_returns_none_without_credentials(monkeypatch):
    monkeypatch.delenv("GOPLUS_APP_KEY", raising=False)
    monkeypatch.delenv("GOPLUS_APP_SECRET", raising=False)
    post_calls, _ = _patch_goplus_auth_http(monkeypatch)
    client = GoPlusClient()

    token = await client._ensure_access_token()

    assert token is None
    assert post_calls == []  # aucun appel réseau tenté sans identifiants


@pytest.mark.asyncio
async def test_ensure_access_token_fetches_and_caches(monkeypatch):
    monkeypatch.setenv("GOPLUS_APP_KEY", "test-key")
    monkeypatch.setenv("GOPLUS_APP_SECRET", "test-secret")
    post_calls, _ = _patch_goplus_auth_http(
        monkeypatch,
        post_response=_FakeResponse(200, {"result": {"access_token": "tok-1", "expires_in": 7200}}),
    )
    client = GoPlusClient()

    first = await client._ensure_access_token()
    second = await client._ensure_access_token()

    assert first == "tok-1"
    assert second == "tok-1"
    assert len(post_calls) == 1  # mise en cache -- pas de second appel réseau


@pytest.mark.asyncio
async def test_ensure_access_token_refreshes_near_expiry(monkeypatch):
    monkeypatch.setenv("GOPLUS_APP_KEY", "test-key")
    monkeypatch.setenv("GOPLUS_APP_SECRET", "test-secret")
    post_calls, _ = _patch_goplus_auth_http(
        monkeypatch,
        post_response=_FakeResponse(200, {"result": {"access_token": "tok-2", "expires_in": 7200}}),
    )
    client = GoPlusClient()
    await client._ensure_access_token()
    # Force l'état comme si le jeton expirait dans 1 seconde (sous la marge de 300s).
    client._token_expires_at = time.time() + 1

    await client._ensure_access_token()

    assert len(post_calls) == 2  # renouvelé car sous la marge de sécurité


@pytest.mark.asyncio
async def test_ensure_access_token_network_failure_falls_back_silently(monkeypatch):
    monkeypatch.setenv("GOPLUS_APP_KEY", "test-key")
    monkeypatch.setenv("GOPLUS_APP_SECRET", "test-secret")
    _patch_goplus_auth_http(monkeypatch, post_raises=httpx.ConnectError("boom"))
    client = GoPlusClient()

    token = await client._ensure_access_token()  # ne doit jamais lever

    assert token is None


@pytest.mark.asyncio
async def test_get_json_sends_access_token_header_when_authenticated(monkeypatch):
    _patch_goplus_no_sleep(monkeypatch)
    monkeypatch.setenv("GOPLUS_APP_KEY", "test-key")
    monkeypatch.setenv("GOPLUS_APP_SECRET", "test-secret")
    client = GoPlusClient()
    url = f"{client.base_url}/token_security/8453"
    ok_payload = {"code": 1, "message": "OK", "result": {ADDR.lower(): {"is_honeypot": "0"}}}
    _, get_calls = _patch_goplus_auth_http(
        monkeypatch,
        post_response=_FakeResponse(200, {"result": {"access_token": "tok-3", "expires_in": 7200}}),
        get_responses={url: [_FakeResponse(200, ok_payload)]},
    )

    security = await client.get_token_security(ADDR, chain_id="8453")

    assert security.available is True
    assert get_calls[0]["headers"] == {"access-token": "tok-3"}


@pytest.mark.asyncio
async def test_get_json_sends_no_header_without_credentials(monkeypatch):
    _patch_goplus_no_sleep(monkeypatch)
    monkeypatch.delenv("GOPLUS_APP_KEY", raising=False)
    monkeypatch.delenv("GOPLUS_APP_SECRET", raising=False)
    client = GoPlusClient()
    url = f"{client.base_url}/token_security/8453"
    ok_payload = {"code": 1, "message": "OK", "result": {ADDR.lower(): {"is_honeypot": "0"}}}
    _patch_goplus_http(monkeypatch, {url: [_FakeResponse(200, ok_payload)]})

    security = await client.get_token_security(ADDR, chain_id="8453")

    assert security.available is True  # comportement historique inchangé sans clé


# ── client GoPlus ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_token_security_honeypot():
    payload = {
        "code": 1,
        "message": "OK",
        "result": {
            ADDR.lower(): {
                "is_honeypot": "1",
                "sell_tax": "0.99",
                "buy_tax": "0",
                "cannot_sell_all": "1",
                "hidden_owner": "1",
                "can_take_back_ownership": "0",
                "is_open_source": "1",
            }
        },
    }
    sec = await _client_returning(payload).get_token_security(ADDR)
    assert sec.available is True
    assert sec.is_honeypot is True
    assert sec.cannot_sell_all is True
    assert sec.sell_tax == 0.99
    assert sec.hidden_owner is True
    assert sec.can_take_back_ownership is False


@pytest.mark.asyncio
async def test_get_token_security_clean():
    payload = {
        "code": 1,
        "result": {
            ADDR.lower(): {
                "is_honeypot": "0",
                "sell_tax": "0",
                "buy_tax": "0",
                "cannot_sell_all": "0",
                "hidden_owner": "0",
                "can_take_back_ownership": "0",
                "is_open_source": "1",
            }
        },
    }
    sec = await _client_returning(payload).get_token_security(ADDR)
    assert sec.available is True
    assert sec.is_honeypot is False
    assert sec.sell_tax == 0.0


@pytest.mark.asyncio
async def test_get_token_security_case_insensitive_key():
    # GoPlus renvoie parfois la clé dans une casse différente : on prend l'entrée dispo.
    payload = {"code": 1, "result": {ADDR.upper(): {"is_honeypot": "1"}}}
    sec = await _client_returning(payload).get_token_security(ADDR)
    assert sec.available is True
    assert sec.is_honeypot is True


@pytest.mark.asyncio
async def test_get_token_security_empty_result_unavailable():
    payload = {"code": 4, "message": "contract not found", "result": {}}
    sec = await _client_returning(payload).get_token_security(ADDR)
    assert sec.available is False
    assert sec.is_honeypot is None
    assert sec.error is not None


@pytest.mark.asyncio
async def test_get_token_security_transport_error_graceful():
    c = GoPlusClient()

    async def fake(path, *, params=None):
        return None, "donnée GoPlus indisponible (timeout GoPlus)"

    c._get_json = fake  # type: ignore[method-assign]
    sec = await c.get_token_security(ADDR)
    assert sec.available is False
    assert sec.error is not None
    assert sec.is_honeypot is None


# ── absorption dans le contexte de scan ───────────────────────────────────────

def test_apply_honeypot_signals_confirmed():
    ctx = TokenScanContext(contract=ADDR, valid_address=True, security_score=80, lite_verdict="SAFE")
    sec = TokenSecurity(
        address=ADDR, available=True, is_honeypot=True, cannot_sell_all=True, sell_tax=0.5
    )
    _apply_honeypot_signals(ctx, sec)
    assert ctx.is_honeypot is True
    assert ctx.lite_verdict == "DANGER"
    assert ctx.security_score < 80
    assert any("HONEYPOT" in f for f in ctx.risk_flags)


def test_apply_honeypot_signals_unavailable_is_graceful():
    ctx = TokenScanContext(contract=ADDR, valid_address=True, security_score=80, lite_verdict="SAFE")
    _apply_honeypot_signals(ctx, TokenSecurity(address=ADDR, available=False, error="indispo"))
    # Donnée inconnue → aucune décision dégradée, seulement une note d'absence.
    assert ctx.is_honeypot is None
    assert ctx.lite_verdict == "SAFE"
    assert ctx.security_score == 80
    assert any("GoPlus" in f for f in ctx.risk_flags)


def test_apply_honeypot_signals_clean_no_penalty():
    ctx = TokenScanContext(contract=ADDR, valid_address=True, security_score=80, lite_verdict="SAFE")
    sec = TokenSecurity(
        address=ADDR, available=True, is_honeypot=False, cannot_sell_all=False,
        sell_tax=0.0, buy_tax=0.0, hidden_owner=False, can_take_back_ownership=False,
    )
    _apply_honeypot_signals(ctx, sec)
    assert ctx.is_honeypot is False
    assert ctx.lite_verdict == "SAFE"
    assert ctx.security_score == 80


# ── barrières du filtre de sécurité ───────────────────────────────────────────

def _clean_ctx() -> TokenScanContext:
    return TokenScanContext(
        contract=ADDR,
        valid_address=True,
        best_pair=PairSnapshot(pair_address="0xpool", liquidity_usd=50_000.0),
        security_score=75,
        lite_verdict="SAFE",
        contract_verified=True,
        has_mint=False,
        has_blacklist=False,
        has_disable_transfers=False,
        top_holder_pct=15.0,
    )


def test_screen_clean_passes_when_honeypot_fields_none():
    # Sans scan honeypot (champs None), le comportement est inchangé : un token propre passe.
    assert safety_screen(_clean_ctx()).passed is True


def test_screen_honeypot_confirmed_fails_hard():
    c = _clean_ctx()
    c.is_honeypot = True
    r = safety_screen(c)
    assert r.passed is False
    assert r.hard_fail is True
    assert any("honeypot" in x.lower() for x in r.reasons)


def test_screen_cannot_sell_fails():
    c = _clean_ctx()
    c.cannot_sell = True
    assert safety_screen(c).passed is False


def test_screen_high_sell_tax_fails():
    c = _clean_ctx()
    c.sell_tax = 0.30
    assert safety_screen(c).passed is False


def test_screen_moderate_sell_tax_ok():
    c = _clean_ctx()
    c.sell_tax = 0.05
    assert safety_screen(c).passed is True


def test_screen_hidden_owner_fails():
    c = _clean_ctx()
    c.hidden_owner = True
    assert safety_screen(c).passed is False


def test_screen_take_back_ownership_fails():
    c = _clean_ctx()
    c.can_take_back_ownership = True
    assert safety_screen(c).passed is False


# ── Malicious Address API (AML, #157) ──────────────────────────────────────

@pytest.mark.asyncio
async def test_get_address_security_flags_malicious():
    payload = {
        "code": 1,
        "message": "ok",
        "result": {
            "sanctioned": "1",
            "phishing_activities": "0",
            "mixer": "0",
            "contract_address": "0",
            "data_source": "",
            "number_of_malicious_contracts_created": "0",
        },
    }
    c = _client_returning(payload)

    result = await c.get_address_security(ADDR)

    assert result.available is True
    assert result.is_malicious is True
    assert result.flags == {"sanctioned": True}


@pytest.mark.asyncio
async def test_get_address_security_clean_address():
    """Vérifié en direct (14/07) : réponse réelle sur l'adresse burn Base,
    tous drapeaux à "0" -- couverture Base confirmée, pas la densité des
    données malveillantes."""
    payload = {
        "code": 1,
        "message": "ok",
        "result": {
            "cybercrime": "0", "money_laundering": "0", "gas_abuse": "0",
            "financial_crime": "0", "darkweb_transactions": "0", "reinit": "0",
            "phishing_activities": "0", "fake_kyc": "0", "blacklist_doubt": "0",
            "fake_standard_interface": "0", "stealing_attack": "0",
            "blackmail_activities": "0", "sanctioned": "0",
            "malicious_mining_activities": "0", "mixer": "0", "fake_token": "0",
            "honeypot_related_address": "0",
            "contract_address": "1", "data_source": "",
            "number_of_malicious_contracts_created": "0",
        },
    }
    c = _client_returning(payload)

    result = await c.get_address_security(ADDR)

    assert result.available is True
    assert result.is_malicious is False
    assert result.flags == {}


@pytest.mark.asyncio
async def test_get_address_security_meta_fields_never_flags():
    """contract_address/data_source/number_of_malicious_contracts_created sont
    des métadonnées, jamais des catégories de risque -- même avec une valeur
    "positive", elles ne doivent jamais faire basculer is_malicious."""
    payload = {
        "code": 1,
        "result": {
            "contract_address": "1",
            "number_of_malicious_contracts_created": "5",
            "data_source": "some_source",
        },
    }
    c = _client_returning(payload)

    result = await c.get_address_security(ADDR)

    assert result.is_malicious is False
    assert result.flags == {}


@pytest.mark.asyncio
async def test_get_address_security_bad_code_unavailable():
    c = _client_returning({"code": 0, "message": "address invalid"})

    result = await c.get_address_security(ADDR)

    assert result.available is False
    assert "address invalid" in result.error


@pytest.mark.asyncio
async def test_get_address_security_empty_address_no_call():
    c = GoPlusClient()

    result = await c.get_address_security("")

    assert result.available is False
    assert result.error == "adresse vide"


@pytest.mark.asyncio
async def test_get_address_security_network_error_propagates():
    c = GoPlusClient()

    async def fake_get_json(path, *, params=None):
        return None, "donnée GoPlus indisponible (timeout GoPlus)"

    c._get_json = fake_get_json  # type: ignore[method-assign]

    result = await c.get_address_security(ADDR)

    assert result.available is False
    assert "indisponible" in result.error


@pytest.mark.asyncio
async def test_get_address_security_forwards_explicit_chain_id():
    """#157, 14/07 -- multi-chaînes : chain_id doit être transmis tel quel dans
    les params, jamais silencieusement retombé sur Base quand l'appelant en
    passe un autre (trouvé non testé en explorant le code ce soir)."""
    c = GoPlusClient()
    seen_params = {}

    async def fake_get_json(path, *, params=None):
        seen_params.update(params or {})
        return {"code": 1, "message": "ok", "result": {"contract_address": "0", "data_source": ""}}, None

    c._get_json = fake_get_json  # type: ignore[method-assign]

    await c.get_address_security(ADDR, chain_id="42220")  # Celo

    assert seen_params.get("chain_id") == "42220"


@pytest.mark.asyncio
async def test_get_address_security_defaults_to_base_chain_id():
    c = GoPlusClient()
    seen_params = {}

    async def fake_get_json(path, *, params=None):
        seen_params.update(params or {})
        return {"code": 1, "message": "ok", "result": {"contract_address": "0", "data_source": ""}}, None

    c._get_json = fake_get_json  # type: ignore[method-assign]

    await c.get_address_security(ADDR)  # pas de chain_id explicite

    assert seen_params.get("chain_id") == "8453"
