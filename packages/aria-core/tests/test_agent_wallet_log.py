"""Journal append-only du futur pilote agent-wallet (seam, cf. CLAUDE.md 15/07)."""
from __future__ import annotations

import pytest

from aria_core import agent_wallet_log as awl


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(awl, "DB_PATH", str(tmp_path / "agent_wallet_test.db"))
    yield


@pytest.mark.asyncio
async def test_empty_log_by_default():
    assert await awl.list_transactions() == []


@pytest.mark.asyncio
async def test_record_and_list_roundtrip():
    await awl.record_transaction(
        wallet_product="metamask_agent_wallet",
        chain="base",
        action_type="swap",
        token_in="USDC",
        token_out="WETH",
        amount_in=10.0,
        amount_out=0.003,
        slippage_bps=500,
        tx_hash="0xabc123",
        status="ok",
    )
    rows = await awl.list_transactions()
    assert len(rows) == 1
    row = rows[0]
    assert row["wallet_product"] == "metamask_agent_wallet"
    assert row["tx_hash"] == "0xabc123"
    assert row["status"] == "ok"
    assert row["slippage_bps"] == 500


@pytest.mark.asyncio
async def test_blocked_and_failed_attempts_are_also_logged():
    """Même doctrine que bonding_trade_log : un refus côté garde-fou reste tracé,
    jamais silencieux."""
    await awl.record_transaction(
        wallet_product="coinbase_agentic_wallet",
        action_type="swap",
        status="blocked",
        reason="slippage calculé 12% > tolérance 10%",
    )
    await awl.record_transaction(
        wallet_product="coinbase_agentic_wallet",
        action_type="swap",
        status="failed",
        reason="devis indisponible",
    )
    rows = await awl.list_transactions()
    statuses = {r["status"] for r in rows}
    assert statuses == {"blocked", "failed"}


@pytest.mark.asyncio
async def test_list_transactions_order_most_recent_first():
    await awl.record_transaction(wallet_product="p", action_type="swap", status="ok", tx_hash="0x1")
    await awl.record_transaction(wallet_product="p", action_type="swap", status="ok", tx_hash="0x2")
    rows = await awl.list_transactions()
    assert [r["tx_hash"] for r in rows] == ["0x2", "0x1"]


@pytest.mark.asyncio
async def test_list_transactions_respects_limit():
    for i in range(5):
        await awl.record_transaction(
            wallet_product="p", action_type="swap", status="ok", tx_hash=f"0x{i}"
        )
    rows = await awl.list_transactions(limit=2)
    assert len(rows) == 2
