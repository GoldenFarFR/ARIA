"""24/08 -- first production/heartbeat caller for the Robinhood Chain leg of
the homemade agent wallet. Offline-only tests (fake w3/injected functions,
same doctrine as the rest of this chantier's test suite) -- the real proof
already happened live on testnet (docs/HANDOFF_AGENT_WALLET.md, 23-24/08
entries)."""
from __future__ import annotations

import pytest
from eth_account import Account

from aria_core.onchain import robinhood_pilot_cycle as cycle


@pytest.mark.asyncio
async def test_disabled_gate_short_circuits_before_touching_anything(monkeypatch):
    monkeypatch.delenv("ARIA_ROBINHOOD_TESTNET_REHEARSAL_ENABLED", raising=False)
    result = await cycle.run_robinhood_testnet_rehearsal_cycle()
    assert result == {"outcome": "disabled"}


@pytest.mark.asyncio
async def test_reports_no_key_without_raising(monkeypatch):
    monkeypatch.setenv("ARIA_ROBINHOOD_TESTNET_REHEARSAL_ENABLED", "true")
    from aria_core.onchain import safe_robinhood_deploy as deploy

    def _raise(*a, **kw):
        raise RuntimeError("ARIA_ROBINHOOD_DEPLOYER_PRIVATE_KEY absente ou vide")

    monkeypatch.setattr(deploy, "deployer_account", _raise)
    result = await cycle.run_robinhood_testnet_rehearsal_cycle()
    assert result == {"outcome": "no_key"}


@pytest.mark.asyncio
async def test_happy_path_wires_remaining_and_send_through_the_guardrail_wrapper(monkeypatch):
    monkeypatch.setenv("ARIA_ROBINHOOD_TESTNET_REHEARSAL_ENABLED", "true")
    monkeypatch.setenv("ARIA_HOMEMADE_AGENT_WALLET_ENABLED", "true")

    from aria_core import custody_pause, homemade_agent_wallet, outgoing_pause
    from aria_core.onchain import safe_robinhood_deploy as deploy
    from aria_core.onchain import safe_robinhood_signer as signer
    from aria_core.onchain import safe_robinhood_wallet as wallet

    monkeypatch.setattr(outgoing_pause, "is_paused", lambda strict=False: False)
    monkeypatch.setattr(custody_pause, "is_paused", lambda: False)
    monkeypatch.setattr(homemade_agent_wallet.agent_wallet_log, "record_transaction", _noop_record)

    account = Account.create()
    monkeypatch.setattr(deploy, "deployer_account", lambda: account)
    monkeypatch.setattr(wallet, "read_allowance", lambda *a, **kw: {"error": None, "remaining": 20})

    captured = {}

    async def fake_send(*, safe, token, to, amount, account):  # noqa: ANN001
        captured["safe"] = safe
        captured["token"] = token
        captured["to"] = to
        captured["amount"] = amount
        return {"error": None, "tx_hash": "0xabc", "status": "ok"}

    monkeypatch.setattr(signer, "send_allowance_transfer", fake_send)

    result = await cycle.run_robinhood_testnet_rehearsal_cycle()

    assert result["outcome"] == "ok"
    assert result["tx_hash"] == "0xabc"
    assert captured["safe"] == cycle.SAFE_ADDRESS
    assert captured["token"] == cycle.TOKEN_ADDRESS
    assert captured["to"] == account.address  # closed loop -- delegate is its own destination
    assert captured["amount"] == cycle.REHEARSAL_AMOUNT


@pytest.mark.asyncio
async def test_kill_switch_blocks_before_any_send_attempt(monkeypatch):
    """The whole point of routing through ``homemade_agent_wallet.
    attempt_transfer`` rather than calling the signer directly: the kill
    switch is checked even on worthless testnet funds, so the wiring is
    genuinely rehearsed, not bypassed for convenience."""
    monkeypatch.setenv("ARIA_ROBINHOOD_TESTNET_REHEARSAL_ENABLED", "true")
    monkeypatch.setenv("ARIA_HOMEMADE_AGENT_WALLET_ENABLED", "true")

    from aria_core import homemade_agent_wallet, outgoing_pause
    from aria_core.onchain import safe_robinhood_deploy as deploy
    from aria_core.onchain import safe_robinhood_signer as signer

    monkeypatch.setattr(outgoing_pause, "is_paused", lambda strict=False: True)
    monkeypatch.setattr(outgoing_pause, "blocked_notice", lambda label: f"{label} bloque")
    monkeypatch.setattr(homemade_agent_wallet.agent_wallet_log, "record_transaction", _noop_record)

    account = Account.create()
    monkeypatch.setattr(deploy, "deployer_account", lambda: account)

    called = {"send": False}

    async def fake_send(**_kw):
        called["send"] = True
        return {"error": None, "tx_hash": "0xshouldnothappen", "status": "ok"}

    monkeypatch.setattr(signer, "send_allowance_transfer", fake_send)

    result = await cycle.run_robinhood_testnet_rehearsal_cycle()

    assert result["outcome"] == "blocked"
    assert called["send"] is False


async def _noop_record(*a, **kw):
    return None
