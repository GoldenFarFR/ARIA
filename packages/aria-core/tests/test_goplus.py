"""GoPlus (détection honeypot) — parsing du client, absorption dans le contexte de scan,
et barrières du filtre de sécurité. 100 % hors-ligne (aucun appel réseau réel)."""
from __future__ import annotations

import pytest

from aria_core.services.goplus import GoPlusClient, TokenSecurity, _tax, _tri
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
