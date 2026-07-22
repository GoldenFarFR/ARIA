"""Filtre de sécurité — le gardien du pool entraînable (déterministe, hors-ligne).

Couvre les barrières marché ET les barrières prioritaires « le dev garde le
pouvoir » (vérification, mint/blacklist/disable, concentration des holders).
"""
from __future__ import annotations

import pytest

from aria_core import momentum_entry as me
from aria_core.skills.acp_onchain_scan import PairSnapshot, TokenScanContext
from aria_core.skills.safety_screen import safety_screen


@pytest.fixture(autouse=True)
def _reset_wash_trading_state():
    # État module-level partagé avec momentum_entry.py (même clé contrat/chaîne) --
    # jamais laisser une candidature d'un test polluer le suivant.
    me._ratio_breach_since.clear()
    yield
    me._ratio_breach_since.clear()


def _ctx(
    *,
    valid=True,
    score=75,
    verdict="SAFE",
    liq=50_000.0,
    has_pair=True,
    verified=True,
    has_mint=False,
    has_blacklist=False,
    has_disable=False,
    top_holder=15.0,
) -> TokenScanContext:
    pair = PairSnapshot(pair_address="0xpool", liquidity_usd=liq) if has_pair else None
    return TokenScanContext(
        contract="0x" + "a" * 40,
        valid_address=valid,
        best_pair=pair,
        security_score=score,
        lite_verdict=verdict,
        contract_verified=verified,
        has_mint=has_mint,
        has_blacklist=has_blacklist,
        has_disable_transfers=has_disable,
        top_holder_pct=top_holder,
    )


def test_clean_token_passes():
    r = safety_screen(_ctx())
    assert r.passed is True
    assert "screené" in r.reasons[0]


# ── barrières marché ────────────────────────────────────────────────────────

def test_low_liquidity_alone_passes_via_bypass():
    # 22/07 -- item #5 : liquidité faible SEULE ne bloque plus si score/verdict/mint
    # sont déjà propres -- le risque scam/rug est déjà écarté par le scoring lui-même.
    r = safety_screen(_ctx(liq=10_000.0))
    assert r.passed is True
    assert any("tolérée" in x for x in r.reasons)


def test_low_liquidity_with_bad_score_still_fails():
    # Le bypass exige TOUT propre -- un score bas en plus de la liquidité faible
    # reste bloquant (jamais un blanc-seing générique sur la liquidité).
    assert safety_screen(_ctx(liq=10_000.0, score=55)).passed is False


def test_low_score_fails():
    assert safety_screen(_ctx(score=55)).passed is False


def test_non_safe_verdict_fails():
    assert safety_screen(_ctx(verdict="CAUTION")).passed is False


def test_no_pair_fails():
    assert safety_screen(_ctx(has_pair=False)).passed is False


def test_invalid_address_fails():
    assert safety_screen(_ctx(valid=False)).passed is False


# ── barrières « le dev garde le pouvoir » (prioritaires) ─────────────────────

def test_unverified_contract_fails():
    r = safety_screen(_ctx(verified=False))
    assert r.passed is False
    assert any("non vérifié" in x for x in r.reasons)


def test_unknown_verification_fails_closed():
    # Donnée inconnue (None) => on n'inclut pas ce qu'on ne peut pas confirmer.
    assert safety_screen(_ctx(verified=None)).passed is False


def test_mint_function_fails():
    r = safety_screen(_ctx(has_mint=True))
    assert r.passed is False
    assert any("mint" in x for x in r.reasons)


def test_blacklist_fails():
    assert safety_screen(_ctx(has_blacklist=True)).passed is False


def test_disable_transfers_fails():
    assert safety_screen(_ctx(has_disable=True)).passed is False


# ── barrière concentration (baleine) ─────────────────────────────────────────

def test_whale_concentration_fails():
    r = safety_screen(_ctx(top_holder=45.0))
    assert r.passed is False
    assert any("holder dominant" in x for x in r.reasons)


def test_unknown_distribution_fails_closed():
    r = safety_screen(_ctx(top_holder=None))
    assert r.passed is False
    assert any("distribution des holders inconnue" in x for x in r.reasons)


def test_concentration_threshold_tunable():
    # 35% échoue au seuil par défaut (30), passe si on relève à 40.
    assert safety_screen(_ctx(top_holder=35.0)).passed is False
    assert safety_screen(_ctx(top_holder=35.0), max_top_holder_pct=40.0).passed is True


# ── hard_fail : mécanisme malveillant (définitif) vs aspect d'investissement
# (mou, réévalué avec la maturité du projet) -- décision opérateur 10/07 ────────

def test_no_pair_is_soft_fail_not_hard():
    r = safety_screen(_ctx(has_pair=False))
    assert r.passed is False
    assert r.hard_fail is False


def test_low_liquidity_is_soft_fail_not_hard():
    # Combiné à un score bas (sinon le bypass #5 le ferait passer) pour rester un
    # vrai cas d'échec ici -- la liquidité seule reste un échec MOU (pas hard_fail).
    r = safety_screen(_ctx(liq=1_000.0, score=55))
    assert r.passed is False
    assert r.hard_fail is False


def test_unverified_contract_is_soft_fail_not_hard():
    r = safety_screen(_ctx(verified=False))
    assert r.passed is False
    assert r.hard_fail is False


def test_whale_concentration_is_soft_fail_not_hard():
    r = safety_screen(_ctx(top_holder=45.0))
    assert r.passed is False
    assert r.hard_fail is False


def test_invalid_address_is_hard_fail():
    r = safety_screen(_ctx(valid=False))
    assert r.hard_fail is True


def test_mint_function_is_hard_fail():
    ctx = _ctx(has_mint=True)
    ctx.mint_authority = "eoa"  # confirmé (pas 'unknown', qui reste mou)
    assert safety_screen(ctx).hard_fail is True


def test_blacklist_is_hard_fail():
    assert safety_screen(_ctx(has_blacklist=True)).hard_fail is True


def test_disable_transfers_is_hard_fail():
    assert safety_screen(_ctx(has_disable=True)).hard_fail is True


# ── item #1 (22/07) : anti-wash-trading réutilisé du pipeline momentum ────────
# (MAX_VOLUME_TO_LIQUIDITY_RATIO + _wash_trading_ratio_confirmed, TEL QUEL, jamais
# une deuxième constante indépendante qui pourrait diverger).

def _ctx_with_volume(*, liq: float = 50_000.0, volume_24h: float = 10_000.0) -> TokenScanContext:
    ctx = _ctx(liq=liq)
    ctx.best_pair.volume_24h_usd = volume_24h
    return ctx


def test_wash_trading_single_reading_not_rejected():
    # Une lecture extrême ISOLÉE ne rejette pas encore -- même philosophie que le
    # pipeline momentum : une candidature démarre, mais rien n'est confirmé.
    ctx = _ctx_with_volume(liq=50_000.0, volume_24h=50_000.0 * 25.0)
    r = safety_screen(ctx)
    assert r.passed is True
    assert (ctx.contract, "base") in me._ratio_breach_since


def test_wash_trading_confirmed_after_sustained_breach_rejects():
    ctx = _ctx_with_volume(liq=50_000.0, volume_24h=50_000.0 * 25.0)
    safety_screen(ctx)  # démarre la candidature
    me._ratio_breach_since[(ctx.contract, "base")] -= (me._WASH_TRADING_CONFIRMATION_SECONDS + 1)
    r = safety_screen(ctx)
    assert r.passed is False
    assert any("wash-trading" in x for x in r.reasons)
    assert r.hard_fail is False  # comportement de marché, jamais un mécanisme confirmé dans le contrat


def test_wash_trading_normal_ratio_never_flagged():
    ctx = _ctx_with_volume(liq=50_000.0, volume_24h=5_000.0)
    assert safety_screen(ctx).passed is True


# Item #2 (slippage_modifiable, signal GoPlus) : tests aux côtés de hidden_owner/
# can_take_back_ownership dans tests/test_goplus.py, pas dupliqués ici -- même
# convention que le reste de ce fichier GoPlus déjà en place.
