"""Regression test for a real 07/24 production gap found by a throughput audit:
`smart_money_leaderboard_discovery_cycle`, `token_holder_extraction_cycle`, and
`trade_devils_advocate_cycle` each had a real, working `*_enabled()` gate
function in their own module, but `_sync_x_curiosity_enabled()` never called
it -- these 3 tasks stayed frozen at the static `enabled=False` declared in
HEARTBEAT_TASKS forever, regardless of the env var. Confirmed live (docker
exec against the running prod container): the first two gates were already
True in production, so this silently nullified capabilities CLAUDE.md
documents as live (21/07 "Intelligence wallet/entité propriétaire... EN
LIGNE"). Same pattern as test_heartbeat_bonding_discovery.py."""
from __future__ import annotations

from aria_core import heartbeat


def _task(task_id: str) -> heartbeat.HeartbeatTask:
    match = [t for t in heartbeat.HEARTBEAT_TASKS if t.id == task_id]
    assert match, f"tâche introuvable : {task_id}"
    return match[0]


def test_smart_money_leaderboard_discovery_cycle_respects_its_env_var(monkeypatch):
    monkeypatch.delenv("ARIA_SMART_MONEY_LEADERBOARD_ENABLED", raising=False)
    heartbeat._sync_x_curiosity_enabled()
    assert _task("smart_money_leaderboard_discovery_cycle").enabled is False

    monkeypatch.setenv("ARIA_SMART_MONEY_LEADERBOARD_ENABLED", "1")
    heartbeat._sync_x_curiosity_enabled()
    assert _task("smart_money_leaderboard_discovery_cycle").enabled is True

    monkeypatch.delenv("ARIA_SMART_MONEY_LEADERBOARD_ENABLED", raising=False)
    heartbeat._sync_x_curiosity_enabled()
    assert _task("smart_money_leaderboard_discovery_cycle").enabled is False


def test_token_holder_extraction_cycle_respects_its_env_var(monkeypatch):
    monkeypatch.delenv("ARIA_TOKEN_HOLDER_EXTRACTION_ENABLED", raising=False)
    heartbeat._sync_x_curiosity_enabled()
    assert _task("token_holder_extraction_cycle").enabled is False

    monkeypatch.setenv("ARIA_TOKEN_HOLDER_EXTRACTION_ENABLED", "1")
    heartbeat._sync_x_curiosity_enabled()
    assert _task("token_holder_extraction_cycle").enabled is True

    monkeypatch.delenv("ARIA_TOKEN_HOLDER_EXTRACTION_ENABLED", raising=False)
    heartbeat._sync_x_curiosity_enabled()
    assert _task("token_holder_extraction_cycle").enabled is False


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
