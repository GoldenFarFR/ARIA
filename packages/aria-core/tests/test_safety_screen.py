"""Filtre de sécurité — le gardien du pool entraînable (déterministe, hors-ligne).

Couvre les barrières marché ET les barrières prioritaires « le dev garde le
pouvoir » (vérification, mint/blacklist/disable, concentration des holders).
"""
from __future__ import annotations

from aria_core.skills.acp_onchain_scan import PairSnapshot, TokenScanContext
from aria_core.skills.safety_screen import safety_screen


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

def test_low_liquidity_fails():
    assert safety_screen(_ctx(liq=10_000.0)).passed is False


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
