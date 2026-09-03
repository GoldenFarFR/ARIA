"""Scheduler tests (specs/019-security-scientist): T_detect measured against
reality, a failed cycle recorded rather than silent, and silence never
collapsing into PASS. The last test actually executes run.sh -- not a
description of what it should do."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
import security_posture as sp  # noqa: E402
import security_scientist_observe as sso  # noqa: E402

from aria_core import security_evidence as se  # noqa: E402

RUN_SH = Path("/opt/aria-data/security-scientist-watch/run.sh")


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(se, "DB_PATH", str(tmp_path / "security_scientist_test.db"))
    yield


def _spawn_disposable_venv_process(tmp_path):
    venv_dir = tmp_path / "disposable-venv"
    (venv_dir / "bin").mkdir(parents=True)
    (venv_dir / "pyvenv.cfg").write_text("home = /usr/bin\n")
    fake_python = venv_dir / "bin" / "python3"
    os.symlink(sys.executable, fake_python)
    return subprocess.Popen([str(fake_python), "-c", "import time; time.sleep(20)"])


# ---------------------------------------------------------------------------
# Decisive test 1: T_detect measured against a real process's real start
# time, not asserted -- the observation history must contain both the
# appearance and a real, small, non-negative delay.
# ---------------------------------------------------------------------------

async def test_t_detect_is_measured_against_the_processs_real_start_time(tmp_path):
    proc = _spawn_disposable_venv_process(tmp_path)
    try:
        time.sleep(0.5)
        now = time.time()
        report = await sso.run_pass(now)
        live = report["live"]
        surface_id = next(sid for sid, obs in live.items() if obs["pid"] == proc.pid)

        rows = await se.list_observations(surface_id)
        assert len(rows) == 1, "the first sighting must be exactly one observation"
        t_detect = rows[0]["payload"].get("t_detect_seconds")
        assert t_detect is not None, "T_detect was never measured on first sighting"
        assert 0 <= t_detect < 30, f"implausible T_detect: {t_detect}s"
    finally:
        proc.kill()
        proc.wait(timeout=5)


async def test_t_detect_is_only_measured_once_per_surface_not_every_pass(tmp_path):
    proc = _spawn_disposable_venv_process(tmp_path)
    try:
        time.sleep(0.3)
        first_pass = time.time()
        report = await sso.run_pass(first_pass)
        live = report["live"]
        surface_id = next(sid for sid, obs in live.items() if obs["pid"] == proc.pid)

        time.sleep(0.3)
        await sso.run_pass(time.time())

        rows = await se.list_observations(surface_id)
        assert len(rows) == 2
        assert "t_detect_seconds" in rows[0]["payload"]
        assert "t_detect_seconds" not in rows[1]["payload"], (
            "T_detect re-measured on a pass where the surface was already known"
        )
    finally:
        proc.kill()
        proc.wait(timeout=5)


# ---------------------------------------------------------------------------
# Decisive test 2: a cycle made unavailable/failed must never look like PASS.
# ---------------------------------------------------------------------------

async def test_a_failed_cycle_is_recorded_never_pass():
    now = time.time()
    evidence = await sso.record_heartbeat(now, ok=False, exit_code=124, duration_seconds=60.0)
    assert evidence.status != sp.PASS
    assert evidence.status == sp.FAIL  # a captured, proven failure -- not a guess

    status = await se.reported_status(se.OBSERVER_SURFACE_ID, now)
    assert status != sp.PASS

    rows = await se.list_observations(se.OBSERVER_SURFACE_ID)
    assert rows[-1]["payload"]["exit_code"] == 124, "the real exit code must be visible, not silent"


async def test_a_surface_with_zero_observations_ever_is_unobserved_never_pass():
    """Total silence -- the scheduler never even ran for this surface --
    must never be indistinguishable from 'proven safe.'"""
    status = await se.reported_status("proc-never-seen-1234", time.time())
    assert status == sp.UNOBSERVED
    assert status != sp.PASS
    assert status != sp.UNKNOWN  # "never observed" is a stronger claim than "observed, unproven"


async def test_a_successful_heartbeat_after_a_failure_recovers_the_surface():
    now = time.time()
    await sso.record_heartbeat(now, ok=False, exit_code=1, duration_seconds=5.0)
    assert await se.reported_status(se.OBSERVER_SURFACE_ID, now) != sp.PASS

    later = now + 1
    await sso.record_heartbeat(later, ok=True, exit_code=0, duration_seconds=2.0)
    assert await se.reported_status(se.OBSERVER_SURFACE_ID, later) == sp.PASS


# ---------------------------------------------------------------------------
# End-to-end: actually execute run.sh, not a description of what it should
# do. Isolated from production DATA_DIR/system_issues DB via env overrides.
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not RUN_SH.exists(), reason="run.sh not deployed on this host")
def test_run_sh_executes_a_real_cycle_and_records_a_real_heartbeat(tmp_path):
    watch_dir = tmp_path / "watch"
    data_dir = tmp_path / "data"
    issues_db = tmp_path / "issues.db"
    watch_dir.mkdir(exist_ok=True)
    data_dir.mkdir(exist_ok=True)
    issues_db.touch()  # production's aria.db always pre-exists; mirror that here

    env = dict(os.environ)
    env["DATA_DIR"] = str(data_dir)
    env["SECURITY_SCIENTIST_WATCH_DIR"] = str(watch_dir)
    env["SECURITY_SCIENTIST_ISSUES_DB"] = str(issues_db)

    result = subprocess.run(["bash", str(RUN_SH)], env=env, capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, f"run.sh itself must exit 0 even on a failed cycle: {result.stderr}"

    log_file = watch_dir / "run.log"
    assert log_file.exists(), "run.sh must log every cycle attempt"
    log_content = log_file.read_text()
    assert "exit=" in log_content

    db_file = data_dir / "security_scientist.db"
    assert db_file.exists(), "the real Python collector must have run and created its ledger"


@pytest.mark.skipif(not RUN_SH.exists(), reason="run.sh not deployed on this host")
def test_run_sh_records_a_real_failure_never_pass_when_the_collector_breaks(tmp_path):
    """Decisive test: make the scheduler itself unavailable for one cycle
    (a broken collector, simulating a crash) and confirm run.sh reports it
    honestly -- never silence, never PASS."""
    watch_dir = tmp_path / "watch"
    data_dir = tmp_path / "data"
    issues_db = tmp_path / "issues.db"
    watch_dir.mkdir(exist_ok=True)
    data_dir.mkdir(exist_ok=True)

    broken_collector = tmp_path / "broken_collector.py"
    broken_collector.write_text("import sys\nsys.exit(3)\n")
    issues_db.touch()  # production's aria.db always pre-exists; mirror that here

    env = dict(os.environ)
    env["DATA_DIR"] = str(data_dir)
    env["SECURITY_SCIENTIST_WATCH_DIR"] = str(watch_dir)
    env["SECURITY_SCIENTIST_ISSUES_DB"] = str(issues_db)
    env["SECURITY_SCIENTIST_COLLECTOR"] = str(broken_collector)

    result = subprocess.run(["bash", str(RUN_SH)], env=env, capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, "run.sh must exit 0 even when the cycle it ran failed"

    log_content = (watch_dir / "run.log").read_text()
    assert "exit=3" in log_content, "the real exit code must be visible in the log"

    db_file = data_dir / "security_scientist.db"
    import sqlite3

    conn = sqlite3.connect(str(db_file))
    row = conn.execute(
        "SELECT status FROM security_evaluations WHERE surface_id='security-scientist-observer' "
        "ORDER BY evaluated_at DESC LIMIT 1"
    ).fetchone()
    conn.close()
    assert row is not None, "the failure must still be recorded as an evaluation"
    assert row[0] != "PASS"

    issues_conn = sqlite3.connect(str(issues_db))
    issue_row = issues_conn.execute(
        "SELECT status FROM system_issues WHERE source='security-scientist-watch' AND dedup_key='cycle-failure'"
    ).fetchone()
    issues_conn.close()
    assert issue_row is not None and issue_row[0] == "open", (
        "a real cycle failure must surface as an open system_issues row, not silently"
    )
