"""Journal append-only du futur pilote agent-wallet (seam, cf. CLAUDE.md 15/07)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import aiosqlite
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


# ── recent_failed_swap (18/07, cooldown boucle de décision autonome) ────────

async def _backdate_last_row(minutes_ago: float) -> None:
    ts = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
    async with aiosqlite.connect(awl.DB_PATH) as db:
        await db.execute(
            "UPDATE agent_wallet_tx_log SET created_at = ? "
            "WHERE id = (SELECT MAX(id) FROM agent_wallet_tx_log)",
            (ts,),
        )
        await db.commit()


@pytest.mark.asyncio
async def test_recent_failed_swap_false_with_no_history():
    assert await awl.recent_failed_swap("0xabc", within_minutes=60) is False


@pytest.mark.asyncio
async def test_recent_failed_swap_true_just_after_failure():
    await awl.record_transaction(
        wallet_product="coinbase_agentic_wallet", action_type="swap",
        token_out="0xTokenA", status="failed", reason="slippage dépassé",
    )
    assert await awl.recent_failed_swap("0xTokenA", within_minutes=60) is True
    assert await awl.recent_failed_swap("0xtokena", within_minutes=60) is True  # insensible à la casse


@pytest.mark.asyncio
async def test_recent_failed_swap_false_after_cooldown_window_elapsed():
    await awl.record_transaction(
        wallet_product="coinbase_agentic_wallet", action_type="swap",
        token_out="0xTokenB", status="failed", reason="RPC timeout",
    )
    await _backdate_last_row(120)  # 2h -- au-delà du cooldown de 60 min
    assert await awl.recent_failed_swap("0xTokenB", within_minutes=60) is False


@pytest.mark.asyncio
async def test_recent_failed_swap_false_when_last_attempt_succeeded():
    """Un succès (position ouverte) n'est jamais un cooldown -- c'est le check
    'position déjà en cours' (other_tokens) qui bloque, pas celui-ci."""
    await awl.record_transaction(
        wallet_product="coinbase_agentic_wallet", action_type="swap",
        token_out="0xTokenC", status="ok", tx_hash="0xdeadbeef",
    )
    assert await awl.recent_failed_swap("0xTokenC", within_minutes=60) is False


@pytest.mark.asyncio
async def test_recent_failed_swap_false_when_last_attempt_blocked():
    """Un refus de garde-fou (plafond, kill-switch) n'est pas un échec technique --
    jamais le même cooldown que 'failed'."""
    await awl.record_transaction(
        wallet_product="coinbase_agentic_wallet", action_type="swap",
        token_out="0xTokenD", status="blocked", reason="kill-switch actif",
    )
    assert await awl.recent_failed_swap("0xTokenD", within_minutes=60) is False


@pytest.mark.asyncio
async def test_recent_failed_swap_uses_only_the_most_recent_attempt():
    """Un échec ancien suivi d'un succès récent -- pas de cooldown fantôme."""
    await awl.record_transaction(
        wallet_product="p", action_type="swap", token_out="0xTokenE", status="failed",
    )
    await awl.record_transaction(
        wallet_product="p", action_type="swap", token_out="0xTokenE", status="ok", tx_hash="0x1",
    )
    assert await awl.recent_failed_swap("0xTokenE", within_minutes=60) is False


@pytest.mark.asyncio
async def test_recent_failed_swap_empty_token_returns_false():
    assert await awl.recent_failed_swap("", within_minutes=60) is False
