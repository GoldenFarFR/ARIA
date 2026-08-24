"""Shadow-process kill-switch (24/08) -- /offshadow and /onshadow on
Telegram. Read by the standalone shadow process's own supervisor
(``shadow_persistent.py``, outside this repo, systemd service
``aria-shadow-persistent``), not by anything inside this package -- so this
suite covers the module's own contract only, not the supervisor's cancel
behavior (that lives in a file this test suite cannot import).
"""
import json

from aria_core import shadow_pause
from aria_core.paths import configure_data_dir


def test_default_not_paused(tmp_path):
    configure_data_dir(tmp_path)
    assert shadow_pause.is_paused() is False
    st = shadow_pause.pause_status()
    assert st["paused"] is False
    assert st["since"] is None


def test_pause_then_resume(tmp_path):
    configure_data_dir(tmp_path)
    shadow_pause.pause(by=12345, reason="debug session")
    assert shadow_pause.is_paused() is True
    st = shadow_pause.pause_status()
    assert st["paused"] is True
    assert st["by"] == 12345
    assert st["reason"] == "debug session"
    assert st["since"] is not None

    shadow_pause.resume(by=12345)
    assert shadow_pause.is_paused() is False
    assert shadow_pause.pause_status()["since"] is None


def test_state_persists_on_disk_own_file(tmp_path):
    configure_data_dir(tmp_path)
    shadow_pause.pause(by=1)
    state_file = tmp_path / "shadow_pause_state.json"
    assert state_file.exists()
    assert not (tmp_path / "paper_pause_state.json").exists()
    assert not (tmp_path / "x_pause_state.json").exists()
    assert not (tmp_path / "custody_pause_state.json").exists()
    assert not (tmp_path / "pause_state.json").exists()
    data = json.loads(state_file.read_text(encoding="utf-8"))
    assert data["paused"] is True


def test_corrupt_file_fails_open(tmp_path):
    """Same doctrine as paper_pause: a shadow pocket guards zero capital, so
    an unreadable state must never freeze data collection over a corrupted
    debug toggle."""
    configure_data_dir(tmp_path)
    (tmp_path / "shadow_pause_state.json").write_text("{ not valid json", encoding="utf-8")
    assert shadow_pause.is_paused() is False
    assert shadow_pause.pause_status()["readable"] is False


def test_never_shares_state_with_other_pauses(tmp_path):
    from aria_core import custody_pause, outgoing_pause, paper_pause, x_pause

    configure_data_dir(tmp_path)
    outgoing_pause.pause(by="manual-owner")
    custody_pause.pause(by="auto:agent_wallet_monitor")
    paper_pause.pause(by="owner")
    x_pause.pause(by="owner")
    assert shadow_pause.is_paused() is False  # none of the other four touch this one

    outgoing_pause.resume(by="manual-owner")
    custody_pause.resume(by="auto:agent_wallet_monitor")
    paper_pause.resume(by="owner")
    x_pause.resume(by="owner")
    shadow_pause.pause(by="owner")
    assert outgoing_pause.is_paused() is False
    assert custody_pause.is_paused() is False
    assert paper_pause.is_paused() is False
    assert x_pause.is_paused() is False
