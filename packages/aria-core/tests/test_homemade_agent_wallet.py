"""18/08 -- guardrail wrapper for the homemade agent wallet (Safe+
AllowanceModule / Squads v4). Mirrors test_agent_wallet_pilot.py's doctrine
checks (gate, kill-switch, cap, systematic logging) -- never a real chain
call here, send_fn/remaining_fn are always fakes injected by each test."""
from __future__ import annotations

import pytest

from aria_core import agent_wallet_log, homemade_agent_wallet as haw


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_wallet_log, "DB_PATH", str(tmp_path / "homemade_wallet_test.db"))
    yield


@pytest.fixture(autouse=True)
def _gate_off_by_default(monkeypatch):
    monkeypatch.delenv("ARIA_HOMEMADE_AGENT_WALLET_ENABLED", raising=False)
    monkeypatch.setattr("aria_core.outgoing_pause.is_paused", lambda **kw: False)
    monkeypatch.setattr("aria_core.custody_pause.is_paused", lambda: False)
    yield


async def _ok_remaining() -> int:
    return 10_000_000


async def _ok_send(**kwargs) -> dict:
    return {"error": None, "tx_hash": "0xdeadbeef", "status": "ok"}


@pytest.mark.asyncio
async def test_blocked_when_gate_disabled_by_default():
    result = await haw.attempt_transfer(
        chain="robinhood_testnet", amount=1_000, remaining_fn=_ok_remaining, send_fn=_ok_send,
    )
    assert result.status == "blocked"
    assert "ARIA_HOMEMADE_AGENT_WALLET_ENABLED" in result.reason
    rows = await agent_wallet_log.list_transactions()
    assert rows[0]["status"] == "blocked"
    assert rows[0]["wallet_product"] == "homemade_agent_wallet"


@pytest.mark.asyncio
async def test_blocked_when_kill_switch_paused(monkeypatch):
    monkeypatch.setenv("ARIA_HOMEMADE_AGENT_WALLET_ENABLED", "true")
    monkeypatch.setattr("aria_core.outgoing_pause.is_paused", lambda **kw: True)
    result = await haw.attempt_transfer(
        chain="robinhood_testnet", amount=1_000, remaining_fn=_ok_remaining, send_fn=_ok_send,
    )
    assert result.status == "blocked"


@pytest.mark.asyncio
async def test_blocked_when_custody_paused(monkeypatch):
    monkeypatch.setenv("ARIA_HOMEMADE_AGENT_WALLET_ENABLED", "true")
    monkeypatch.setattr("aria_core.custody_pause.is_paused", lambda: True)
    result = await haw.attempt_transfer(
        chain="robinhood_testnet", amount=1_000, remaining_fn=_ok_remaining, send_fn=_ok_send,
    )
    assert result.status == "blocked"


@pytest.mark.asyncio
async def test_blocked_when_amount_is_zero_or_negative(monkeypatch):
    monkeypatch.setenv("ARIA_HOMEMADE_AGENT_WALLET_ENABLED", "true")
    result = await haw.attempt_transfer(
        chain="robinhood_testnet", amount=0, remaining_fn=_ok_remaining, send_fn=_ok_send,
    )
    assert result.status == "blocked"
    assert "nul" in result.reason


@pytest.mark.asyncio
async def test_blocked_when_amount_exceeds_app_level_cap(monkeypatch):
    monkeypatch.setenv("ARIA_HOMEMADE_AGENT_WALLET_ENABLED", "true")
    result = await haw.attempt_transfer(
        chain="robinhood_testnet", amount=haw.MAX_TRANSACTION_NATIVE_UNITS + 1,
        remaining_fn=_ok_remaining, send_fn=_ok_send,
    )
    assert result.status == "blocked"
    assert "plafond" in result.reason


@pytest.mark.asyncio
async def test_blocked_when_amount_exceeds_real_remaining_allowance(monkeypatch):
    monkeypatch.setenv("ARIA_HOMEMADE_AGENT_WALLET_ENABLED", "true")

    async def low_remaining():
        return 100

    result = await haw.attempt_transfer(
        chain="robinhood_testnet", amount=101, remaining_fn=low_remaining, send_fn=_ok_send,
    )
    assert result.status == "blocked"
    assert "allowance restante réelle" in result.reason


@pytest.mark.asyncio
async def test_blocked_fail_closed_when_remaining_fn_raises(monkeypatch):
    monkeypatch.setenv("ARIA_HOMEMADE_AGENT_WALLET_ENABLED", "true")

    async def broken_remaining():
        raise ConnectionError("RPC down")

    result = await haw.attempt_transfer(
        chain="robinhood_testnet", amount=1, remaining_fn=broken_remaining, send_fn=_ok_send,
    )
    assert result.status == "blocked"
    assert "indisponible" in result.reason


@pytest.mark.asyncio
async def test_ok_path_logs_success_and_returns_tx_hash(monkeypatch):
    monkeypatch.setenv("ARIA_HOMEMADE_AGENT_WALLET_ENABLED", "true")
    result = await haw.attempt_transfer(
        chain="robinhood_testnet", amount=1_000, remaining_fn=_ok_remaining, send_fn=_ok_send,
        safe="0xabc", to="0xdef",
    )
    assert result.status == "ok"
    assert result.tx_hash == "0xdeadbeef"
    rows = await agent_wallet_log.list_transactions()
    assert rows[0]["status"] == "ok"


@pytest.mark.asyncio
async def test_send_kwargs_are_forwarded_to_send_fn(monkeypatch):
    monkeypatch.setenv("ARIA_HOMEMADE_AGENT_WALLET_ENABLED", "true")
    seen = {}

    async def _capturing_send(**kwargs):
        seen.update(kwargs)
        return {"error": None, "tx_hash": "0x1", "status": "ok"}

    await haw.attempt_transfer(
        chain="robinhood_testnet", amount=1_000, remaining_fn=_ok_remaining,
        send_fn=_capturing_send, safe="0xabc", to="0xdef", delegate_key_path="/tmp/x",
    )
    assert seen["amount"] == 1_000
    assert seen["safe"] == "0xabc"
    assert seen["to"] == "0xdef"
    assert seen["delegate_key_path"] == "/tmp/x"


@pytest.mark.asyncio
async def test_send_fn_exception_is_logged_as_failed(monkeypatch):
    monkeypatch.setenv("ARIA_HOMEMADE_AGENT_WALLET_ENABLED", "true")

    async def _broken_send(**kwargs):
        raise RuntimeError("send crashed")

    result = await haw.attempt_transfer(
        chain="robinhood_testnet", amount=1_000, remaining_fn=_ok_remaining, send_fn=_broken_send,
    )
    assert result.status == "failed"
    rows = await agent_wallet_log.list_transactions()
    assert rows[0]["status"] == "failed"


@pytest.mark.asyncio
async def test_send_fn_error_field_is_treated_as_failure(monkeypatch):
    monkeypatch.setenv("ARIA_HOMEMADE_AGENT_WALLET_ENABLED", "true")

    async def _erroring_send(**kwargs):
        return {"error": "on-chain call rejected", "tx_hash": None}

    result = await haw.attempt_transfer(
        chain="robinhood_testnet", amount=1_000, remaining_fn=_ok_remaining, send_fn=_erroring_send,
    )
    assert result.status == "failed"
    assert result.reason == "on-chain call rejected"


@pytest.mark.asyncio
async def test_reverted_onchain_status_is_treated_as_failure_not_success(monkeypatch):
    """A mined transaction that reverted must never be reported as 'ok' --
    the whole point of re-checking the on-chain receipt status."""
    monkeypatch.setenv("ARIA_HOMEMADE_AGENT_WALLET_ENABLED", "true")

    async def _reverted_send(**kwargs):
        return {"error": None, "tx_hash": "0xreverted", "status": "reverted"}

    result = await haw.attempt_transfer(
        chain="robinhood_testnet", amount=1_000, remaining_fn=_ok_remaining, send_fn=_reverted_send,
    )
    assert result.status == "failed"
    assert result.tx_hash == "0xreverted"
    assert "REVERTED" in result.reason
