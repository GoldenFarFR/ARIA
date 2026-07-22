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


# ── _resolve_bonding_phase : diligence produit (description/tokenomics/détails,
#    audit 11/07) -- capturée sans coût réseau supplémentaire, même appel ────────


@pytest.mark.asyncio
async def test_resolve_bonding_phase_captures_product_diligence(monkeypatch):
    async def fake_fetch_by_address(address, chain="BASE"):
        return _bonding_token(
            description="Agent IA on-chain pour la gestion de portefeuille",
            tokenomics="15% team, 85% via bonding curve",
            additional_details="Équipe doxxée, roadmap publique",
        )

    monkeypatch.setattr(
        "aria_core.services.virtuals.virtuals_client.fetch_by_address", fake_fetch_by_address,
    )

    ctx = TokenScanContext(contract=ADDR, valid_address=True)
    await scan._resolve_bonding_phase(ctx, ADDR)

    assert ctx.virtuals_description == "Agent IA on-chain pour la gestion de portefeuille"
    assert ctx.virtuals_tokenomics == "15% team, 85% via bonding curve"
    assert ctx.virtuals_additional_details == "Équipe doxxée, roadmap publique"


@pytest.mark.asyncio
async def test_resolve_bonding_phase_product_diligence_absent_stays_none(monkeypatch):
    """Token trouvé sur Virtuals mais sans ces champs -- dégradation douce, jamais
    une valeur inventée : les trois champs restent None."""
    async def fake_fetch_by_address(address, chain="BASE"):
        return _bonding_token(description=None, tokenomics=None, additional_details=None)

    monkeypatch.setattr(
        "aria_core.services.virtuals.virtuals_client.fetch_by_address", fake_fetch_by_address,
    )

    ctx = TokenScanContext(contract=ADDR, valid_address=True)
    await scan._resolve_bonding_phase(ctx, ADDR)

    assert ctx.virtuals_description is None
    assert ctx.virtuals_tokenomics is None
    assert ctx.virtuals_additional_details is None
    assert ctx.bonding_phase is True  # le statut bonding reste détecté normalement


@pytest.mark.asyncio
async def test_resolve_bonding_phase_not_found_leaves_diligence_none(monkeypatch):
    async def fake_fetch_by_address(address, chain="BASE"):
        return None

    monkeypatch.setattr(
        "aria_core.services.virtuals.virtuals_client.fetch_by_address", fake_fetch_by_address,
    )

    ctx = TokenScanContext(contract=ADDR, valid_address=True)
    await scan._resolve_bonding_phase(ctx, ADDR)

    assert ctx.virtuals_description is None
    assert ctx.virtuals_tokenomics is None
    assert ctx.virtuals_additional_details is None


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
async def test_resolve_bonding_phase_sets_token_created_at_ms(monkeypatch):
    """22/07 -- tâche #28 : repli pour insider_wallets, qui n'a autrement AUCUNE
    date de référence en bonding pré-graduation (ctx.best_pair reste None)."""
    async def fake_fetch_by_address(address, chain="BASE"):
        return _bonding_token(created_at="2026-07-06T12:00:00.000Z")

    monkeypatch.setattr(
        "aria_core.services.virtuals.virtuals_client.fetch_by_address", fake_fetch_by_address,
    )

    ctx = TokenScanContext(contract=ADDR, valid_address=True)
    await scan._resolve_bonding_phase(ctx, ADDR)

    assert ctx.token_created_at_ms == 1783339200000


@pytest.mark.asyncio
async def test_resolve_bonding_phase_missing_created_at_stays_none(monkeypatch):
    async def fake_fetch_by_address(address, chain="BASE"):
        return _bonding_token(created_at=None)

    monkeypatch.setattr(
        "aria_core.services.virtuals.virtuals_client.fetch_by_address", fake_fetch_by_address,
    )

    ctx = TokenScanContext(contract=ADDR, valid_address=True)
    await scan._resolve_bonding_phase(ctx, ADDR)

    assert ctx.token_created_at_ms is None


# ── _resolve_bonding_phase : repli on-chain (audit 11/07, gate OFF par défaut) ─────


@pytest.mark.asyncio
async def test_resolve_bonding_phase_onchain_fallback_when_gate_on(monkeypatch):
    """virtual_raised absent (heuristique API renvoie None) + gate ON -> le repli
    on-chain est tenté et son résultat est utilisé."""
    async def fake_fetch_by_address(address, chain="BASE"):
        return _bonding_token(
            virtual_raised=None, pair_address="0xPair", pre_token_address=ADDR,
        )

    monkeypatch.setattr(
        "aria_core.services.virtuals.virtuals_client.fetch_by_address", fake_fetch_by_address,
    )
    monkeypatch.setattr(
        "aria_core.services.base_onchain.onchain_graduation_enabled", lambda: True,
    )
    monkeypatch.setattr(
        "aria_core.services.base_onchain.onchain_graduation_progress",
        lambda **kwargs: 0.73,
    )

    ctx = TokenScanContext(contract=ADDR, valid_address=True)
    await scan._resolve_bonding_phase(ctx, ADDR)

    assert ctx.bonding_phase is True
    assert ctx.bonding_progress == pytest.approx(0.73)


@pytest.mark.asyncio
async def test_resolve_bonding_phase_onchain_fallback_skipped_when_gate_off(monkeypatch):
    """Gate OFF (comportement par défaut) -> le repli on-chain n'est même pas tenté,
    bonding_progress reste None comme avant ce changement."""
    async def fake_fetch_by_address(address, chain="BASE"):
        return _bonding_token(virtual_raised=None, pair_address="0xPair", pre_token_address=ADDR)

    def _should_not_be_called(**kwargs):
        raise AssertionError("onchain_graduation_progress ne doit pas être appelé, gate OFF")

    monkeypatch.setattr(
        "aria_core.services.virtuals.virtuals_client.fetch_by_address", fake_fetch_by_address,
    )
    monkeypatch.setattr(
        "aria_core.services.base_onchain.onchain_graduation_enabled", lambda: False,
    )
    monkeypatch.setattr(
        "aria_core.services.base_onchain.onchain_graduation_progress", _should_not_be_called,
    )

    ctx = TokenScanContext(contract=ADDR, valid_address=True)
    await scan._resolve_bonding_phase(ctx, ADDR)

    assert ctx.bonding_phase is True
    assert ctx.bonding_progress is None


@pytest.mark.asyncio
async def test_resolve_bonding_phase_onchain_fallback_not_tried_when_api_heuristic_has_value(
    monkeypatch,
):
    """virtual_raised présent (heuristique API réussit) -> le repli on-chain n'est jamais
    tenté, même gate ON (évite un eth_call inutile)."""
    async def fake_fetch_by_address(address, chain="BASE"):
        return _bonding_token(pair_address="0xPair", pre_token_address=ADDR)  # virtual_raised=21000 par défaut

    def _should_not_be_called(**kwargs):
        raise AssertionError("onchain_graduation_progress ne doit pas être appelé, heuristique déjà résolue")

    monkeypatch.setattr(
        "aria_core.services.virtuals.virtuals_client.fetch_by_address", fake_fetch_by_address,
    )
    monkeypatch.setattr(
        "aria_core.services.base_onchain.onchain_graduation_enabled", lambda: True,
    )
    monkeypatch.setattr(
        "aria_core.services.base_onchain.onchain_graduation_progress", _should_not_be_called,
    )

    ctx = TokenScanContext(contract=ADDR, valid_address=True)
    await scan._resolve_bonding_phase(ctx, ADDR)

    assert ctx.bonding_progress == pytest.approx(0.5)


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

    async def fake_flags(self, contract):
        return ContractFlags(
            address=contract, is_verified=True, has_mint=False,
            has_blacklist=False, has_disable_transfers=False, available=True, error=None,
        )

    async def fake_holders(self, contract):
        return TokenHoldersResult(holders=[], total_supply=None, available=False, error=None)

    async def fake_fetch_by_address(address, chain="BASE"):
        return _bonding_token()

    monkeypatch.setattr(scan, "_fetch_token_pairs", fake_pairs)
    monkeypatch.setattr(type(scan.blockscout_client), "check_contract_flags", fake_flags)
    monkeypatch.setattr(type(scan.blockscout_client), "get_token_holders", fake_holders)
    monkeypatch.setattr(
        "aria_core.services.virtuals.virtuals_client.fetch_by_address", fake_fetch_by_address,
    )

    ctx = await scan.scan_base_token(ADDR)

    assert ctx.pairs_found == 0
    assert ctx.bonding_phase is True
    assert ctx.lite_verdict != "DANGER"
    assert any("phase de bonding" in f for f in ctx.risk_flags)
