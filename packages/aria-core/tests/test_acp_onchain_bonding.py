"""Niche Virtuals bonding-phase (tâche #10) — un token encore en courbe de bonding n'a
PAS de paire DexScreener par conception (pas de pool DEX avant graduation). Avant ce
correctif, `_score_and_verdict` traitait ça comme un défaut de sécurité générique
("Aucune paire DexScreener trouvée") et pouvait produire un DANGER/CAUTION mal fondé.
Vérifie : le comportement existant (non-bonding) est inchangé, la détection bonding
est best-effort (jamais bloquante), et le verdict bonding utilise des signaux réels
(progression, holders) — jamais une confiance inventée.
"""
from __future__ import annotations

import pytest

from aria_core.services.virtuals import VirtualToken
from aria_core.skills import acp_onchain_scan as scan
from aria_core.skills.acp_onchain_scan import TokenScanContext

ADDR = "0x" + "a" * 40


def _bonding_token(**overrides) -> VirtualToken:
    defaults = dict(
        name="Aria Agent",
        symbol="ARIA",
        status="UNDERGRAD",
        chain="BASE",
        token_address=ADDR,
        holder_count=120,
        virtual_raised=21_000.0,  # 50% de GRADUATION_THRESHOLD_VIRTUAL (42 000)
        mcap=12_345.0,
    )
    defaults.update(overrides)
    return VirtualToken(**defaults)


# ── _score_and_verdict : comportement existant inchangé (régression) ──────────────────

def test_no_pair_no_bonding_keeps_existing_generic_verdict():
    """Sans détection bonding (comportement par défaut), le message générique existant
    ("Aucune paire DexScreener") reste inchangé — garde-fou de non-régression."""
    ctx = TokenScanContext(contract=ADDR, valid_address=True)
    assert ctx.bonding_phase is False  # défaut

    scan._score_and_verdict(ctx, None)

    assert any("Aucune paire DexScreener trouvée" in f for f in ctx.risk_flags)
    assert not any("bonding" in f.lower() for f in ctx.risk_flags)


# ── _score_and_verdict : branche bonding ────────────────────────────────────────────

def test_bonding_phase_does_not_default_to_danger():
    ctx = TokenScanContext(contract=ADDR, valid_address=True)
    ctx.bonding_phase = True
    ctx.bonding_progress = 0.5
    ctx.bonding_holder_count = 120

    scan._score_and_verdict(ctx, None)

    assert ctx.lite_verdict != "DANGER"
    assert any("phase de bonding" in f for f in ctx.risk_flags)
    assert any("50%" in f for f in ctx.risk_flags)
    assert not any("Aucune paire DexScreener trouvée sur Base — liquidité non vérifiable" in f for f in ctx.risk_flags)


def test_bonding_phase_missing_progress_is_labeled_not_invented():
    ctx = TokenScanContext(contract=ADDR, valid_address=True)
    ctx.bonding_phase = True
    ctx.bonding_progress = None  # API n'exposait pas virtualRaised

    scan._score_and_verdict(ctx, None)

    assert any("progression vers la graduation non disponible" in f for f in ctx.risk_flags)


def test_bonding_phase_high_progress_and_holders_scores_higher_than_low():
    ctx_low = TokenScanContext(contract=ADDR, valid_address=True)
    ctx_low.bonding_phase = True
    ctx_low.bonding_progress = 0.05
    ctx_low.bonding_holder_count = 3
    scan._score_and_verdict(ctx_low, None)

    ctx_high = TokenScanContext(contract=ADDR, valid_address=True)
    ctx_high.bonding_phase = True
    ctx_high.bonding_progress = 0.9
    ctx_high.bonding_holder_count = 500
    scan._score_and_verdict(ctx_high, None)

    assert ctx_high.security_score > ctx_low.security_score


# ── _resolve_bonding_phase : best-effort, jamais bloquant ──────────────────────────

@pytest.mark.asyncio
async def test_resolve_bonding_phase_sets_fields_when_in_bonding(monkeypatch):
    async def fake_fetch_by_address(address, chain="BASE"):
        assert address == ADDR
        return _bonding_token()

    monkeypatch.setattr(
        "aria_core.services.virtuals.virtuals_client.fetch_by_address", fake_fetch_by_address,
    )

    ctx = TokenScanContext(contract=ADDR, valid_address=True)
    await scan._resolve_bonding_phase(ctx, ADDR)

    assert ctx.bonding_phase is True
    assert ctx.bonding_progress == pytest.approx(0.5)
    assert ctx.bonding_holder_count == 120


@pytest.mark.asyncio
async def test_resolve_bonding_phase_graduated_token_stays_false(monkeypatch):
    """Un token DÉJÀ gradué (AVAILABLE) sans paire DexScreener est un cas réellement
    suspect (devrait avoir une paire) — bonding_phase doit rester False, pas de faux SAFE."""
    async def fake_fetch_by_address(address, chain="BASE"):
        return _bonding_token(status="AVAILABLE", raw_status="AVAILABLE")

    monkeypatch.setattr(
        "aria_core.services.virtuals.virtuals_client.fetch_by_address", fake_fetch_by_address,
    )

    ctx = TokenScanContext(contract=ADDR, valid_address=True)
    await scan._resolve_bonding_phase(ctx, ADDR)

    assert ctx.bonding_phase is False


@pytest.mark.asyncio
async def test_resolve_bonding_phase_not_found_stays_false(monkeypatch):
    async def fake_fetch_by_address(address, chain="BASE"):
        return None

    monkeypatch.setattr(
        "aria_core.services.virtuals.virtuals_client.fetch_by_address", fake_fetch_by_address,
    )

    ctx = TokenScanContext(contract=ADDR, valid_address=True)
    await scan._resolve_bonding_phase(ctx, ADDR)

    assert ctx.bonding_phase is False


@pytest.mark.asyncio
async def test_resolve_bonding_phase_network_failure_never_raises(monkeypatch):
    async def fake_fetch_by_address(address, chain="BASE"):
        raise RuntimeError("Virtuals API indisponible")

    monkeypatch.setattr(
        "aria_core.services.virtuals.virtuals_client.fetch_by_address", fake_fetch_by_address,
    )

    ctx = TokenScanContext(contract=ADDR, valid_address=True)
    await scan._resolve_bonding_phase(ctx, ADDR)  # ne doit jamais lever

    assert ctx.bonding_phase is False


# ── scan_base_token : câblage bout-en-bout ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_scan_base_token_uses_bonding_data_when_no_dex_pair(monkeypatch):
    from aria_core.services.blockscout import ContractFlags, TokenHoldersResult

    async def fake_pairs(contract):
        return []

    async def fake_flags(contract):
        return ContractFlags(
            address=contract, is_verified=True, has_mint=False,
            has_blacklist=False, has_disable_transfers=False, available=True, error=None,
        )

    async def fake_holders(contract):
        return TokenHoldersResult(holders=[], total_supply=None, available=False, error=None)

    async def fake_fetch_by_address(address, chain="BASE"):
        return _bonding_token()

    monkeypatch.setattr(scan, "_fetch_token_pairs", fake_pairs)
    monkeypatch.setattr(scan.blockscout_client, "check_contract_flags", fake_flags)
    monkeypatch.setattr(scan.blockscout_client, "get_token_holders", fake_holders)
    monkeypatch.setattr(
        "aria_core.services.virtuals.virtuals_client.fetch_by_address", fake_fetch_by_address,
    )

    ctx = await scan.scan_base_token(ADDR)

    assert ctx.pairs_found == 0
    assert ctx.bonding_phase is True
    assert ctx.lite_verdict != "DANGER"
    assert any("phase de bonding" in f for f in ctx.risk_flags)
