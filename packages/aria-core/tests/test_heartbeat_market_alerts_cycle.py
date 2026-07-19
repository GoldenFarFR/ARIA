"""Câblage heartbeat de market_alerts_cycle (19/07, digest Otto AI x402, module jumeau
de market_sentiment_cycle) -- gate dynamique + dispatch d'exécution."""
from __future__ import annotations

import pytest

from aria_core import heartbeat


def _find_task(task_id: str) -> heartbeat.HeartbeatTask:
    for task in heartbeat.HEARTBEAT_TASKS:
        if task.id == task_id:
            return task
    raise AssertionError(f"tâche {task_id} introuvable dans HEARTBEAT_TASKS")


def test_market_alerts_cycle_registered_and_off_by_default():
    task = _find_task("market_alerts_cycle")
    assert task.enabled is False
    assert task.interval_minutes == 60


@pytest.mark.asyncio
async def test_run_task_dispatches_to_market_alerts_cycle(monkeypatch):
    called = {"n": 0}

    async def fake_cycle():
        called["n"] += 1
        return {"updated": True}

    monkeypatch.setattr("aria_core.skills.market_alerts.run_market_alerts_cycle", fake_cycle)
    monkeypatch.setattr("aria_core.heartbeat.append_memory", lambda *a, **k: None)

    await heartbeat.aria_heartbeat._run_task("market_alerts_cycle")

    assert called["n"] == 1


@pytest.mark.asyncio
async def test_run_task_no_memory_append_when_not_updated(monkeypatch):
    """Dégradation douce (échec de paiement/réseau) -- aucune entrée de mémoire
    "fausse" écrite si rien n'a été rafraîchi."""
    async def fake_cycle():
        return {"updated": False, "reason": "plafond hebdomadaire x402 dépassé"}

    memory_calls = []
    monkeypatch.setattr("aria_core.skills.market_alerts.run_market_alerts_cycle", fake_cycle)
    monkeypatch.setattr(
        "aria_core.heartbeat.append_memory", lambda *a, **k: memory_calls.append((a, k)),
    )

    await heartbeat.aria_heartbeat._run_task("market_alerts_cycle")

    assert memory_calls == []
