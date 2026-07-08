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
