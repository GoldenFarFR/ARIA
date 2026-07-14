"""wallet_scoring_chain_ranking_refresh -- enregistrement heartbeat mensuel
(#157, 14/07). Patron test_heartbeat_bonding_discovery.py."""
from __future__ import annotations

from aria_core import heartbeat


def _task(task_id: str) -> heartbeat.HeartbeatTask:
    match = [t for t in heartbeat.HEARTBEAT_TASKS if t.id == task_id]
    assert match, f"tâche introuvable : {task_id}"
    return match[0]


def test_chain_ranking_refresh_registered_monthly_and_enabled():
    task = _task("wallet_scoring_chain_ranking_refresh")

    assert task.interval_minutes == 43200
    assert task.enabled is True
