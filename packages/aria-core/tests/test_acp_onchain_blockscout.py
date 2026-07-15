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
