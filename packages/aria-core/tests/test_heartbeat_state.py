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


# ── aria_brain_cycle organic cadence (24/07, operator decision: "toute les
# 20h avec une marge de +/- 4h pour faire comme si elle ecrivait quand elle a
# le temp") ──────────────────────────────────────────────────────────────────


def test_aria_brain_effective_interval_always_within_16h_24h_window():
    """The jittered interval must always land in [16h, 24h] (960-1440 min),
    never outside the +/-4h window around the 20h center -- checked across
    many distinct seeds, not just one lucky draw."""
    for i in range(200):
        last_run = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=i)
        interval = hb._aria_brain_effective_interval_minutes(last_run)
        assert 960 <= interval <= 1440


def test_aria_brain_effective_interval_deterministic_for_same_last_run():
    """The SAME last_run must always yield the SAME jitter -- never re-rolled
    tick after tick while the cycle isn't due yet (that would make the wait
    erratic and could fire early on an unlucky tick)."""
    last_run = datetime(2026, 7, 23, 13, 45, tzinfo=timezone.utc)
    first = hb._aria_brain_effective_interval_minutes(last_run)
    second = hb._aria_brain_effective_interval_minutes(last_run)
    assert first == second


def test_aria_brain_effective_interval_varies_across_different_last_runs():
    """Non-regression against a degenerate implementation that would always
    return the exact center (1200) regardless of the seed."""
    intervals = {
        hb._aria_brain_effective_interval_minutes(
            datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=i)
        )
        for i in range(50)
    }
    assert len(intervals) > 1


def test_aria_brain_effective_interval_handles_never_run_before():
    interval = hb._aria_brain_effective_interval_minutes(None)
    assert 960 <= interval <= 1440


def test_aria_brain_cycle_not_due_before_16h_even_with_organic_jitter(monkeypatch, tmp_path):
    """Whatever the jitter, aria_brain_cycle must never fire before the
    shortest possible organic interval (16h) has elapsed."""
    state_path = tmp_path / "heartbeat_state.json"
    monkeypatch.setattr(hb, "_HEARTBEAT_STATE_PATH", state_path)

    last_run = datetime.now(timezone.utc) - timedelta(hours=15)
    hb._save_heartbeat_state({"aria_brain_cycle": last_run})
    interval = hb._aria_brain_effective_interval_minutes(last_run)
    assert hb._task_due("aria_brain_cycle", interval, {}) is False


def test_aria_brain_cycle_due_after_24h_regardless_of_organic_jitter(monkeypatch, tmp_path):
    """Whatever the jitter, aria_brain_cycle must always be due once the
    longest possible organic interval (24h) has elapsed."""
    state_path = tmp_path / "heartbeat_state.json"
    monkeypatch.setattr(hb, "_HEARTBEAT_STATE_PATH", state_path)

    last_run = datetime.now(timezone.utc) - timedelta(hours=25)
    hb._save_heartbeat_state({"aria_brain_cycle": last_run})
    interval = hb._aria_brain_effective_interval_minutes(last_run)
    assert hb._task_due("aria_brain_cycle", interval, {}) is True