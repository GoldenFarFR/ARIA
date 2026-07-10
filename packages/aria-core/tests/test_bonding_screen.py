"""Filtre de sécurité niche bonding — pendant de safety_screen.py sans exiger de
paire DEX (qui n'existe pas par construction avant graduation)."""
from __future__ import annotations

from aria_core.skills.acp_onchain_scan import TokenScanContext
from aria_core.skills.bonding_screen import bonding_safety_screen


def _healthy_bonding_ctx(**overrides) -> TokenScanContext:
    defaults = dict(
        contract="0x" + "a" * 40,
        valid_address=True,
        bonding_phase=True,
        bonding_progress=0.6,
        security_score=78,
        lite_verdict="SAFE",
        contract_verified=True,
        has_mint=True,
        mint_authority="launchpad",
        mint_authority_detail="déployé par Virtuals Protocol",
        has_blacklist=False,
        has_disable_transfers=False,
        dev_signal="aligned",
    )
    defaults.update(overrides)
    return TokenScanContext(**defaults)


def test_healthy_bonding_candidate_passes():
    result = bonding_safety_screen(_healthy_bonding_ctx())
    assert result.passed is True
    assert result.hard_fail is False
    assert result.reasons == []


def test_invalid_address_hard_fails():
    result = bonding_safety_screen(_healthy_bonding_ctx(valid_address=False))
    assert result.passed is False
    assert result.hard_fail is True


def test_not_bonding_phase_soft_fails():
    result = bonding_safety_screen(_healthy_bonding_ctx(bonding_phase=False))
    assert result.passed is False
    assert result.hard_fail is False  # retry-able, pas un rejet définitif


def test_unverified_contract_hard_fails():
    result = bonding_safety_screen(_healthy_bonding_ctx(contract_verified=False))
    assert result.passed is False
    assert result.hard_fail is True


def test_unknown_contract_verification_soft_fails():
    result = bonding_safety_screen(_healthy_bonding_ctx(contract_verified=None))
    assert result.passed is False
    assert result.hard_fail is False


def test_mint_dev_controlled_hard_fails():
    result = bonding_safety_screen(
        _healthy_bonding_ctx(mint_authority="eoa", mint_authority_detail="owner = wallet externe")
    )
    assert result.passed is False
    assert result.hard_fail is True
    assert any("mint" in r for r in result.reasons)


def test_mint_authority_unknown_soft_fails():
    result = bonding_safety_screen(_healthy_bonding_ctx(mint_authority="unknown"))
    assert result.passed is False
    assert result.hard_fail is False


def test_mint_renounced_ok():
    result = bonding_safety_screen(_healthy_bonding_ctx(mint_authority="renounced"))
    assert result.passed is True


def test_no_mint_at_all_ok():
    result = bonding_safety_screen(_healthy_bonding_ctx(has_mint=False, mint_authority=None))
    assert result.passed is True


def test_blacklist_hard_fails():
    result = bonding_safety_screen(_healthy_bonding_ctx(has_blacklist=True))
    assert result.passed is False
    assert result.hard_fail is True


def test_disable_transfers_hard_fails():
    result = bonding_safety_screen(_healthy_bonding_ctx(has_disable_transfers=True))
    assert result.passed is False
    assert result.hard_fail is True


def test_danger_verdict_hard_fails():
    result = bonding_safety_screen(_healthy_bonding_ctx(lite_verdict="DANGER", security_score=20))
    assert result.passed is False
    assert result.hard_fail is True


def test_caution_verdict_soft_fails():
    result = bonding_safety_screen(_healthy_bonding_ctx(lite_verdict="CAUTION", security_score=50))
    assert result.passed is False
    assert result.hard_fail is False


def test_dev_signal_concern_hard_fails():
    result = bonding_safety_screen(_healthy_bonding_ctx(dev_signal="concern"))
    assert result.passed is False
    assert result.hard_fail is True


def test_dev_signal_unresolved_soft_fails():
    result = bonding_safety_screen(_healthy_bonding_ctx(dev_signal=None))
    assert result.passed is False
    assert result.hard_fail is False


def test_dev_signal_neutral_ok():
    result = bonding_safety_screen(_healthy_bonding_ctx(dev_signal="neutral"))
    assert result.passed is True


def test_result_exposes_bonding_progress_and_score():
    result = bonding_safety_screen(_healthy_bonding_ctx(bonding_progress=0.42, security_score=81))
    assert result.bonding_progress == 0.42
    assert result.security_score == 81
    assert result.verdict == "SAFE"
