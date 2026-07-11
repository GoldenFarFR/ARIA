"""canonical_facts_sync_cycle — enregistrement + gate OFF par défaut (heartbeat).

Trou trouvé le 11/07 : `sync_canonical_facts()` existait depuis la migration monorepo
(01/07) mais n'avait jamais eu d'appelant en production -- cause racine du doublon
`content/faq.yaml`/`truth_ledger/canonical_facts.yaml` (aucune synchro réelle malgré le
mécanisme conçu pour ça). Câblé au heartbeat, même patron que les autres tâches."""
from __future__ import annotations

from aria_core import heartbeat


def _task(task_id: str) -> heartbeat.HeartbeatTask:
    match = [t for t in heartbeat.HEARTBEAT_TASKS if t.id == task_id]
    assert match, f"tâche introuvable : {task_id}"
    return match[0]


def test_canonical_facts_sync_cycle_registered_and_off_by_default():
    task = _task("canonical_facts_sync_cycle")
    assert task.enabled is False
    assert task.interval_minutes > 0


def test_gate_respects_env_var(monkeypatch):
    monkeypatch.delenv("ARIA_CANONICAL_FACTS_SYNC_ENABLED", raising=False)
    heartbeat._sync_x_curiosity_enabled()
    assert _task("canonical_facts_sync_cycle").enabled is False

    monkeypatch.setenv("ARIA_CANONICAL_FACTS_SYNC_ENABLED", "1")
    heartbeat._sync_x_curiosity_enabled()
    assert _task("canonical_facts_sync_cycle").enabled is True

    monkeypatch.delenv("ARIA_CANONICAL_FACTS_SYNC_ENABLED", raising=False)
    heartbeat._sync_x_curiosity_enabled()
    assert _task("canonical_facts_sync_cycle").enabled is False
