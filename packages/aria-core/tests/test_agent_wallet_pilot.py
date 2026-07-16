"""Pilote agent-wallet réel (Coinbase Agentic Wallet) -- exception nommée décidée
par l'opérateur (16/07) sur docs/pilote-agent-wallet-10usd.md §4. Vérifie les
garde-fous non négociables (§3) : plafond dur sur solde réel, slippage forcé,
kill-switch, journalisation systématique -- jamais un appel réel au SDK CDP ici,
seulement des fakes injectés."""
from __future__ import annotations

import pytest

from aria_core import agent_wallet_log, agent_wallet_pilot as pilot


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_wallet_log, "DB_PATH", str(tmp_path / "wallet_pilot_test.db"))
    yield


@pytest.fixture(autouse=True)
def _gate_off_by_default(monkeypatch):
    monkeypatch.delenv("ARIA_AGENT_WALLET_PILOT_ENABLED", raising=False)
    monkeypatch.setattr("aria_core.outgoing_pause.is_paused", lambda **kw: False)
    yield


async def _ok_balance() -> float:
    return 20.0


async def _ok_swap(**kwargs) -> dict:
    return {"tx_hash": "0xdeadbeef", "amount_out": 0.001}


@pytest.mark.asyncio
async def test_blocked_when_gate_disabled_by_default():
    result = await pilot.attempt_swap(
        chain="base", token_in="USDC", token_out="WETH", amount_in_usd=5.0,
        wallet_address="0xabc", balance_fn=_ok_balance, swap_fn=_ok_swap,
    )
    assert result.status == "blocked"
    assert "ARIA_AGENT_WALLET_PILOT_ENABLED" in result.reason
    rows = await agent_wallet_log.list_transactions()
    assert rows[0]["status"] == "blocked"


@pytest.mark.asyncio
async def test_blocked_when_kill_switch_paused(monkeypatch):
    monkeypatch.setenv("ARIA_AGENT_WALLET_PILOT_ENABLED", "true")
    monkeypatch.setattr("aria_core.outgoing_pause.is_paused", lambda **kw: True)
    result = await pilot.attempt_swap(
        chain="base", token_in="USDC", token_out="WETH", amount_in_usd=5.0,
        wallet_address="0xabc", balance_fn=_ok_balance, swap_fn=_ok_swap,
    )
    assert result.status == "blocked"


@pytest.mark.asyncio
async def test_blocked_when_amount_exceeds_hard_cap(monkeypatch):
    monkeypatch.setenv("ARIA_AGENT_WALLET_PILOT_ENABLED", "true")
    result = await pilot.attempt_swap(
        chain="base", token_in="USDC", token_out="WETH",
        amount_in_usd=pilot.MAX_TRANSACTION_USD + 0.01,
        wallet_address="0xabc", balance_fn=_ok_balance, swap_fn=_ok_swap,
    )
    assert result.status == "blocked"
    assert "plafond" in result.reason


@pytest.mark.asyncio
async def test_blocked_when_amount_exceeds_real_balance(monkeypatch):
    monkeypatch.setenv("ARIA_AGENT_WALLET_PILOT_ENABLED", "true")

    async def low_balance():
        return 2.0

    result = await pilot.attempt_swap(
        chain="base", token_in="USDC", token_out="WETH", amount_in_usd=5.0,
        wallet_address="0xabc", balance_fn=low_balance, swap_fn=_ok_swap,
    )
    assert result.status == "blocked"
    assert "solde réel" in result.reason


@pytest.mark.asyncio
async def test_fail_closed_when_balance_unavailable(monkeypatch):
    monkeypatch.setenv("ARIA_AGENT_WALLET_PILOT_ENABLED", "true")

    async def unavailable_balance():
        return None

    result = await pilot.attempt_swap(
        chain="base", token_in="USDC", token_out="WETH", amount_in_usd=5.0,
        wallet_address="0xabc", balance_fn=unavailable_balance, swap_fn=_ok_swap,
    )
    assert result.status == "blocked"


@pytest.mark.asyncio
async def test_fail_closed_when_balance_fn_raises(monkeypatch):
    monkeypatch.setenv("ARIA_AGENT_WALLET_PILOT_ENABLED", "true")

    async def raising_balance():
        raise RuntimeError("API CDP down")

    result = await pilot.attempt_swap(
        chain="base", token_in="USDC", token_out="WETH", amount_in_usd=5.0,
        wallet_address="0xabc", balance_fn=raising_balance, swap_fn=_ok_swap,
    )
    assert result.status == "blocked"


@pytest.mark.asyncio
async def test_successful_swap_within_cap_and_balance(monkeypatch):
    monkeypatch.setenv("ARIA_AGENT_WALLET_PILOT_ENABLED", "true")
    result = await pilot.attempt_swap(
        chain="base", token_in="USDC", token_out="WETH", amount_in_usd=5.0,
        wallet_address="0xabc", balance_fn=_ok_balance, swap_fn=_ok_swap,
    )
    assert result.status == "ok"
    assert result.tx_hash == "0xdeadbeef"
    rows = await agent_wallet_log.list_transactions()
    assert rows[0]["status"] == "ok"
    assert rows[0]["wallet_product"] == "coinbase_agentic_wallet"
    assert rows[0]["slippage_bps"] == pilot.MAX_SLIPPAGE_BPS


@pytest.mark.asyncio
async def test_slippage_always_forced_regardless_of_caller_input(monkeypatch):
    """Règle absolue 09/07 : jamais la valeur par défaut/fournie d'un outil externe."""
    monkeypatch.setenv("ARIA_AGENT_WALLET_PILOT_ENABLED", "true")
    captured = {}

    async def capturing_swap(**kwargs):
        captured.update(kwargs)
        return {"tx_hash": "0x1", "amount_out": 0.001}

    await pilot.attempt_swap(
        chain="base", token_in="USDC", token_out="WETH", amount_in_usd=5.0,
        wallet_address="0xabc", balance_fn=_ok_balance, swap_fn=capturing_swap,
        slippage_bps=3000,  # tentative de contourner -- doit être ignorée
    )
    assert captured["slippage_bps"] == pilot.MAX_SLIPPAGE_BPS


@pytest.mark.asyncio
async def test_swap_failure_logged_as_failed_not_blocked(monkeypatch):
    monkeypatch.setenv("ARIA_AGENT_WALLET_PILOT_ENABLED", "true")

    async def failing_swap(**kwargs):
        raise RuntimeError("facilitator timeout")

    result = await pilot.attempt_swap(
        chain="base", token_in="USDC", token_out="WETH", amount_in_usd=5.0,
        wallet_address="0xabc", balance_fn=_ok_balance, swap_fn=failing_swap,
    )
    assert result.status == "failed"
    rows = await agent_wallet_log.list_transactions()
    assert rows[0]["status"] == "failed"


@pytest.mark.asyncio
async def test_zero_or_negative_amount_blocked(monkeypatch):
    monkeypatch.setenv("ARIA_AGENT_WALLET_PILOT_ENABLED", "true")
    result = await pilot.attempt_swap(
        chain="base", token_in="USDC", token_out="WETH", amount_in_usd=0.0,
        wallet_address="0xabc", balance_fn=_ok_balance, swap_fn=_ok_swap,
    )
    assert result.status == "blocked"


@pytest.mark.asyncio
async def test_no_generic_transfer_function_exists():
    """§5 de la doc : aucune fonction de transfert/retrait générique dans ce module."""
    assert not hasattr(pilot, "transfer")
    assert not hasattr(pilot, "withdraw")
    assert not hasattr(pilot, "send")
