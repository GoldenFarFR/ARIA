"""_resolve_dev_behavior -- 11/08, real duplicate-call gap found (Explore
audit while scoping backlog #93): ``holders`` (a ``TokenHoldersResult``
already fetched once by ``scan_base_token``) was never threaded through to
``gather_dev_wallet_facts``, which silently refetched ``get_token_holders``
for the SAME contract a second time within the SAME VC evaluation --
``_resolve_insider_wallets`` right below it already reused ``holders``
correctly. These tests lock the fix in."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from aria_core.services.blockscout import (
    AddressInfo,
    TokenHolder,
    TokenHoldersResult,
    TokenTransfersResult,
)
from aria_core.skills import acp_onchain_scan as scan
from aria_core.skills.acp_onchain_scan import PairSnapshot, TokenScanContext

ADDR = "0x" + "a" * 40
DEV = "0x" + "d" * 40


def _pair() -> PairSnapshot:
    return PairSnapshot(pair_address="0xpair", liquidity_usd=20_000, base_address=ADDR)


def _holders() -> TokenHoldersResult:
    return TokenHoldersResult(
        holders=[TokenHolder(address=DEV, balance=1_000.0, percentage=5.0)],
        available=True,
    )


def _mock_get_token_transfers(monkeypatch) -> None:
    """``_resolve_dev_behavior`` -> ``gather_dev_wallet_facts`` (dev_wallet.py)
    unconditionally calls ``client.get_token_transfers(dev, limit=100)`` --
    with no injectable ``client=`` threaded through from ``scan_base_token``,
    this always resolved to the REAL ``blockscout_client`` singleton (16/08,
    real hang found: this was the actual unmocked network call behind the
    reproducible full-suite pytest hang documented in docs/task-backlog.md --
    a real, slow/rate-limited Blockscout response on the synthetic ``DEV``
    address, retried by the client's own dome/backoff, not an infinite hang
    but easily 5-18+ minutes under real degraded conditions). Mocking
    ``get_address_info``/``get_token_holders`` alone was never enough --
    every test exercising ``gather_dev_wallet_facts`` needs this too."""
    monkeypatch.setattr(
        type(scan.blockscout_client), "get_token_transfers",
        AsyncMock(return_value=TokenTransfersResult(transfers=[], available=True)),
    )


@pytest.mark.asyncio
async def test_resolve_dev_behavior_reuses_holders_no_refetch(monkeypatch):
    ctx = TokenScanContext(contract=ADDR, valid_address=True)
    ctx.best_pair = _pair()
    monkeypatch.setattr(
        type(scan.blockscout_client), "get_address_info",
        AsyncMock(return_value=AddressInfo(address=ADDR, creator_address=DEV, available=True)),
    )
    never_called = AsyncMock(side_effect=AssertionError("get_token_holders must not be re-fetched"))
    monkeypatch.setattr(type(scan.blockscout_client), "get_token_holders", never_called)
    _mock_get_token_transfers(monkeypatch)

    await scan._resolve_dev_behavior(ctx, ADDR, _holders())

    never_called.assert_not_called()
    assert ctx.dev_signal != "unknown"  # the deployer's 5% holding was resolved from the reused snapshot


@pytest.mark.asyncio
async def test_resolve_dev_behavior_no_holders_falls_back_to_fetch(monkeypatch):
    """``holders=None`` (caller has nothing to reuse) -- unchanged behavior,
    gather_dev_wallet_facts fetches it itself."""
    ctx = TokenScanContext(contract=ADDR, valid_address=True)
    ctx.best_pair = _pair()
    monkeypatch.setattr(
        type(scan.blockscout_client), "get_address_info",
        AsyncMock(return_value=AddressInfo(address=ADDR, creator_address=DEV, available=True)),
    )
    fetch_mock = AsyncMock(return_value=_holders())
    monkeypatch.setattr(type(scan.blockscout_client), "get_token_holders", fetch_mock)
    _mock_get_token_transfers(monkeypatch)

    await scan._resolve_dev_behavior(ctx, ADDR)

    fetch_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_scan_base_token_dev_behavior_does_not_duplicate_holders_call(monkeypatch):
    """End-to-end regression: scan_base_token(include_dev_behavior=True)
    must call get_token_holders exactly ONCE per evaluation (the shared
    gather at the top), never a second time via dev_behavior."""
    monkeypatch.setattr(scan, "_fetch_token_pairs", AsyncMock(return_value=[_pair()]))
    monkeypatch.setattr(
        type(scan.blockscout_client), "check_contract_flags",
        AsyncMock(return_value=scan.ContractFlags(address=ADDR, available=False, error="skip")),
    )
    holders_mock = AsyncMock(return_value=_holders())
    monkeypatch.setattr(type(scan.blockscout_client), "get_token_holders", holders_mock)
    monkeypatch.setattr(
        type(scan.blockscout_client), "get_address_info",
        AsyncMock(return_value=AddressInfo(address=ADDR, creator_address=DEV, available=True)),
    )
    _mock_get_token_transfers(monkeypatch)

    await scan.scan_base_token(ADDR, include_dev_behavior=True)

    holders_mock.assert_awaited_once()
