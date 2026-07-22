"""Câblage du signal 'réputation du déployeur' dans scan_base_token
(_resolve_deployer_reputation, include_deployer_reputation) -- purement additif,
jamais bloquant."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from aria_core.services.blockscout import AddressInfo, TokenHoldersResult
from aria_core.services import deployer_history
from aria_core.skills import acp_onchain_scan as scan
from aria_core.skills.acp_onchain_scan import PairSnapshot, TokenScanContext
from aria_core.services.deployer_history import DeployerHistoryFacts, DeployerHistoryVerdict

ADDR = "0x" + "a" * 40
DEV = "0x" + "d" * 40


def _pair() -> PairSnapshot:
    return PairSnapshot(pair_address="0xpair", liquidity_usd=20_000, base_address=ADDR)


@pytest.mark.asyncio
async def test_resolve_deployer_reputation_no_creator_is_unknown(monkeypatch):
    ctx = TokenScanContext(contract=ADDR, valid_address=True)
    ctx.best_pair = _pair()
    monkeypatch.setattr(
        type(scan.blockscout_client), "get_address_info",
        AsyncMock(return_value=AddressInfo(address=ADDR, available=False)),
    )

    await scan._resolve_deployer_reputation(ctx, ADDR)

    assert ctx.deployer_reputation_signal == "unknown"


@pytest.mark.asyncio
async def test_resolve_deployer_reputation_wires_verdict_onto_context(monkeypatch):
    ctx = TokenScanContext(contract=ADDR, valid_address=True)
    ctx.best_pair = _pair()
    monkeypatch.setattr(
        type(scan.blockscout_client), "get_address_info",
        AsyncMock(return_value=AddressInfo(address=ADDR, creator_address=DEV, available=True)),
    )
    fake_facts = DeployerHistoryFacts(prior_contracts_found=2, known_rugs=["0x" + "1" * 40], available=True)
    monkeypatch.setattr(deployer_history, "gather_deployer_history_facts", AsyncMock(return_value=fake_facts))
    monkeypatch.setattr(
        deployer_history, "judge_deployer_history",
        lambda facts: DeployerHistoryVerdict(signal="concern", points=["récidiviste confirmé"]),
    )

    await scan._resolve_deployer_reputation(ctx, ADDR)

    assert ctx.deployer_reputation_signal == "concern"
    assert ctx.deployer_reputation_points == ["récidiviste confirmé"]


@pytest.mark.asyncio
async def test_resolve_deployer_reputation_exception_is_unknown_not_raised(monkeypatch):
    ctx = TokenScanContext(contract=ADDR, valid_address=True)
    ctx.best_pair = _pair()
    monkeypatch.setattr(
        type(scan.blockscout_client), "get_address_info", AsyncMock(side_effect=RuntimeError("panne réseau")),
    )

    await scan._resolve_deployer_reputation(ctx, ADDR)  # ne doit jamais lever

    assert ctx.deployer_reputation_signal == "unknown"


@pytest.mark.asyncio
async def test_scan_base_token_include_deployer_reputation_off_by_default(monkeypatch):
    """Sans include_deployer_reputation=True, aucun appel -- comportement inchangé."""
    monkeypatch.setattr(scan, "_fetch_token_pairs", AsyncMock(return_value=[_pair()]))
    monkeypatch.setattr(
        type(scan.blockscout_client), "check_contract_flags",
        AsyncMock(return_value=scan.ContractFlags(address=ADDR, available=False, error="skip")),
    )
    monkeypatch.setattr(
        type(scan.blockscout_client), "get_token_holders",
        AsyncMock(return_value=TokenHoldersResult(available=False, error="skip")),
    )
    resolve_mock = AsyncMock()
    monkeypatch.setattr(scan, "_resolve_deployer_reputation", resolve_mock)

    await scan.scan_base_token(ADDR)

    resolve_mock.assert_not_called()
