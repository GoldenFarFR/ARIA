"""Intégration Blockscout dans le scoring on-chain ACP — purement additive.

Vérifie que les signaux Blockscout (contrat vérifié/mint/blacklist/disable
transfers, concentration whale) s'ajoutent aux signaux DexScreener existants
sans les remplacer, et que l'indisponibilité d'une donnée on-chain ne
dégrade jamais le score (cf. AGENTS.md — jamais de supposition).
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from aria_core.services.blockscout import ContractFlags, TokenHolder, TokenHoldersResult
from aria_core.skills import acp_onchain_scan as scan
from aria_core.skills.acp_onchain_scan import PairSnapshot, TokenScanContext

ADDR = "0x" + "a" * 40


def _pair() -> PairSnapshot:
    return PairSnapshot(
        pair_address="0xpair",
        dex_id="aerodrome",
        liquidity_usd=20_000,
        volume_24h_usd=5_000,
        buys_24h=50,
        sells_24h=30,
        base_symbol="TOK",
        quote_symbol="WETH",
        base_address=ADDR,
    )


def _baseline_score() -> int:
    ctx = TokenScanContext(contract=ADDR, valid_address=True)
    scan._score_and_verdict(ctx, _pair())
    return ctx.security_score


def test_baseline_unaffected_when_no_onchain_data():
    ctx = TokenScanContext(contract=ADDR, valid_address=True)
    scan._score_and_verdict(ctx, _pair())
    assert ctx.security_score == _baseline_score()
    assert not any("Blockscout" in f for f in ctx.risk_flags)


def test_mint_detected_adds_flag_and_degrades_score():
    ctx = TokenScanContext(contract=ADDR, valid_address=True)
    contract_flags = ContractFlags(
        address=ADDR,
        is_verified=True,
        has_mint=True,
        has_blacklist=False,
        has_disable_transfers=False,
        available=True,
        error=None,
    )

    scan._score_and_verdict(ctx, _pair(), contract_flags=contract_flags)

    assert ctx.security_score == _baseline_score() - 30
    assert any("mint" in f.lower() for f in ctx.risk_flags)


@pytest.mark.parametrize("authority", ["renounced", "launchpad", "contract"])
def test_mint_penalty_neutralized_by_safe_authority(authority):
    """#164 (corrigé 22/07) : un mint renoncé/launchpad/contrat (timelock/multisig)
    ne doit plus subir le malus -30 -- le crible dur (safety_screen) neutralisait déjà
    ce cas via mint_authority, mais le score composite l'ignorait avant ce correctif."""
    ctx = TokenScanContext(contract=ADDR, valid_address=True)
    ctx.mint_authority = authority
    contract_flags = ContractFlags(
        address=ADDR,
        is_verified=True,
        has_mint=True,
        has_blacklist=False,
        has_disable_transfers=False,
        available=True,
        error=None,
    )

    scan._score_and_verdict(ctx, _pair(), contract_flags=contract_flags)

    assert ctx.security_score == _baseline_score()
    assert any("neutralisée" in f.lower() for f in ctx.risk_flags)


@pytest.mark.parametrize("authority", [None, "eoa", "unknown"])
def test_mint_penalty_still_applies_for_unsafe_or_unresolved_authority(authority):
    """Fail-closed : une autorité EOA (dev), inconnue, ou jamais résolue conserve le
    malus -30 -- seule une autorité VÉRIFIÉE sûre (SAFE_AUTHORITIES) le neutralise."""
    ctx = TokenScanContext(contract=ADDR, valid_address=True)
    ctx.mint_authority = authority
    contract_flags = ContractFlags(
        address=ADDR,
        is_verified=True,
        has_mint=True,
        has_blacklist=False,
        has_disable_transfers=False,
        available=True,
        error=None,
    )

    scan._score_and_verdict(ctx, _pair(), contract_flags=contract_flags)

    assert ctx.security_score == _baseline_score() - 30


@pytest.mark.asyncio
async def test_scan_base_token_resolves_mint_authority_before_scoring(monkeypatch):
    """Intégration bout-en-bout : un mint renoncé (owner = adresse morte) ne doit
    plus faire tomber security_score/lite_verdict -- verrouille l'ORDRE d'appel
    (mint_authority doit être résolu AVANT _score_and_verdict, pas après), le vrai
    bug derrière #164 (une simple relecture de la formule seule ne l'aurait pas
    révélé, seul un scan complet le peut)."""
    from aria_core.services.blockscout import AddressInfo

    pair = _pair()
    monkeypatch.setattr(scan, "_fetch_token_pairs", AsyncMock(return_value=[pair]))
    monkeypatch.setattr(
        type(scan.blockscout_client),
        "check_contract_flags",
        AsyncMock(return_value=ContractFlags(
            address=ADDR, is_verified=True, has_mint=True,
            has_blacklist=False, has_disable_transfers=False, available=True,
        )),
    )
    monkeypatch.setattr(
        type(scan.blockscout_client), "get_token_holders", AsyncMock(return_value=TokenHoldersResult())
    )
    monkeypatch.setattr(
        type(scan.blockscout_client),
        "get_address_info",
        AsyncMock(return_value=AddressInfo(address=ADDR, available=True, creator_address=None)),
    )
    dead = "0x000000000000000000000000000000000000dead"
    monkeypatch.setattr(
        type(scan.blockscout_client), "read_owner", AsyncMock(return_value=(dead, None))
    )

    ctx = await scan.scan_base_token(ADDR)

    assert ctx.mint_authority == "renounced"
    assert ctx.security_score == _baseline_score()
    assert not any("supply potentiellement inflatable" in f for f in ctx.risk_flags)


def test_blacklist_and_disable_transfers_each_degrade_score():
    ctx = TokenScanContext(contract=ADDR, valid_address=True)
    contract_flags = ContractFlags(
        address=ADDR,
        is_verified=True,
        has_mint=False,
        has_blacklist=True,
        has_disable_transfers=True,
        available=True,
        error=None,
    )

    scan._score_and_verdict(ctx, _pair(), contract_flags=contract_flags)

    assert ctx.security_score == max(5, _baseline_score() - 60)
    assert any("blacklist" in f.lower() for f in ctx.risk_flags)
    assert any("transferts" in f.lower() for f in ctx.risk_flags)


def test_unverified_contract_is_distinct_flag_no_score_penalty():
    ctx = TokenScanContext(contract=ADDR, valid_address=True)
    contract_flags = ContractFlags(
        address=ADDR,
        is_verified=False,
        available=True,
        error="contrat non vérifié — scan des fonctions sensibles impossible",
    )

    scan._score_and_verdict(ctx, _pair(), contract_flags=contract_flags)

    assert ctx.security_score == _baseline_score()
    assert any("non vérifié" in f for f in ctx.risk_flags)
    assert not any("indisponible" in f for f in ctx.risk_flags)


def test_onchain_data_unavailable_does_not_degrade_score():
    ctx = TokenScanContext(contract=ADDR, valid_address=True)
    contract_flags = ContractFlags(
        address=ADDR,
        available=False,
        error="donnée on-chain indisponible (timeout Blockscout)",
    )
    holders = TokenHoldersResult(available=False, error="donnée on-chain indisponible (rate limit Blockscout)")

    scan._score_and_verdict(ctx, _pair(), contract_flags=contract_flags, holders=holders)

    assert ctx.security_score == _baseline_score()
    assert any("indisponible" in f for f in ctx.risk_flags)


def test_whale_concentration_flag_and_penalty():
    ctx = TokenScanContext(contract=ADDR, valid_address=True)
    holders = TokenHoldersResult(
        holders=[TokenHolder(address="0xwhale", balance=600, percentage=60.0)],
        total_supply=1000,
        available=True,
        error=None,
    )

    scan._score_and_verdict(ctx, _pair(), holders=holders)

    assert ctx.security_score == _baseline_score() - 20
    assert any("whale" in f.lower() for f in ctx.risk_flags)


def test_whale_check_excludes_known_liquidity_pair_address():
    ctx = TokenScanContext(contract=ADDR, valid_address=True)
    pair = _pair()
    holders = TokenHoldersResult(
        holders=[TokenHolder(address=pair.pair_address, balance=600, percentage=60.0)],
        total_supply=1000,
        available=True,
        error=None,
    )

    scan._score_and_verdict(ctx, pair, holders=holders)

    assert ctx.security_score == _baseline_score()
    assert not any("whale" in f.lower() for f in ctx.risk_flags)


def test_no_pair_branch_still_applies_onchain_flags():
    ctx = TokenScanContext(contract=ADDR, valid_address=True)
    contract_flags = ContractFlags(
        address=ADDR,
        is_verified=True,
        has_mint=True,
        available=True,
        error=None,
    )

    scan._score_and_verdict(ctx, None, contract_flags=contract_flags)

    assert ctx.security_score == 5  # max(5, 35 - 30)
    assert any("mint" in f.lower() for f in ctx.risk_flags)
    assert ctx.lite_verdict == "DANGER"


@pytest.mark.asyncio
async def test_scan_base_token_wires_blockscout_calls(monkeypatch):
    monkeypatch.setattr(scan, "_fetch_token_pairs", AsyncMock(return_value=[_pair()]))
    monkeypatch.setattr(
        type(scan.blockscout_client),
        "check_contract_flags",
        AsyncMock(
            return_value=ContractFlags(
                address=ADDR, is_verified=True, has_mint=True, available=True, error=None
            )
        ),
    )
    monkeypatch.setattr(
        type(scan.blockscout_client),
        "get_token_holders",
        AsyncMock(return_value=TokenHoldersResult(available=True, error=None)),
    )

    ctx = await scan.scan_base_token(ADDR)

    assert ctx.security_score == _baseline_score() - 30
    assert any("mint" in f.lower() for f in ctx.risk_flags)


@pytest.mark.asyncio
async def test_scan_base_token_ignores_pair_where_contract_is_only_the_quote_token(monkeypatch):
    """19/07 -- même correctif que momentum_entry._best_pair/paper_trader.
    _default_pair_lookup (reproduction de l'incident réel PLAZM #21, en fait
    ESHARE) : /vc ne doit JAMAIS analyser/publier le prix d'un token différent
    de celui réellement scanné, même si ``ca`` apparaît comme quote d'un pool
    plus liquide appartenant à un autre token de base."""
    other_token_as_base = PairSnapshot(
        pair_address="other_pool", liquidity_usd=999_999.0, price_usd=0.01759,
        base_address="0x" + "b" * 40, base_symbol="OTHER",
    )
    own_pair = PairSnapshot(
        pair_address="own_pool", liquidity_usd=100.0, price_usd=5.84,
        base_address=ADDR, base_symbol="TOK",
    )
    monkeypatch.setattr(
        scan, "_fetch_token_pairs", AsyncMock(return_value=[other_token_as_base, own_pair])
    )
    monkeypatch.setattr(
        type(scan.blockscout_client), "check_contract_flags",
        AsyncMock(return_value=ContractFlags(address=ADDR, available=False, error="skip")),
    )
    monkeypatch.setattr(
        type(scan.blockscout_client), "get_token_holders",
        AsyncMock(return_value=TokenHoldersResult(available=False, error="skip")),
    )

    ctx = await scan.scan_base_token(ADDR)

    assert ctx.best_pair.pair_address == "own_pool"
    assert ctx.best_pair.price_usd == 5.84
    assert ctx.pairs_found == 1
