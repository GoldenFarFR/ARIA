"""Collector/Critic/Judge separation -- the four decisive tests before the
scheduler (specs/019-security-scientist). Each one tries to manufacture an
illegitimate PASS; the whole point of this file is that none of them can.
"""
from __future__ import annotations

import ast
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
import security_posture as sp  # noqa: E402
import security_scientist_critic as critic  # noqa: E402
import security_scientist_judge as judge  # noqa: E402
from security_scientist_types import RuntimeObservation, SelfCritique  # noqa: E402

SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "scripts"


def _observation(**overrides) -> RuntimeObservation:
    base = dict(
        pid=4242, surface_id="proc-4242-1000", cmdline0="/opt/x/.venv/bin/python3",
        exe="/usr/bin/python3.14", cwd="/opt/x", argv_fingerprint="abc123", start_time="1000",
    )
    base.update(overrides)
    return RuntimeObservation(**base)


def _clean_critique(**overrides) -> SelfCritique:
    base = dict(
        coverage_complete=True, runtime_identity_verified=True, lookahead_checked=True,
        measurement_independence_checked=True, instrument_integrity_checked=True,
        hypothesis_scope_checked=True, reproducibility_checked=True,
    )
    base.update(overrides)
    return SelfCritique(**base)


# ---------------------------------------------------------------------------
# Test 1 -- a lying collector cannot reach the verdict.
# ---------------------------------------------------------------------------

def test_collector_cannot_pass_a_conclusion_to_the_judge():
    """G1, enforced by the type system: judge()'s signature has no parameter
    through which a producer-supplied conclusion could enter."""
    obs = _observation()
    crit = _clean_critique()
    with pytest.raises(TypeError):
        judge.judge(obs, crit, producer_conclusion="SAFE", verified=True, now=time.time())  # type: ignore[call-arg]


def test_observation_type_structurally_cannot_carry_a_conclusion():
    """A collector literally cannot construct an observation with an extra
    conclusion-shaped field -- the frozen dataclass has no such slot."""
    with pytest.raises(TypeError):
        RuntimeObservation(  # type: ignore[call-arg]
            pid=1, surface_id="proc-1-1", cmdline0="x", exe="x", cwd="x",
            argv_fingerprint="x", start_time="1", producer_conclusion="SAFE",
        )


# ---------------------------------------------------------------------------
# Test 2 -- incomplete critic coverage forces UNKNOWN, whatever else looks clean.
# ---------------------------------------------------------------------------

def test_incomplete_coverage_forces_unknown_even_with_everything_else_perfect():
    obs = _observation()
    crit = _clean_critique(coverage_complete=False)
    evidence = judge.judge(obs, crit, verified=True, now=time.time())
    assert evidence.status == sp.UNKNOWN


def test_unresolved_none_field_is_treated_the_same_as_an_explicit_failure():
    """None (never checked) must never be a permissive default -- same
    consequence as an explicit False."""
    obs = _observation()
    crit = _clean_critique(reproducibility_checked=None)
    evidence = judge.judge(obs, crit, verified=True, now=time.time())
    assert evidence.status == sp.UNKNOWN


# ---------------------------------------------------------------------------
# Test 3 -- measured identity != executing identity blocks PASS.
# ---------------------------------------------------------------------------

def test_identity_mismatch_between_scanned_and_running_process_blocks_pass():
    obs = _observation(exe="/usr/bin/python3.14")

    def _lying_reverify(pid: int) -> str:
        return "/usr/bin/python3.9"  # a different binary is "actually" running now

    result = critic.critique(obs, reverify_identity=_lying_reverify)
    assert "IDENTITY_MISMATCH" in result.failure_codes

    evidence = judge.judge(obs, result, verified=True, now=time.time())
    assert evidence.status != sp.PASS
    assert evidence.status == sp.UNKNOWN


def test_matching_identity_does_not_by_itself_raise_identity_mismatch():
    obs = _observation(exe="/usr/bin/python3.14")

    def _consistent_reverify(pid: int) -> str:
        return "/usr/bin/python3.14"

    result = critic.critique(obs, reverify_identity=_consistent_reverify)
    assert "IDENTITY_MISMATCH" not in result.failure_codes


# ---------------------------------------------------------------------------
# Test 4 -- an unavailable observation is UNOBSERVED (this project's existing
# "cannot even guarantee it looked" state -- see data-model.md's explicit
# mapping table), never UNKNOWN, and never, ever PASS.
# ---------------------------------------------------------------------------

def test_unavailable_observation_is_unobserved_never_unknown_never_pass():
    evidence = judge.judge_unavailable("proc-9999-1", now=time.time(), reason="process vanished mid-scan")
    assert evidence.status == sp.UNOBSERVED
    assert evidence.status != sp.UNKNOWN
    assert evidence.status != sp.PASS


# ---------------------------------------------------------------------------
# Structural independence -- Critic must never import the Collector's
# internals (research.md #8), only the shared, zero-logic types module.
# ---------------------------------------------------------------------------

def _imported_module_names(py_file: Path) -> set[str]:
    tree = ast.parse(py_file.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def test_critic_never_imports_the_collector():
    imports = _imported_module_names(SCRIPTS_DIR / "security_scientist_critic.py")
    assert "security_scientist_observe" not in imports


def test_judge_never_imports_the_collector_or_the_critic_module():
    imports = _imported_module_names(SCRIPTS_DIR / "security_scientist_judge.py")
    assert "security_scientist_observe" not in imports
    assert "security_scientist_critic" not in imports
