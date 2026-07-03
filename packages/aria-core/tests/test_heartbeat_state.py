from datetime import datetime, timedelta, timezone

from aria_core import heartbeat as hb


def test_heartbeat_state_survives_reload(monkeypatch, tmp_path):
    state_path = tmp_path / "heartbeat_state.json"
    monkeypatch.setattr(hb, "_HEARTBEAT_STATE_PATH", state_path)

    now = datetime.now(timezone.utc)
    hb._save_heartbeat_state({"founder_ping": now})

    reloaded = hb._load_heartbeat_state()
    assert "founder_ping" in reloaded
    assert hb._task_due("founder_ping", 1440, {}) is False


def test_heartbeat_task_due_after_interval(monkeypatch, tmp_path):
    state_path = tmp_path / "heartbeat_state.json"
    monkeypatch.setattr(hb, "_HEARTBEAT_STATE_PATH", state_path)

    old = datetime.now(timezone.utc) - timedelta(hours=25)
    hb._save_heartbeat_state({"repertoire_grow": old})

    assert hb._task_due("repertoire_grow", 1440, {}) is True