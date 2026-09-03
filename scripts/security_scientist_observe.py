#!/usr/bin/env python3
"""Step 1 (DISCOVER) collector for the Security Scientist (specs/019).

Reads /proc directly, stdlib only (no psutil -- not a project dependency,
see research.md #1). Classifies each live process as a Python-venv runtime
(the founding incident's exact shape) or "unrecognized" (recorded, never
dropped -- FR-003) using argv[0]/cmdline[0], NOT the `exe` symlink target
(verified live that venv python binaries are symlinks to the base
interpreter, so `exe` cannot identify which venv launched a process --
research.md #3's empirical correction).

Anti-loop invariant (plan.md refinement #8): this module's only write target
is its own evidence ledger (aria_core.security_evidence) -- no lockfile,
process, or config file it inspects is ever touched.
"""
from __future__ import annotations

import hashlib
import os
import platform
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import security_posture as sp  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages" / "aria-core" / "src"))
from aria_core import security_evidence as se  # noqa: E402

import security_scientist_contradictions as classify  # noqa: E402
import security_scientist_critic as critic  # noqa: E402
import security_scientist_judge as judge  # noqa: E402
from security_scientist_types import RuntimeObservation  # noqa: E402

# CLASSIFY's source pair -- module-level so tests can monkeypatch them to
# inject a controlled A/B/C scenario, exactly like DB_PATH is monkeypatched
# elsewhere. Production always gets classify.py's own real-repo defaults.
CLASSIFY_CLAUDE_MD = classify.DEFAULT_CLAUDE_MD
CLASSIFY_CODE_ROOTS = classify.DEFAULT_CODE_ROOTS

COLLECTOR_VERSION = "security_scientist_observe.py/v1"
MAX_AGE_SECONDS = 3600  # a runtime classification older than this is STALE, not PASS
HEARTBEAT_MAX_AGE_SECONDS = 900  # the scheduler itself must be heard from at least every 15min

_CLK_TCK = os.sysconf("SC_CLK_TCK")

_VENV_PYTHON_RE = re.compile(r"^(.*)/bin/python[\d.]*$")
_LOCKFILES = ("requirements.txt", "pyproject.toml", "uv.lock", "poetry.lock")


def environment_identity() -> dict:
    return {
        "hostname": platform.node(),
        "kernel_release": platform.release(),
        "python_version": platform.python_version(),
        "collector_version": COLLECTOR_VERSION,
    }


def boot_time() -> float:
    """Absolute unix timestamp of system boot -- the reference point T_detect
    is measured against (process start times in /proc are clock ticks since
    boot, not wall-clock)."""
    for line in Path("/proc/stat").read_text().splitlines():
        if line.startswith("btime "):
            return float(line.split()[1])
    raise RuntimeError("btime not found in /proc/stat")


def process_start_absolute(start_time_ticks: str, boot: float) -> float:
    return boot + (int(start_time_ticks) / _CLK_TCK)


def _read_start_time(pid: int) -> str:
    """/proc/<pid>/stat field 22 -- part of the surface_id so a reused PID
    never gets confused with the process that previously held it."""
    raw = Path(f"/proc/{pid}/stat").read_text()
    after_comm = raw.rsplit(")", 1)[1].split()
    return after_comm[19]  # field 22 (starttime), 0-indexed from state=field3


def collect_live_processes() -> dict[str, dict]:
    """One dict per currently-running PID, keyed by a stable surface_id.
    Raw facts only -- no classification, no verdict (Collector, not Critic/Judge)."""
    result: dict[str, dict] = {}
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        pid = int(entry)
        try:
            cmdline_raw = Path(f"/proc/{pid}/cmdline").read_bytes()
            if not cmdline_raw:
                continue
            cmdline0 = cmdline_raw.split(b"\x00")[0].decode("utf-8", "replace")
            exe = os.readlink(f"/proc/{pid}/exe")
            cwd = os.readlink(f"/proc/{pid}/cwd")
            start_time = _read_start_time(pid)
        except (FileNotFoundError, ProcessLookupError, PermissionError, OSError):
            continue  # process exited mid-read, or unreadable -- never fabricate its facts

        surface_id = f"proc-{pid}-{start_time}"
        result[surface_id] = {
            "pid": pid,
            "cmdline0": cmdline0,
            "exe": exe,
            "cwd": cwd,
            "start_time": start_time,
            "argv_fingerprint": hashlib.sha256(cmdline_raw).hexdigest()[:16],
        }
    return result


def classify_runtime(facts: dict) -> tuple[str, bool]:
    """Returns (runtime_kind, lockfile_present). Only judges what KIND of
    runtime this is -- never a safe/unsafe verdict, that's the Judge's job.

    Bug found and fixed by testing against the real host before trusting
    this: the standard layout is <project>/.venv/ + <project>/pyproject.toml
    -- the lockfile lives in the venv directory's PARENT, never inside the
    venv directory itself. Checking only `venv_root` produced 5 false
    UNKNOWNs on the real production host (aria-core/.venv,
    obv-ao-screener/.venv) whose real lockfiles were one level up. Both
    locations are checked so a venv that genuinely embeds its own manifest
    (non-standard layout) is still caught."""
    match = _VENV_PYTHON_RE.match(facts["cmdline0"])
    if not match:
        return "unrecognized", False
    venv_root = Path(match.group(1))
    if not (venv_root / "pyvenv.cfg").exists():
        return "unrecognized", False
    candidates = [venv_root, venv_root.parent]
    lockfile_present = any(
        (candidate / name).exists() for candidate in candidates for name in _LOCKFILES
    )
    return "python-venv", lockfile_present


async def run_pass(now: float) -> dict[str, dict]:
    """One full discovery pass: records the observer's own heartbeat
    (research.md #12), discovers every live process, evaluates each via the
    shared security_posture contract, and records any disappearance since
    the last pass rather than silently dropping it (FR-003)."""
    env = environment_identity()

    await se.record_observation(
        se.INVENTORY_SURFACE_ID, {"invocation": "ok"},
        observer_version=COLLECTOR_VERSION, environment_identity=env, observed_at=now,
    )

    live = collect_live_processes()
    known_surfaces = set(await se.list_surface_ids("proc-"))
    boot = boot_time()

    for surface_id, facts in live.items():
        payload = dict(facts)
        if surface_id not in known_surfaces:
            # First sighting of this surface -- measure the real detection
            # delay against the process's actual start time (T_detect), not
            # a claim. Recorded as a raw fact (a duration is a measurement,
            # never a security conclusion -- G1 is unaffected).
            t0 = process_start_absolute(facts["start_time"], boot)
            payload["t_detect_seconds"] = max(0.0, now - t0)

        obs_id = await se.record_observation(
            surface_id, payload, observer_version=COLLECTOR_VERSION,
            environment_identity=env, observed_at=now,
        )
        observation = RuntimeObservation(surface_id=surface_id, **facts)
        kind, lockfile_present = classify_runtime(facts)
        verified = kind == "python-venv" and lockfile_present

        # Independent critique -- NOT computed here, NOT trusted from the
        # collector's own opinion. security_scientist_critic re-reads the
        # process's identity itself (research.md #8/plan.md refinement #4).
        critique_result = critic.critique(observation)
        evidence = judge.judge(
            observation, critique_result, verified=verified,
            now=now, max_age_seconds=MAX_AGE_SECONDS,
        )
        await se.record_evaluation(
            surface_id, evidence.status,
            observations_used=[obs_id] if obs_id else [],
            self_critique_id=f"{surface_id}@{now}",
            detail=f"runtime_kind={kind}, lockfile_present={lockfile_present}; {evidence.detail}",
            evaluated_at=now,
        )

    disappeared = known_surfaces - set(live.keys())
    for surface_id in disappeared:
        rows = await se.list_observations(surface_id)
        if rows and rows[-1]["payload"].get("still_present") is False:
            continue  # already recorded, never re-record the same removal
        await se.record_observation(
            surface_id, {"still_present": False},
            observer_version=COLLECTOR_VERSION, environment_identity=env, observed_at=now,
        )

    # CLASSIFY runs LAST in the cycle, as its own parallel output -- never a
    # second input to the Judge (the process evaluations above are already
    # final by this point). A CLASSIFY failure is caught here, never left to
    # crash the whole cycle after DISCOVER/JUDGE already did real, valuable
    # work -- but it IS reflected in the report so cycle_ok() can catch it.
    try:
        classify_result = await classify.record_pass_from_files(
            claude_md_path=CLASSIFY_CLAUDE_MD, code_root=CLASSIFY_CODE_ROOTS, now=now,
        )
    except Exception as exc:  # noqa: BLE001 -- any CLASSIFY failure must be visible, never crash DISCOVER
        classify_result = {"status": "ERROR", "contradictions_found": 0, "reason": str(exc)}

    return {"live": live, "classify": classify_result}


def cycle_ok(report: dict) -> bool:
    """Whether the FULL cycle (not just DISCOVER) may be reported as a
    healthy pass -- CLASSIFY failing (ERROR or UNKNOWN) makes the whole
    cycle non-PASS, exactly like a DISCOVER crash would, so run.sh's
    heartbeat surfaces it rather than reporting success on partial work."""
    return report["classify"]["status"] == "OK"


async def record_heartbeat(now: float, *, ok: bool, exit_code: int, duration_seconds: float) -> sp.Evidence:
    """Records the SCHEDULER's own liveness -- called by run.sh AFTER
    attempting a cycle, with the REAL outcome (success or failure), never
    written optimistically before the attempt (property #2: a failed cycle
    must never look identical to a successful one). This is a simple
    deterministic fact (did the wrapper's invocation succeed), so it goes
    through security_posture.measured() directly -- the same pattern already
    justified in plan.md's Complexity Tracking for security_posture_collect.
    py's existing 4 checks, not the process-specific Collector/Critic/Judge
    chain (property #3: the scheduler still never decides its OWN verdict --
    the shared, already-proven security_posture contract does)."""
    env = environment_identity()
    payload = {
        "invocation": "ok" if ok else "failed",
        "exit_code": exit_code,
        "duration_seconds": duration_seconds,
    }
    obs_id = await se.record_observation(
        se.OBSERVER_SURFACE_ID, payload, observer_version=COLLECTOR_VERSION,
        environment_identity=env, observed_at=now,
    )
    evidence = sp.measured(
        se.OBSERVER_SURFACE_ID, ok,
        f"cycle {'succeeded' if ok else 'FAILED'} (exit={exit_code}, {duration_seconds:.1f}s)",
        checked_at=now, max_age_seconds=HEARTBEAT_MAX_AGE_SECONDS, source="run.sh",
    )
    await se.record_evaluation(
        se.OBSERVER_SURFACE_ID, evidence.status,
        observations_used=[obs_id] if obs_id else [],
        self_critique_id=f"heartbeat@{now}", detail=evidence.detail, evaluated_at=now,
    )
    return evidence


async def main() -> int:
    import argparse
    import json

    ap = argparse.ArgumentParser()
    ap.add_argument("--heartbeat", action="store_true", help="record scheduler liveness, don't run a pass")
    ap.add_argument("--ok", action="store_true")
    ap.add_argument("--exit-code", type=int, default=0)
    ap.add_argument("--duration-seconds", type=float, default=0.0)
    args = ap.parse_args()

    now = time.time()
    if args.heartbeat:
        evidence = await record_heartbeat(
            now, ok=args.ok, exit_code=args.exit_code, duration_seconds=args.duration_seconds,
        )
        print(json.dumps({"observer_status": evidence.status}, indent=2))
        return 0

    cycle = await run_pass(now)
    report = {
        "generated_at": now,
        "processes_seen": len(cycle["live"]),
        "gap_seconds": await se.last_discovery_pass_age(now),
        "classify": cycle["classify"],
    }
    print(json.dumps(report, indent=2))
    return 0 if cycle_ok(cycle) else 2


if __name__ == "__main__":
    import asyncio

    raise SystemExit(asyncio.run(main()))
