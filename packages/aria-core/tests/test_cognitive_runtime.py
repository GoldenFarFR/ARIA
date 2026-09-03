"""Negative controls on the cognitive-runtime SessionStart hook.

Companion to docs/cerveau-epistemique-sessions.md and docs/regressions-cognitives.md.
The hook's whole reason to exist is that a document a model can choose to read is a
document it will eventually not read -- so every test here tries to catch the hook
claiming a guarantee (identity, freshness, drift detection) it did not actually
verify, the same discipline as test_security_posture.py.

Real bug fixed the same day these tests were written: the hook used to discard
stdin (`cat >/dev/null`) and read a `$CLAUDE_SESSION_ID` environment variable that
the harness never sets (confirmed against code.claude.com/docs/en/hooks: SessionStart
passes `session_id`/`source` as JSON on stdin, never as an env var -- no other hook
in this project uses CLAUDE_SESSION_ID). Every trace line said "session=unknown",
so the drift-detection line -- which compares against the last line NOT from the
current session -- silently never found anything to compare against. Test 7 below
locks that fix so the regression class cannot come back.
"""

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[3] / ".claude" / "hooks" / "cognitive-runtime.sh"
REAL_BRAIN = Path(__file__).resolve().parents[3] / "docs" / "cerveau-epistemique-sessions.md"
REAL_REGRESSIONS = Path(__file__).resolve().parents[3] / "docs" / "regressions-cognitives.md"


def run_hook(*, stdin_json: dict | None = None, env_overrides: dict | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.update(env_overrides or {})
    payload = json.dumps(stdin_json) if stdin_json is not None else ""
    return subprocess.run(
        ["bash", str(HOOK)],
        input=payload,
        capture_output=True,
        text=True,
        env=env,
        timeout=5,
    )


@pytest.fixture()
def isolated_trace_dir(tmp_path) -> Path:
    d = tmp_path / "cognitive-runtime-trace"
    d.mkdir()
    return d


def test_hook_with_truly_closed_stdin_returns_valid_json_fast(isolated_trace_dir):
    # This is the exact scenario that hung for 2 minutes before this fix: no JSON
    # at all on stdin, just immediate EOF (`bash cognitive-runtime.sh </dev/null`).
    # A test that instead sends `{}` (see below) would pass even if the dead
    # `cat >/dev/null` that caused the original hang were still there, since a
    # real JSON body also reaches EOF -- an empty string is the case that used to
    # block forever without one.
    start = time.monotonic()
    result = run_hook(
        stdin_json=None,
        env_overrides={"COGNITIVE_RUNTIME_TRACE_DIR_OVERRIDE": str(isolated_trace_dir)},
    )
    elapsed = time.monotonic() - start
    assert result.returncode == 0
    assert elapsed < 1.0, f"hook took {elapsed:.3f}s, expected under 1s"
    payload = json.loads(result.stdout)
    assert payload["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "additionalContext" in payload["hookSpecificOutput"]


def test_hook_with_minimal_json_stdin_returns_valid_json_fast(isolated_trace_dir):
    # Distinct from the EOF case above: a well-formed but empty JSON object, the
    # shape a real SessionStart call would never actually send (it always carries
    # session_id/source) but that jq must still degrade out of gracefully.
    start = time.monotonic()
    result = run_hook(
        stdin_json={},
        env_overrides={"COGNITIVE_RUNTIME_TRACE_DIR_OVERRIDE": str(isolated_trace_dir)},
    )
    elapsed = time.monotonic() - start
    assert result.returncode == 0
    assert elapsed < 1.0, f"hook took {elapsed:.3f}s, expected under 1s"
    payload = json.loads(result.stdout)
    assert payload["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "additionalContext" in payload["hookSpecificOutput"]


def test_payload_stays_under_2kb_budget(isolated_trace_dir):
    result = run_hook(
        stdin_json={"session_id": "budget-test", "source": "startup"},
        env_overrides={"COGNITIVE_RUNTIME_TRACE_DIR_OVERRIDE": str(isolated_trace_dir)},
    )
    ctx = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    size = len(ctx.encode("utf-8"))
    assert size < 2048, f"payload is {size} bytes, over the ~2 Ko operator budget"


def test_hash_in_trace_matches_independently_computed_sha256(isolated_trace_dir):
    real_hash = hashlib.sha256(REAL_BRAIN.read_bytes()).hexdigest()[:12]
    result = run_hook(
        stdin_json={"session_id": "hash-test", "source": "startup"},
        env_overrides={"COGNITIVE_RUNTIME_TRACE_DIR_OVERRIDE": str(isolated_trace_dir)},
    )
    ctx = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert real_hash in ctx, "the hash injected into context must match the real file, never a hardcoded value"

    trace_line = (isolated_trace_dir / "loaded.log").read_text().strip()
    assert f"hash={real_hash}" in trace_line


def test_drift_detected_when_brain_content_changes(tmp_path, isolated_trace_dir):
    fixture_brain = tmp_path / "brain-fixture.md"
    fixture_brain.write_text(
        "<!-- BRAIN-PROTOCOL: ARIA-EPISTEMIC v1.0 -->\n## Section one\n", encoding="utf-8"
    )
    overrides = {
        "COGNITIVE_RUNTIME_BRAIN_OVERRIDE": str(fixture_brain),
        "COGNITIVE_RUNTIME_REGRESSIONS_OVERRIDE": str(REAL_REGRESSIONS),
        "COGNITIVE_RUNTIME_TRACE_DIR_OVERRIDE": str(isolated_trace_dir),
    }

    first = run_hook(stdin_json={"session_id": "session-A", "source": "startup"}, env_overrides=overrides)
    first_ctx = json.loads(first.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "ATTENTION" not in first_ctx, "no prior session recorded yet -- nothing to drift from"

    # Never mutate the real brain for this test -- only the fixture copy.
    fixture_brain.write_text(
        "<!-- BRAIN-PROTOCOL: ARIA-EPISTEMIC v1.0 -->\n## Section one\n## Section two (added)\n",
        encoding="utf-8",
    )
    second = run_hook(stdin_json={"session_id": "session-B", "source": "startup"}, env_overrides=overrides)
    second_ctx = json.loads(second.stdout)["hookSpecificOutput"]["additionalContext"]

    first_hash = hashlib.sha256(
        "<!-- BRAIN-PROTOCOL: ARIA-EPISTEMIC v1.0 -->\n## Section one\n".encode()
    ).hexdigest()[:12]
    second_hash = hashlib.sha256(fixture_brain.read_bytes()).hexdigest()[:12]
    assert first_hash != second_hash, "test fixture setup error: the two versions must hash differently"
    assert "ATTENTION" in second_ctx and "CHANGE" in second_ctx
    assert first_hash in second_ctx and second_hash in second_ctx


def test_two_executions_append_two_distinct_lines(isolated_trace_dir):
    # Behavioral counterpart to test_session_id_is_read_from_stdin_json_never_
    # hardcoded_or_env_var below: that test locks the CODE (no CLAUDE_SESSION_ID,
    # jq present); this one proves the session_id actually PROPAGATES end to end
    # into the persisted trace line, in a real subprocess, not just a code shape.
    first = run_hook(stdin_json={"session_id": "s1", "source": "startup"},
                      env_overrides={"COGNITIVE_RUNTIME_TRACE_DIR_OVERRIDE": str(isolated_trace_dir)})
    second = run_hook(stdin_json={"session_id": "s2", "source": "resume"},
                       env_overrides={"COGNITIVE_RUNTIME_TRACE_DIR_OVERRIDE": str(isolated_trace_dir)})
    assert first.returncode == 0, f"first hook failed: {first.stderr}"
    assert second.returncode == 0, f"second hook failed: {second.stderr}"
    lines = (isolated_trace_dir / "loaded.log").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2, "each successful bootstrap must append, never overwrite"
    assert lines[0] != lines[1]
    assert "session=s1" in lines[0]
    assert "session=s2" in lines[1]


def test_missing_brain_is_fail_visible_not_silent(tmp_path, isolated_trace_dir):
    missing = tmp_path / "does-not-exist.md"
    result = run_hook(
        stdin_json={"session_id": "missing-brain-test"},
        env_overrides={
            "COGNITIVE_RUNTIME_BRAIN_OVERRIDE": str(missing),
            "COGNITIVE_RUNTIME_TRACE_DIR_OVERRIDE": str(isolated_trace_dir),
        },
    )
    assert result.returncode == 0, "a missing brain must never block the session"
    ctx = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "COGNITIVE RUNTIME INDISPONIBLE" in ctx
    # Fail-visible must not silently write a trace line implying a real bootstrap happened.
    assert not (isolated_trace_dir / "loaded.log").exists()


def test_session_id_is_read_from_stdin_json_never_hardcoded_or_env_var():
    # COGNITIVE-011 note to self: a plain text search would also match the
    # explanatory comment above, in the script, that documents why this
    # regression was fixed -- exactly the "instrument accuses the implementation"
    # failure mode already on file in docs/regressions-cognitives.md. Only
    # executed (non-comment) lines count as a real regression.
    executed = [
        line for line in HOOK.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    offenders = [line for line in executed if "CLAUDE_SESSION_ID" in line]
    assert not offenders, (
        f"regression lock: the harness never sets this env var (verified against "
        f"code.claude.com/docs/en/hooks) -- every trace line silently said "
        f"session=unknown and drift detection never had anything to compare "
        f"against: {offenders}"
    )
    assert any("session_id" in line for line in executed), (
        "session identity must be extracted from the SessionStart stdin JSON"
    )
    assert any("jq" in line for line in executed), (
        "session identity parsing must use the hook's JSON input"
    )
