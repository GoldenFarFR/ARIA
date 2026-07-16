"""Résilience de AriaHeartbeat._tick() -- une tâche individuelle lente ou en échec ne
doit jamais bloquer les tâches suivantes du même tick, ni empêcher la persistance de
heartbeat_state.json. Incident réel (16/07) diagnostiqué en direct par VPS Principal :
wallet_scan_queue_cycle est resté bloqué ~8+ minutes en échec continu GeckoTerminal
(429) puis CoinMarketCap (500) pendant une panne externe -- avant ce correctif, aucun
try/except n'entourait `await self._run_task(...)` dans la boucle for de `_tick()`, et
`_save_heartbeat_state()` n'était appelé qu'une seule fois APRÈS la boucle entière :
une tâche bloquée empêchait donc TOUTES les autres tâches de ce tick (dont
paper_trade_cycle) de jamais voir leur état persisté sur disque, tant que la tâche
bloquée ne se terminait pas elle-même."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from aria_core import heartbeat as hb


def _fake_task(task_id: str, *, interval_minutes: int = 15) -> hb.HeartbeatTask:
    return hb.HeartbeatTask(
        id=task_id, name=task_id, description="", interval_minutes=interval_minutes, enabled=True,
    )


@pytest.fixture(autouse=True)
def _isolate_state(monkeypatch, tmp_path):
    monkeypatch.setattr(hb, "_HEARTBEAT_STATE_PATH", tmp_path / "heartbeat_state.json")
    monkeypatch.setattr("aria_core.outgoing_pause.is_paused", lambda: False)
    monkeypatch.setattr(hb, "_sync_x_curiosity_enabled", lambda: None)


@pytest.mark.asyncio
async def test_slow_task_does_not_block_subsequent_tasks(monkeypatch):
    """Une tâche qui dépasse _TASK_TIMEOUT_SECONDS est abandonnée (asyncio.TimeoutError),
    mais la tâche suivante dans HEARTBEAT_TASKS tourne quand même dans le MÊME tick."""
    monkeypatch.setattr(hb, "_TASK_TIMEOUT_SECONDS", 0.05)
    slow_task = _fake_task("slow_task")
    fast_task = _fake_task("fast_task")
    monkeypatch.setattr(hb, "HEARTBEAT_TASKS", [slow_task, fast_task])

    ran = []

    async def fake_run_task(self, task_id):
        if task_id == "slow_task":
            await asyncio.sleep(10)  # bien plus long que le timeout de test (0.05s)
        else:
            ran.append(task_id)

    monkeypatch.setattr(hb.AriaHeartbeat, "_run_task", fake_run_task)

    heart = hb.AriaHeartbeat()
    await heart._tick()

    assert ran == ["fast_task"]  # la tâche rapide a bien tourné malgré le blocage de la première


@pytest.mark.asyncio
async def test_failing_task_does_not_block_subsequent_tasks(monkeypatch):
    """Une tâche qui lève une exception est journalisée puis ignorée -- ne coupe plus
    tout le tick comme avant ce correctif."""
    failing_task = _fake_task("failing_task")
    ok_task = _fake_task("ok_task")
    monkeypatch.setattr(hb, "HEARTBEAT_TASKS", [failing_task, ok_task])

    ran = []

    async def fake_run_task(self, task_id):
        if task_id == "failing_task":
            raise RuntimeError("boom")
        ran.append(task_id)

    monkeypatch.setattr(hb.AriaHeartbeat, "_run_task", fake_run_task)

    heart = hb.AriaHeartbeat()
    await heart._tick()  # ne doit lever aucune exception

    assert ran == ["ok_task"]


@pytest.mark.asyncio
async def test_state_persisted_incrementally_even_if_later_task_hangs(monkeypatch, tmp_path):
    """heartbeat_state.json doit contenir l'entrée de la PREMIÈRE tâche même si la
    DEUXIÈME dépasse son timeout -- avant ce correctif, _save_heartbeat_state() n'était
    appelé qu'une seule fois après la boucle entière, jamais atteint si une tâche
    bloquait indéfiniment."""
    monkeypatch.setattr(hb, "_TASK_TIMEOUT_SECONDS", 0.05)
    first_task = _fake_task("first_task")
    hanging_task = _fake_task("hanging_task")
    monkeypatch.setattr(hb, "HEARTBEAT_TASKS", [first_task, hanging_task])

    async def fake_run_task(self, task_id):
        if task_id == "hanging_task":
            await asyncio.sleep(10)

    monkeypatch.setattr(hb.AriaHeartbeat, "_run_task", fake_run_task)

    heart = hb.AriaHeartbeat()
    await heart._tick()

    persisted = hb._load_heartbeat_state()
    assert "first_task" in persisted
    assert "hanging_task" in persisted  # marquée "tentée" (jamais retentée en boucle serrée)


@pytest.mark.asyncio
async def test_task_marked_attempted_after_timeout_respects_normal_interval(monkeypatch):
    """Une tâche qui expire est quand même marquée comme "tentée maintenant" -- son
    interval_minutes normal s'applique avant la prochaine tentative, plutôt qu'un
    retry en boucle serrée toutes les 60s pendant une panne externe prolongée."""
    monkeypatch.setattr(hb, "_TASK_TIMEOUT_SECONDS", 0.05)
    task = _fake_task("wallet_scan_queue_cycle", interval_minutes=20)
    monkeypatch.setattr(hb, "HEARTBEAT_TASKS", [task])

    async def fake_run_task(self, task_id):
        await asyncio.sleep(10)

    monkeypatch.setattr(hb.AriaHeartbeat, "_run_task", fake_run_task)

    heart = hb.AriaHeartbeat()
    await heart._tick()

    assert hb._task_due("wallet_scan_queue_cycle", 20, heart._last_runs) is False
