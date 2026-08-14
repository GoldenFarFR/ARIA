"""#166/#167 (14/08) -- vector_memory_maintenance_cycle single-gate wiring
(standalone maintenance, no paper-trading double-gate needed -- it never
touches the momentum/paper-trading pipeline, same doctrine as
candle_history_watchlist_cycle)."""
from __future__ import annotations

from aria_core import heartbeat


def _task() -> heartbeat.HeartbeatTask:
    match = [t for t in heartbeat.HEARTBEAT_TASKS if t.id == "vector_memory_maintenance_cycle"]
    assert match, "tâche introuvable : vector_memory_maintenance_cycle"
    return match[0]


def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ARIA_VECTOR_MEMORY_MAINTENANCE_ENABLED", raising=False)
    heartbeat._sync_x_curiosity_enabled()
    assert _task().enabled is False


def test_enabled_when_gate_on(monkeypatch):
    monkeypatch.setenv("ARIA_VECTOR_MEMORY_MAINTENANCE_ENABLED", "1")
    heartbeat._sync_x_curiosity_enabled()
    assert _task().enabled is True

    monkeypatch.delenv("ARIA_VECTOR_MEMORY_MAINTENANCE_ENABLED", raising=False)
    heartbeat._sync_x_curiosity_enabled()
    assert _task().enabled is False


def test_weekly_cadence():
    assert _task().interval_minutes == 10080
