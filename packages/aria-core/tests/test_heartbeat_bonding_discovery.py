"""bonding_discovery_cycle — enregistrement + gate OFF par défaut (heartbeat)."""
from __future__ import annotations

from aria_core import heartbeat


def _task(task_id: str) -> heartbeat.HeartbeatTask:
    match = [t for t in heartbeat.HEARTBEAT_TASKS if t.id == task_id]
    assert match, f"tâche introuvable : {task_id}"
    return match[0]


def test_bonding_discovery_cycle_registered_and_off_by_default():
    task = _task("bonding_discovery_cycle")
    assert task.enabled is False
    assert task.interval_minutes > 0


def test_gate_respects_env_var(monkeypatch):
    monkeypatch.delenv("ARIA_BONDING_DISCOVERY_ENABLED", raising=False)
    heartbeat._sync_x_curiosity_enabled()
    assert _task("bonding_discovery_cycle").enabled is False

    monkeypatch.setenv("ARIA_BONDING_DISCOVERY_ENABLED", "1")
    heartbeat._sync_x_curiosity_enabled()
    assert _task("bonding_discovery_cycle").enabled is True

    monkeypatch.delenv("ARIA_BONDING_DISCOVERY_ENABLED", raising=False)
    heartbeat._sync_x_curiosity_enabled()
    assert _task("bonding_discovery_cycle").enabled is False
