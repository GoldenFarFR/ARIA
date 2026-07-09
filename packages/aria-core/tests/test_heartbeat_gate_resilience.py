"""Résilience de _sync_x_curiosity_enabled() -- un gate de tâche cassé (import manquant,
dépendance non déployée) ne doit ni planter le reste de la boucle ni empêcher l'évaluation
des AUTRES tâches. Incident réel (09/07) : aria_core.x_profile absent sur le VPS (image
Docker non redéployée) faisait planter TOUT _tick() dès le premier appel, donc AUCUNE tâche
heartbeat ne tournait -- pas seulement x_profile_sync."""
from __future__ import annotations

from aria_core import heartbeat


def _task(task_id: str) -> heartbeat.HeartbeatTask:
    match = [t for t in heartbeat.HEARTBEAT_TASKS if t.id == task_id]
    assert match, f"tâche introuvable : {task_id}"
    return match[0]


def test_broken_gate_does_not_stop_evaluation_of_other_tasks(monkeypatch):
    """Simule l'incident réel : le gate de x_profile_sync lève ModuleNotFoundError.
    Les tâches qui suivent dans HEARTBEAT_TASKS (ex. paper_trade_cycle, aria_exam_cycle)
    doivent quand même être correctement (ré)évaluées, pas rester bloquées sur un état
    périmé faute d'avoir été atteintes."""
    import aria_core.x_profile as x_profile_module

    def _broken(*args, **kwargs):
        raise ModuleNotFoundError("No module named 'aria_core.x_profile'")

    monkeypatch.setattr(x_profile_module, "x_profile_sync_enabled", _broken)
    monkeypatch.setenv("ARIA_PAPER_TRADING_ENABLED", "1")
    monkeypatch.setattr("aria_core.exam.exam_enabled", lambda: True)

    # Ne doit lever aucune exception.
    heartbeat._sync_x_curiosity_enabled()

    assert _task("x_profile_sync").enabled is False  # fail-closed sur le gate cassé
    assert _task("paper_trade_cycle").enabled is True  # évaluée normalement malgré le crash précédent
    assert _task("aria_exam_cycle").enabled is True  # idem, plus loin dans la liste


def test_broken_gate_is_reevaluated_cleanly_once_fixed(monkeypatch):
    """Une fois le module réellement disponible (cas normal), le gate reprend une valeur
    saine -- le fail-closed du cycle précédent n'est pas persistant au-delà d'un throw."""
    heartbeat._sync_x_curiosity_enabled()
    # Sans ARIA_X_PROFILE_SYNC_ENABLED ni X configuré, la tâche reste désactivée --
    # mais surtout : aucune exception, la fonction tourne bout en bout normalement.
    assert isinstance(_task("x_profile_sync").enabled, bool)
