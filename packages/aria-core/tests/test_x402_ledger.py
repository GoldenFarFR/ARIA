"""Consolidated x402 ledger (07/24, operator request: "livre des recettes --
sortie et entrée x402 de tout les wallet cumulé"). Combines x402_budget
(spend) and x402_revenue_ledger (revenue) into a single net view. Isolated
DB for both underlying modules."""
from __future__ import annotations

import pytest

from aria_core import x402_budget, x402_ledger, x402_revenue_ledger


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(x402_budget, "DB_PATH", str(tmp_path / "spend_test.db"))
    monkeypatch.setattr(x402_revenue_ledger, "DB_PATH", str(tmp_path / "revenue_test.db"))
    yield


@pytest.mark.asyncio
async def test_empty_ledger_is_all_zeros():
    summary = await x402_ledger.consolidated_summary()
    assert summary["total_spent_usd"] == 0.0
    assert summary["total_revenue_usd"] == 0.0
    assert summary["net_usd"] == 0.0
    assert summary["by_wallet"] == {}


@pytest.mark.asyncio
async def test_spend_only_produces_negative_net():
    await x402_budget.record_spend(resource="wallet-verification", provider="cybercentry", amount_usd=0.02, status="ok")
    summary = await x402_ledger.consolidated_summary()
    assert summary["total_spent_usd"] == pytest.approx(0.02)
    assert summary["total_revenue_usd"] == 0.0
    assert summary["net_usd"] == pytest.approx(-0.02)


@pytest.mark.asyncio
async def test_revenue_only_produces_positive_net():
    await x402_revenue_ledger.record_sale(product="wallet_score", amount_usd=0.10, status="ok")
    summary = await x402_ledger.consolidated_summary()
    assert summary["total_spent_usd"] == 0.0
    assert summary["total_revenue_usd"] == pytest.approx(0.10)
    assert summary["net_usd"] == pytest.approx(0.10)


@pytest.mark.asyncio
async def test_combined_spend_and_revenue_nets_correctly():
    await x402_budget.record_spend(resource="wallet-verification", provider="cybercentry", amount_usd=0.02, status="ok")
    await x402_revenue_ledger.record_sale(product="wallet_score", amount_usd=0.10, status="ok")
    summary = await x402_ledger.consolidated_summary()
    assert summary["net_usd"] == pytest.approx(0.08)


@pytest.mark.asyncio
async def test_failed_entries_never_counted_on_either_side():
    await x402_budget.record_spend(resource="x", provider="y", amount_usd=1.0, status="blocked")
    await x402_revenue_ledger.record_sale(product="wallet_score", amount_usd=1.0, status="failed")
    summary = await x402_ledger.consolidated_summary()
    assert summary["total_spent_usd"] == 0.0
    assert summary["total_revenue_usd"] == 0.0


@pytest.mark.asyncio
async def test_by_wallet_breakdown_includes_the_real_receiving_wallet():
    await x402_revenue_ledger.record_sale(
        product="wallet_score", wallet="aria-wallet-X402-EVM", amount_usd=0.10, status="ok"
    )
    summary = await x402_ledger.consolidated_summary()
    assert "aria-wallet-X402-EVM" in summary["by_wallet"]
    assert summary["by_wallet"]["aria-wallet-X402-EVM"]["revenue_usd"] == pytest.approx(0.10)
    assert summary["by_wallet"]["aria-wallet-X402-EVM"]["net_usd"] == pytest.approx(0.10)


@pytest.mark.asyncio
async def test_format_consolidated_summary_renders_all_fields():
    await x402_budget.record_spend(resource="x", provider="y", amount_usd=0.02, status="ok")
    await x402_revenue_ledger.record_sale(product="wallet_score", amount_usd=0.10, status="ok")
    summary = await x402_ledger.consolidated_summary()
    text = x402_ledger.format_consolidated_summary(summary)
    assert "0.0200" in text
    assert "0.1000" in text
    assert "0.0800" in text
    assert "aria-wallet-X402-EVM" in text
