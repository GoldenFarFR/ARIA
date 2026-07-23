"""Câblage du signal 'cluster Sybil' dans scan_base_token (_resolve_sybil_cluster,
include_sybil_check) -- purement additif, jamais bloquant, off par défaut
(coût réseau plus élevé que les autres signaux consultatifs)."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from aria_core.services.blockscout import TokenHolder, TokenHoldersResult
from aria_core.skills import acp_onchain_scan as scan
from aria_core.skills import sybil_cluster
from aria_core.skills.acp_onchain_scan import PairSnapshot, TokenScanContext
from aria_core.skills.sybil_cluster import SybilClusterFacts, SybilClusterVerdict

ADDR = "0x" + "a" * 40
LP = "0x" + "9" * 40


def _pair() -> PairSnapshot:
    return PairSnapshot(pair_address=LP, liquidity_usd=20_000, base_address=ADDR)


@pytest.mark.asyncio
async def test_resolve_sybil_cluster_no_holders_is_unknown():
    ctx = TokenScanContext(contract=ADDR, valid_address=True)
    ctx.best_pair = _pair()

    await scan._resolve_sybil_cluster(ctx, TokenHoldersResult(available=False, error="skip"))

    assert ctx.sybil_cluster_signal == "unknown"


@pytest.mark.asyncio
async def test_resolve_sybil_cluster_wires_verdict_onto_context(monkeypatch):
    ctx = TokenScanContext(contract=ADDR, valid_address=True)
    ctx.best_pair = _pair()
    holders = TokenHoldersResult(available=True, holders=[TokenHolder(address="0x" + "1" * 40, balance=1.0, percentage=5.0)])
    fake_facts = SybilClusterFacts(holders_checked=5, largest_cluster_size=5, largest_cluster_cumulative_pct=30.0, available=True)
    monkeypatch.setattr(sybil_cluster, "gather_sybil_cluster_facts", AsyncMock(return_value=fake_facts))
    monkeypatch.setattr(
        sybil_cluster, "judge_sybil_cluster",
        lambda facts: SybilClusterVerdict(signal="concern", points=["cluster suspecté"]),
    )

    await scan._resolve_sybil_cluster(ctx, holders)

    assert ctx.sybil_cluster_signal == "concern"
    assert ctx.sybil_cluster_points == ["cluster suspecté"]


@pytest.mark.asyncio
async def test_resolve_sybil_cluster_excludes_lp_address(monkeypatch):
    ctx = TokenScanContext(contract=ADDR, valid_address=True)
    ctx.best_pair = _pair()
    holders = TokenHoldersResult(available=True, holders=[TokenHolder(address=LP, balance=1.0, percentage=90.0)])
    gather_mock = AsyncMock(return_value=SybilClusterFacts(available=True))
    monkeypatch.setattr(sybil_cluster, "gather_sybil_cluster_facts", gather_mock)

    await scan._resolve_sybil_cluster(ctx, holders)

    _, kwargs = gather_mock.await_args
    assert LP.lower() in kwargs["exclude_addresses"]


@pytest.mark.asyncio
async def test_resolve_sybil_cluster_exception_is_unknown_not_raised(monkeypatch):
    ctx = TokenScanContext(contract=ADDR, valid_address=True)
    ctx.best_pair = _pair()
    holders = TokenHoldersResult(available=True, holders=[TokenHolder(address="0x" + "1" * 40, balance=1.0, percentage=5.0)])
    monkeypatch.setattr(sybil_cluster, "gather_sybil_cluster_facts", AsyncMock(side_effect=RuntimeError("panne")))

    await scan._resolve_sybil_cluster(ctx, holders)  # ne doit jamais lever

    assert ctx.sybil_cluster_signal == "unknown"


@pytest.mark.asyncio
async def test_scan_base_token_include_sybil_check_off_by_default(monkeypatch):
    """Sans include_sybil_check=True, aucun appel -- comportement inchangé (coût
    réseau non négligeable, jamais consommé implicitement)."""
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
    monkeypatch.setattr(scan, "_resolve_sybil_cluster", resolve_mock)

    await scan.scan_base_token(ADDR)

    resolve_mock.assert_not_called()
