"""Regression test for a real 07/24 production gap found by a throughput audit:
`trade_devils_advocate_cycle` and others each had a real, working
`*_enabled()` gate function in their own module, but
`_sync_x_curiosity_enabled()` never called it -- these tasks stayed frozen at
the static `enabled=False` declared in HEARTBEAT_TASKS forever, regardless of
the env var. Same pattern as test_heartbeat_bonding_discovery.py.

25/08 -- `smart_money_leaderboard_discovery_cycle` and
`token_holder_extraction_cycle`'s own tests were removed here along with the
entire wallet-scoring mechanism they belonged to (operator decision)."""
from __future__ import annotations

from aria_core import heartbeat


def _task(task_id: str) -> heartbeat.HeartbeatTask:
    match = [t for t in heartbeat.HEARTBEAT_TASKS if t.id == task_id]
    assert match, f"tâche introuvable : {task_id}"
    return match[0]


def test_trade_devils_advocate_cycle_respects_its_env_var(monkeypatch):
    monkeypatch.delenv("ARIA_TRADE_DEVILS_ADVOCATE_ENABLED", raising=False)
    heartbeat._sync_x_curiosity_enabled()
    assert _task("trade_devils_advocate_cycle").enabled is False

    monkeypatch.setenv("ARIA_TRADE_DEVILS_ADVOCATE_ENABLED", "1")
    heartbeat._sync_x_curiosity_enabled()
    assert _task("trade_devils_advocate_cycle").enabled is True

    monkeypatch.delenv("ARIA_TRADE_DEVILS_ADVOCATE_ENABLED", raising=False)
    heartbeat._sync_x_curiosity_enabled()
    assert _task("trade_devils_advocate_cycle").enabled is False


def test_dip_recovery_shadow_cycle_respects_its_env_var(monkeypatch):
    monkeypatch.delenv("ARIA_DIP_RECOVERY_SHADOW_ENABLED", raising=False)
    heartbeat._sync_x_curiosity_enabled()
    assert _task("dip_recovery_shadow_cycle").enabled is False

    monkeypatch.setenv("ARIA_DIP_RECOVERY_SHADOW_ENABLED", "1")
    heartbeat._sync_x_curiosity_enabled()
    assert _task("dip_recovery_shadow_cycle").enabled is True

    monkeypatch.delenv("ARIA_DIP_RECOVERY_SHADOW_ENABLED", raising=False)
    heartbeat._sync_x_curiosity_enabled()
    assert _task("dip_recovery_shadow_cycle").enabled is False
