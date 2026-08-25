"""Shadow no-new-positions switch (25/08) -- /offshadowtrades and
/onshadowtrades on Telegram. Read by the 3 shadow pockets themselves
(base_momentum_shadow.py, robinhood_pump_shadow.py,
solana_late_bonding_shadow.py) as the last filter before opening a new
position -- scoped narrower than shadow_pause (which cancels every loop).
"""
import json

from aria_core import shadow_discovery_only
from aria_core.paths import configure_data_dir


def test_default_not_discovery_only(tmp_path):
    configure_data_dir(tmp_path)
    assert shadow_discovery_only.is_discovery_only() is False
    st = shadow_discovery_only.status()
    assert st["discovery_only"] is False
    assert st["since"] is None


def test_arm_then_disarm(tmp_path):
    configure_data_dir(tmp_path)
    shadow_discovery_only.arm(by=12345, reason="recalibration in progress")
    assert shadow_discovery_only.is_discovery_only() is True
    st = shadow_discovery_only.status()
    assert st["discovery_only"] is True
    assert st["by"] == 12345
    assert st["reason"] == "recalibration in progress"
    assert st["since"] is not None

    shadow_discovery_only.disarm(by=12345)
    assert shadow_discovery_only.is_discovery_only() is False
    assert shadow_discovery_only.status()["since"] is None


def test_state_persists_on_disk_own_file(tmp_path):
    configure_data_dir(tmp_path)
    shadow_discovery_only.arm(by=1)
    state_file = tmp_path / "shadow_discovery_only_state.json"
    assert state_file.exists()
    assert not (tmp_path / "shadow_pause_state.json").exists()
    assert not (tmp_path / "paper_pause_state.json").exists()
    data = json.loads(state_file.read_text(encoding="utf-8"))
    assert data["discovery_only"] is True


def test_corrupt_file_fails_open(tmp_path):
    """Same doctrine as shadow_pause: this guards zero capital, so an
    unreadable state must never starve every pocket's sample over a
    corrupted debug toggle."""
    configure_data_dir(tmp_path)
    (tmp_path / "shadow_discovery_only_state.json").write_text("{ not valid json", encoding="utf-8")
    assert shadow_discovery_only.is_discovery_only() is False
    assert shadow_discovery_only.status()["readable"] is False


def test_never_shares_state_with_shadow_pause(tmp_path):
    from aria_core import shadow_pause

    configure_data_dir(tmp_path)
    shadow_pause.pause(by="owner")
    assert shadow_discovery_only.is_discovery_only() is False  # shadow_pause never touches this one

    shadow_pause.resume(by="owner")
    shadow_discovery_only.arm(by="owner")
    assert shadow_pause.is_paused() is False
