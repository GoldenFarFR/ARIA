"""Paper-trading runtime kill-switch (Item #64, 08/03) -- /off and /on on
Telegram. Distinct from custody_pause: fails OPEN (never freezes the $1M
test over a corrupted debug toggle), and is wired into the scanning/sourcing
paths (heartbeat task gating + momentum_websocket drain), never into any
real-money path.
"""
import json

from aria_core import paper_pause
from aria_core.paths import configure_data_dir


def test_default_not_paused(tmp_path):
    configure_data_dir(tmp_path)
    assert paper_pause.is_paused() is False
    st = paper_pause.pause_status()
    assert st["paused"] is False
    assert st["since"] is None


def test_pause_then_resume(tmp_path):
    configure_data_dir(tmp_path)
    paper_pause.pause(by=12345, reason="debug session")
    assert paper_pause.is_paused() is True
    st = paper_pause.pause_status()
    assert st["paused"] is True
    assert st["by"] == 12345
    assert st["reason"] == "debug session"
    assert st["since"] is not None

    paper_pause.resume(by=12345)
    assert paper_pause.is_paused() is False
    assert paper_pause.pause_status()["since"] is None


def test_state_persists_on_disk_own_file(tmp_path):
    configure_data_dir(tmp_path)
    paper_pause.pause(by=1)
    state_file = tmp_path / "paper_pause_state.json"
    assert state_file.exists()
    assert not (tmp_path / "custody_pause_state.json").exists()
    assert not (tmp_path / "pause_state.json").exists()
    data = json.loads(state_file.read_text(encoding="utf-8"))
    assert data["paused"] is True


def test_corrupt_file_fails_open(tmp_path):
    """Opposite doctrine from custody_pause: a corrupted state must never
    freeze the diagnostic test over a debug toggle."""
    configure_data_dir(tmp_path)
    (tmp_path / "paper_pause_state.json").write_text("{ not valid json", encoding="utf-8")
    assert paper_pause.is_paused() is False
    assert paper_pause.pause_status()["readable"] is False


def test_never_shares_state_with_outgoing_or_custody_pause(tmp_path):
    from aria_core import custody_pause, outgoing_pause

    configure_data_dir(tmp_path)
    outgoing_pause.pause(by="manual-owner")
    custody_pause.pause(by="auto:agent_wallet_monitor")
    assert paper_pause.is_paused() is False  # neither flag touches this one

    outgoing_pause.resume(by="manual-owner")
    custody_pause.resume(by="auto:agent_wallet_monitor")
    paper_pause.pause(by="owner")
    assert outgoing_pause.is_paused() is False
    assert custody_pause.is_paused() is False


def test_heartbeat_paper_tasks_honor_paper_pause(tmp_path, monkeypatch):
    """Same pattern as test_heartbeat_gate_resilience.py's _task() helper --
    verifies /off actually disables the heartbeat tasks it claims to,
    not just the websocket drain."""
    configure_data_dir(tmp_path)
    from aria_core import heartbeat

    def _task(task_id):
        match = [t for t in heartbeat.HEARTBEAT_TASKS if t.id == task_id]
        assert match, f"tache introuvable : {task_id}"
        return match[0]

    monkeypatch.setenv("ARIA_PAPER_TRADING_ENABLED", "1")
    heartbeat._sync_x_curiosity_enabled()
    assert _task("paper_trade_cycle").enabled is True
    assert _task("momentum_discovery_cycle").enabled is True
    assert _task("paper_weekly_review_cycle").enabled is True

    paper_pause.pause(by="owner")
    heartbeat._sync_x_curiosity_enabled()
    assert _task("paper_trade_cycle").enabled is False
    assert _task("momentum_discovery_cycle").enabled is False
    assert _task("paper_weekly_review_cycle").enabled is False

    paper_pause.resume(by="owner")
    heartbeat._sync_x_curiosity_enabled()
    assert _task("paper_trade_cycle").enabled is True


def test_momentum_websocket_drain_honors_paper_pause(tmp_path, monkeypatch):
    """The exact mechanism /off must control: the websocket drain must skip
    entirely once armed, distinct from and in addition to
    ARIA_PAPER_TRADING_ENABLED."""
    configure_data_dir(tmp_path)
    from aria_core import momentum_websocket

    monkeypatch.setenv("ARIA_PAPER_TRADING_ENABLED", "true")
    assert momentum_websocket._paper_trading_enabled() is True

    paper_pause.pause(by="owner")
    assert momentum_websocket._paper_trading_enabled() is False

    paper_pause.resume(by="owner")
    assert momentum_websocket._paper_trading_enabled() is True
