"""Filtre de sécurité — le gardien du pool entraînable (déterministe, hors-ligne)."""
from __future__ import annotations

from aria_core.skills.acp_onchain_scan import PairSnapshot, TokenScanContext
from aria_core.skills.safety_screen import safety_screen


def _ctx(*, valid=True, score=75, verdict="SAFE", liq=50_000.0, has_pair=True) -> TokenScanContext:
    pair = PairSnapshot(pair_address="0xpool", liquidity_usd=liq) if has_pair else None
    return TokenScanContext(
        contract="0x" + "a" * 40,
        valid_address=valid,
        best_pair=pair,
        security_score=score,
        lite_verdict=verdict,
    )


def test_clean_token_passes():
    r = safety_screen(_ctx())
    assert r.passed is True
    assert r.liquidity_usd == 50_000.0
    assert "screené" in r.reasons[0]


def test_low_liquidity_fails():
    r = safety_screen(_ctx(liq=10_000.0))
    assert r.passed is False
    assert any("liquidité" in x for x in r.reasons)


def test_low_score_fails():
    r = safety_screen(_ctx(score=55))
    assert r.passed is False
    assert any("score de sécurité" in x for x in r.reasons)


def test_non_safe_verdict_fails():
    r = safety_screen(_ctx(verdict="CAUTION"))
    assert r.passed is False
    assert any("SAFE requis" in x for x in r.reasons)


def test_danger_fails_with_all_reasons():
    r = safety_screen(_ctx(score=20, verdict="DANGER", liq=400.0))
    assert r.passed is False
    assert len(r.reasons) >= 3  # liquidité + score + verdict


def test_no_pair_fails():
    r = safety_screen(_ctx(has_pair=False))
    assert r.passed is False
    assert any("aucune paire" in x for x in r.reasons)


def test_invalid_address_fails():
    r = safety_screen(_ctx(valid=False))
    assert r.passed is False
    assert any("invalide" in x for x in r.reasons)


def test_threshold_is_tunable():
    # Un token à 25k passe si on abaisse le seuil à 20k.
    assert safety_screen(_ctx(liq=25_000.0)).passed is False
    assert safety_screen(_ctx(liq=25_000.0), min_liquidity_usd=20_000.0).passed is True
