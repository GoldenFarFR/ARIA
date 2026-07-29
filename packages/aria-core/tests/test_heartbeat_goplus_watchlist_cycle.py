"""Item #212 (29/07) -- goplus_watchlist_cycle double-gate wiring (same pattern
as daily_trade_floor_cycle: needs BOTH the master ARIA_PAPER_TRADING_ENABLED
gate AND its own dedicated ARIA_GOPLUS_WATCHLIST_ENABLED)."""
from __future__ import annotations

from aria_core import heartbeat


def _task(task_id: str) -> heartbeat.HeartbeatTask:
    match = [t for t in heartbeat.HEARTBEAT_TASKS if t.id == task_id]
    assert match, f"tâche introuvable : {task_id}"
    return match[0]


def test_disabled_when_both_gates_off(monkeypatch):
    monkeypatch.delenv("ARIA_PAPER_TRADING_ENABLED", raising=False)
    monkeypatch.delenv("ARIA_GOPLUS_WATCHLIST_ENABLED", raising=False)
    heartbeat._sync_x_curiosity_enabled()
    assert _task("goplus_watchlist_cycle").enabled is False


def test_disabled_when_only_paper_trading_on(monkeypatch):
    monkeypatch.setenv("ARIA_PAPER_TRADING_ENABLED", "1")
    monkeypatch.delenv("ARIA_GOPLUS_WATCHLIST_ENABLED", raising=False)
    heartbeat._sync_x_curiosity_enabled()
    assert _task("goplus_watchlist_cycle").enabled is False


def test_disabled_when_only_watchlist_gate_on(monkeypatch):
    monkeypatch.delenv("ARIA_PAPER_TRADING_ENABLED", raising=False)
    monkeypatch.setenv("ARIA_GOPLUS_WATCHLIST_ENABLED", "1")
    heartbeat._sync_x_curiosity_enabled()
    assert _task("goplus_watchlist_cycle").enabled is False


def test_enabled_when_both_gates_on(monkeypatch):
    monkeypatch.setenv("ARIA_PAPER_TRADING_ENABLED", "1")
    monkeypatch.setenv("ARIA_GOPLUS_WATCHLIST_ENABLED", "1")
    heartbeat._sync_x_curiosity_enabled()
    assert _task("goplus_watchlist_cycle").enabled is True

    monkeypatch.delenv("ARIA_GOPLUS_WATCHLIST_ENABLED", raising=False)
    heartbeat._sync_x_curiosity_enabled()
    assert _task("goplus_watchlist_cycle").enabled is False
