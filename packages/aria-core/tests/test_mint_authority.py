"""Autorité du mint + neutralisation du filtre selon le contexte (launchpad/renoncé/contrat)."""
from __future__ import annotations

from aria_core.skills.acp_onchain_scan import TokenScanContext, _is_burn_address
from aria_core.skills.mint_authority import (
    classify_authority,
    match_launchpad,
)
from aria_core.skills.safety_screen import _mint_is_dev_controlled, mint_blocks_confirmed

DEAD = "0x000000000000000000000000000000000000dEaD"
ZERO = "0x0000000000000000000000000000000000000000"
VIRTUALS = "0xf66dea7b3e897cd44a5a231c61b6b4423d613259"
EOA = "0x1111111111111111111111111111111111111111"
CONTRACT_OWNER = "0x2222222222222222222222222222222222222222"


# ── Détection burn (motif zéros + dead) ─────────────────────────────────────

def test_is_burn_address_patterns():
    assert _is_burn_address(ZERO)
    assert _is_burn_address(DEAD)
    assert _is_burn_address("0x" + "0" * 36 + "dead")
    assert _is_burn_address("0xdead000000000000000042069420694206942069")
    # un vrai wallet qui se TERMINE par dead mais avec un corps non nul n'est pas un burn
    assert not _is_burn_address("0x123456789012345678901234567890123456dead")
    assert not _is_burn_address(EOA)
    assert not _is_burn_address(None)


# ── Classification d'autorité ───────────────────────────────────────────────

def test_no_mint_is_na():
    v = classify_authority(has_mint=False)
    assert v.kind == "na"
    assert v.mint_neutralized is True  # rien à neutraliser


def test_launchpad_recognized_neutralizes():
    v = classify_authority(has_mint=True, creator_address=VIRTUALS)
    assert v.kind == "launchpad"
    assert v.launchpad == "Virtuals Protocol"
    assert v.mint_neutralized is True


def test_renounced_owner_is_safe():
    v = classify_authority(has_mint=True, owner_address=DEAD)
    assert v.kind == "renounced"
    assert v.mint_neutralized is True


def test_contract_owner_is_locked_ok():
    v = classify_authority(has_mint=True, owner_address=CONTRACT_OWNER, owner_is_contract=True)
    assert v.kind == "contract"
    assert v.mint_neutralized is True


def test_eoa_owner_is_dev_controlled_danger():
    v = classify_authority(has_mint=True, owner_address=EOA, owner_is_contract=False)
    assert v.kind == "eoa"
    assert v.mint_neutralized is False


def test_unknown_authority_fails_closed():
    v = classify_authority(has_mint=True)  # ni créateur, ni owner
    assert v.kind == "unknown"
    assert v.mint_neutralized is False


def test_match_launchpad_unknown_returns_none():
    assert match_launchpad(EOA) is None
    assert match_launchpad(None) is None


# ── Intégration filtre : le mint ne bloque QUE si un dev contrôle ────────────

def _ctx(**kw) -> TokenScanContext:
    ctx = TokenScanContext(contract="0x" + "a" * 40, valid_address=True)
    for k, val in kw.items():
        setattr(ctx, k, val)
    return ctx


def test_screen_mint_neutralized_by_launchpad():
    ctx = _ctx(has_mint=True, mint_authority="launchpad")
    assert _mint_is_dev_controlled(ctx) is False
    assert mint_blocks_confirmed(ctx) is False


def test_screen_mint_neutralized_by_renounce_and_contract():
    for auth in ("renounced", "contract"):
        ctx = _ctx(has_mint=True, mint_authority=auth)
        assert _mint_is_dev_controlled(ctx) is False


def test_screen_mint_blocks_when_eoa():
    ctx = _ctx(has_mint=True, mint_authority="eoa")
    assert _mint_is_dev_controlled(ctx) is True
    assert mint_blocks_confirmed(ctx) is True  # confirmé -> échec DUR


def test_screen_mint_unknown_blocks_but_soft():
    ctx = _ctx(has_mint=True, mint_authority="unknown")
    assert _mint_is_dev_controlled(ctx) is True   # bloque (fail-closed)
    assert mint_blocks_confirmed(ctx) is False     # mais pas un échec DUR (à réessayer)


def test_screen_no_mint_never_blocks():
    ctx = _ctx(has_mint=False)
    assert _mint_is_dev_controlled(ctx) is False
