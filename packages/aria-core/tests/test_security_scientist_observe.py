"""Decisive gate test for Step 1 (DISCOVER) -- specs/019-security-scientist.

Real subprocess, real /proc -- mocking the instrument under test would let
this pass without proving it works against reality (research.md #9). Per the
approved plan's discipline: if this fails, nothing above this layer is worth
building yet."""
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


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(se, "DB_PATH", str(tmp_path / "security_scientist_test.db"))
    yield


def _spawn_disposable_venv_process(tmp_path):
    """A process outside any declared architecture: a fake venv (pyvenv.cfg,
    no lockfile) whose bin/python3 is a symlink to the real interpreter --
    exactly the aria-core/.venv incident shape."""
    venv_dir = tmp_path / "disposable-venv"
    (venv_dir / "bin").mkdir(parents=True)
    (venv_dir / "pyvenv.cfg").write_text("home = /usr/bin\n")
    fake_python = venv_dir / "bin" / "python3"
    os.symlink(sys.executable, fake_python)
    proc = subprocess.Popen([str(fake_python), "-c", "import time; time.sleep(20)"])
    return proc


async def test_disposable_process_is_discovered_and_classified_unknown_never_pass(tmp_path):
    proc = _spawn_disposable_venv_process(tmp_path)
    try:
        time.sleep(0.3)  # let it actually start before the pass reads /proc
        now = time.time()
        report = await sso.run_pass(now)
        live = report["live"]
        matching = [obs for sid, obs in live.items() if obs["pid"] == proc.pid]
        assert matching, "spawned process not found in the inventory pass"

        surface_id = next(sid for sid, obs in live.items() if obs["pid"] == proc.pid)
        state = await se.state_at(surface_id, now)
        assert state is not None
        assert state["status"] == sp.UNKNOWN, (
            f"a venv with no lockfile must never be PASS, got {state['status']}"
        )
    finally:
        proc.kill()
        proc.wait(timeout=5)


async def test_process_removal_is_recorded_not_silently_dropped(tmp_path):
    proc = _spawn_disposable_venv_process(tmp_path)
    time.sleep(0.3)
    now = time.time()
    report = await sso.run_pass(now)
    live = report["live"]
    surface_id = next(sid for sid, obs in live.items() if obs["pid"] == proc.pid)

    proc.kill()
    proc.wait(timeout=5)
    time.sleep(0.2)

    later = time.time()
    await sso.run_pass(later)

    rows = await se.list_observations(surface_id)
    assert any(r["payload"].get("still_present") is False for r in rows), (
        "the process's disappearance was never recorded -- it just vanished from view"
    )


def test_lockfile_in_venv_parent_directory_is_found(tmp_path):
    """Regression test for a real bug found by running against the live
    production host: the standard layout puts the lockfile in the venv
    directory's PARENT (<project>/.venv/ + <project>/pyproject.toml), not
    inside the venv itself -- checking only venv_root produced 5 false
    UNKNOWNs for aria-core/.venv and obv-ao-screener/.venv on the real VPS."""
    project_dir = tmp_path / "some-project"
    venv_dir = project_dir / ".venv"
    (venv_dir / "bin").mkdir(parents=True)
    (venv_dir / "pyvenv.cfg").write_text("home = /usr/bin\n")
    (project_dir / "pyproject.toml").write_text("[project]\nname = 'x'\n")

    facts = {"cmdline0": str(venv_dir / "bin" / "python3")}
    kind, lockfile_present = sso.classify_runtime(facts)
    assert kind == "python-venv"
    assert lockfile_present is True


def test_venv_with_no_lockfile_anywhere_is_flagged():
    facts = {"cmdline0": "/tmp/does-not-matter/.venv/bin/python3"}
    # pyvenv.cfg absent entirely -- classify_runtime must not assume python-venv
    kind, lockfile_present = sso.classify_runtime(facts)
    assert kind == "unrecognized"
    assert lockfile_present is False


async def test_judge_ignores_a_self_declared_safe_from_the_collector():
    """G1, the founding guarantee: even if a producer's raw payload contains
    an embedded conclusion, the derived status must never trust it."""
    now = time.time()
    obs_id = await se.record_observation(
        "proc-fake-9999",
        {"pid": 9999, "note": "looks fine"},  # no forbidden key -- this must pass through
        observer_version="v1",
        environment_identity={"hostname": "test"},
        observed_at=now,
    )
    assert obs_id is not None
    # The collector/critic chain, not the raw payload, decides -- unverified coverage => UNKNOWN.
    ev = sp.surface_coverage(
        "proc-fake-9999", discovered=True, observed=True, verified=False,
        detail="no lockfile", checked_at=now, max_age_seconds=3600,
    )
    assert ev.status == sp.UNKNOWN
