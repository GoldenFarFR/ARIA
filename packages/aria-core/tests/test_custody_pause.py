"""Custody kill-switch (Item #62, 08/03) -- dedicated, real-money-only,
scoped separately from ``outgoing_pause``. Verifies both the module itself
(same read/write/fail-closed semantics as its outgoing_pause twin) and the
core promise of the split: real-money paths block on it, paper trading
never sees it at all.
"""
import json

import pytest

from aria_core import custody_pause, outgoing_pause
from aria_core.paths import configure_data_dir


# --- État / persistance / robustesse ---------------------------------------


def test_default_not_paused(tmp_path):
    configure_data_dir(tmp_path)
    assert custody_pause.is_paused() is False
    st = custody_pause.pause_status()
    assert st["paused"] is False
    assert st["since"] is None


def test_pause_then_resume(tmp_path):
    configure_data_dir(tmp_path)
    custody_pause.pause(by="auto:agent_wallet_monitor", reason="test incident")
    assert custody_pause.is_paused() is True
    st = custody_pause.pause_status()
    assert st["paused"] is True
    assert st["by"] == "auto:agent_wallet_monitor"
    assert st["reason"] == "test incident"
    assert st["since"] is not None

    custody_pause.resume(by=12345)
    assert custody_pause.is_paused() is False
    assert custody_pause.pause_status()["since"] is None


def test_state_persists_on_disk_and_is_a_separate_file(tmp_path):
    """Own state file, distinct from outgoing_pause's -- confirms the two
    kill-switches genuinely don't share storage."""
    configure_data_dir(tmp_path)
    custody_pause.pause(by=1)
    state_file = tmp_path / "custody_pause_state.json"
    assert state_file.exists()
    assert not (tmp_path / "pause_state.json").exists()  # outgoing_pause untouched
    data = json.loads(state_file.read_text(encoding="utf-8"))
    assert data["paused"] is True
    assert custody_pause.is_paused() is True


def test_corrupt_file_fails_closed(tmp_path):
    """Unlike outgoing_pause's asymmetric doctrine, custody_pause has no
    non-strict caller to fail open for -- always fail-closed on doubt."""
    configure_data_dir(tmp_path)
    (tmp_path / "custody_pause_state.json").write_text("{ not valid json", encoding="utf-8")
    assert custody_pause.is_paused() is True
    reason = custody_pause.money_block_reason()
    assert reason is not None and "fail-closed" in reason.lower()
    assert custody_pause.pause_status()["readable"] is False


def test_missing_file_does_not_block_money(tmp_path):
    configure_data_dir(tmp_path)
    assert custody_pause.is_paused() is False
    assert custody_pause.money_block_reason() is None
    assert custody_pause.pause_status()["readable"] is True


def test_blocked_notice_mentions_custody_and_confirmation_flow(tmp_path):
    configure_data_dir(tmp_path)
    custody_pause.pause(by="auto:agent_wallet_monitor")
    notice = custody_pause.blocked_notice("Ce swap")
    assert "custody" in notice.lower()
    assert "UTC" in notice
    assert "autorisé par moi" in notice.lower()


# --- Isolation: custody_pause is invisible to paper trading -----------------


def test_custody_pause_never_touched_by_outgoing_pause_state(tmp_path):
    """Arming the MANUAL /stop flag (outgoing_pause) must never arm custody_pause,
    and vice versa -- the two are fully independent flags."""
    configure_data_dir(tmp_path)
    outgoing_pause.pause(by="manual-owner")
    assert outgoing_pause.is_paused() is True
    assert custody_pause.is_paused() is False

    outgoing_pause.resume(by="manual-owner")
    custody_pause.pause(by="auto:agent_wallet_monitor")
    assert custody_pause.is_paused() is True
    assert outgoing_pause.is_paused() is False


# --- Blocages effectifs aux points de sortie (real-money paths only) -------


@pytest.mark.asyncio
async def test_escalate_spend_blocked_when_custody_paused(tmp_path):
    configure_data_dir(tmp_path)
    from aria_core.wallet_guard import SpendEscalationError, escalate_spend

    custody_pause.pause(by="auto:agent_wallet_monitor")
    with pytest.raises(SpendEscalationError):
        await escalate_spend(
            "client_fund_job",
            amount="1 USDC",
            counterparty="job x",
            description="Financer job x",
            payload={"job_id": "x", "amount_usdc": 1.0},
        )


@pytest.mark.asyncio
async def test_resolve_spend_blocked_when_custody_paused(tmp_path):
    configure_data_dir(tmp_path)
    from aria_core.wallet_guard import resolve_spend

    custody_pause.pause(by="auto:agent_wallet_monitor")
    out = await resolve_spend("deadbeef", True, "1")
    assert "pause" in out.lower() or "custody" in out.lower()


@pytest.mark.asyncio
async def test_agent_wallet_pilot_swap_blocked_when_custody_paused(tmp_path, monkeypatch):
    configure_data_dir(tmp_path)
    from aria_core import agent_wallet_log, agent_wallet_pilot as pilot

    monkeypatch.setattr(agent_wallet_log, "DB_PATH", str(tmp_path / "wallet_pilot_custody_test.db"))
    monkeypatch.setenv("ARIA_AGENT_WALLET_PILOT_ENABLED", "true")

    async def _ok_balance() -> float:
        return 20.0

    async def _ok_swap(**kwargs) -> dict:
        return {"tx_hash": "0xdeadbeef", "amount_out": 0.001}

    custody_pause.pause(by="auto:agent_wallet_monitor")
    result = await pilot.attempt_swap(
        chain="base", token_in="USDC", token_out="WETH", amount_in_usd=5.0,
        wallet_address="0xabc", balance_fn=_ok_balance, swap_fn=_ok_swap,
    )
    assert result.status == "blocked"
    assert "custody" in result.reason.lower()


@pytest.mark.asyncio
async def test_agent_wallet_smart_swing_blocked_when_custody_paused(tmp_path):
    configure_data_dir(tmp_path)
    from aria_core import agent_wallet_smart_swing as swing

    custody_pause.pause(by="auto:agent_wallet_monitor")
    blocked, reason = swing.blocks_swing_swaps()
    assert blocked is True
    assert reason is not None and "custody" in reason.lower()


# --- The core promise of Item #62: paper trading is exempt -----------------


@pytest.mark.asyncio
async def test_momentum_websocket_drain_ignores_custody_pause(tmp_path, monkeypatch):
    """The exact regression this module exists to prevent: a real incident
    tonight (08/02) froze ALL paper trading for ~5h15 because the auto-arm
    used to share outgoing_pause with momentum_websocket.py's drain. Proves
    custody_pause being armed does NOT stop the drain -- only outgoing_pause
    (the manual /stop) still does."""
    configure_data_dir(tmp_path)
    from aria_core import momentum_websocket

    custody_pause.pause(by="auto:agent_wallet_monitor")
    monkeypatch.setattr(momentum_websocket, "_paper_trading_enabled", lambda: True)

    drained = False

    async def _fake_drain_new_candidates(self):
        nonlocal drained
        drained = True

    monkeypatch.setattr(
        momentum_websocket.MomentumWebsocketListener, "_drain_new_candidates", _fake_drain_new_candidates
    )

    listener = momentum_websocket.MomentumWebsocketListener()
    await listener._drain_once()

    assert drained is True  # custody_pause alone never stops the paper drain
