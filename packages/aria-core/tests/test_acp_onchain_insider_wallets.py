"""Câblage du signal 'sortie de liquidité déguisée' dans scan_base_token
(_resolve_insider_wallets, include_insider_check) -- purement additif, jamais
bloquant, réutilise les holders déjà récupérés par le scan (aucun re-fetch)."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from aria_core.services.blockscout import AddressInfo, TokenHoldersResult
from aria_core.skills import acp_onchain_scan as scan
from aria_core.skills import insider_wallets
from aria_core.skills.acp_onchain_scan import PairSnapshot, TokenScanContext
from aria_core.skills.insider_wallets import InsiderWalletFacts, InsiderWalletVerdict

ADDR = "0x" + "a" * 40
DEV = "0x" + "d" * 40


def _pair(created_at=1782547200000) -> PairSnapshot:
    return PairSnapshot(pair_address="0xpair", liquidity_usd=20_000, base_address=ADDR, pair_created_at=created_at)


@pytest.mark.asyncio
async def test_resolve_insider_wallets_no_creator_is_unknown(monkeypatch):
    ctx = TokenScanContext(contract=ADDR, valid_address=True)
    ctx.best_pair = _pair()
    monkeypatch.setattr(
        type(scan.blockscout_client), "get_address_info",
        AsyncMock(return_value=AddressInfo(address=ADDR, available=False)),
    )

    await scan._resolve_insider_wallets(ctx, ADDR, None)

    assert ctx.insider_signal == "unknown"


@pytest.mark.asyncio
async def test_resolve_insider_wallets_no_pair_created_at_is_unknown(monkeypatch):
    ctx = TokenScanContext(contract=ADDR, valid_address=True)
    ctx.best_pair = _pair(created_at=None)
    monkeypatch.setattr(
        type(scan.blockscout_client), "get_address_info",
        AsyncMock(return_value=AddressInfo(address=ADDR, creator_address=DEV, available=True)),
    )

    await scan._resolve_insider_wallets(ctx, ADDR, None)

    assert ctx.insider_signal == "unknown"


@pytest.mark.asyncio
async def test_resolve_insider_wallets_wires_verdict_onto_context(monkeypatch):
    ctx = TokenScanContext(contract=ADDR, valid_address=True)
    ctx.best_pair = _pair()
    monkeypatch.setattr(
        type(scan.blockscout_client), "get_address_info",
        AsyncMock(return_value=AddressInfo(address=ADDR, creator_address=DEV, available=True)),
    )
    fake_facts = InsiderWalletFacts(examined=2, flagged=["0x" + "1" * 40], available=True)
    monkeypatch.setattr(
        insider_wallets, "gather_insider_wallet_facts", AsyncMock(return_value=fake_facts),
    )
    monkeypatch.setattr(
        insider_wallets, "judge_insider_wallets",
        lambda facts: InsiderWalletVerdict(signal="concern", points=["1/2 wallet(s) suspect(s)"]),
    )

    holders = TokenHoldersResult(available=True, holders=[])
    await scan._resolve_insider_wallets(ctx, ADDR, holders)

    assert ctx.insider_signal == "concern"
    assert ctx.insider_points == ["1/2 wallet(s) suspect(s)"]


@pytest.mark.asyncio
async def test_resolve_insider_wallets_exception_is_unknown_not_raised(monkeypatch):
    ctx = TokenScanContext(contract=ADDR, valid_address=True)
    ctx.best_pair = _pair()
    monkeypatch.setattr(
        type(scan.blockscout_client), "get_address_info", AsyncMock(side_effect=RuntimeError("panne réseau")),
    )

    await scan._resolve_insider_wallets(ctx, ADDR, None)  # ne doit jamais lever

    assert ctx.insider_signal == "unknown"


@pytest.mark.asyncio
async def test_scan_base_token_include_insider_check_off_by_default(monkeypatch):
    """Sans include_insider_check=True, aucun appel Dune -- comportement inchangé."""
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
    monkeypatch.setattr(scan, "_resolve_insider_wallets", resolve_mock)

    await scan.scan_base_token(ADDR)

    resolve_mock.assert_not_called()
