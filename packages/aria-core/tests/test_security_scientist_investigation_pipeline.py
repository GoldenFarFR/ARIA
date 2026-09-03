"""FR-015/016 integration lock (operator-requested, 2026-09-03): the pure
derive_verdict() unit tests proved the derivation logic, but not that a real
pipeline (path enumeration -> path verification -> completeness proof ->
verdict) actually feeds it -- a caller could still construct a
SecurityInvestigation directly and skip straight to a verdict. These tests
exercise run_investigation(), the orchestrator that makes that skip
structurally impossible, using real callables (call-counted, not
pre-filled dicts) so a verdict can only be produced by genuinely running
every upstream stage.

Includes the adversarial case this very integration exercise surfaced:
an empty path enumeration + an unargued completeness claim must never
produce a free PASS on zero verified paths -- exactly the collapse FR-016
exists to forbid.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
import security_scientist_investigation as si  # noqa: E402


class _CountedEnumerator:
    def __init__(self, paths):
        self._paths = paths
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return list(self._paths)


class _CountedVerifier:
    """Real per-path verification -- records exactly which paths it was
    asked about, so a test can prove no path was silently skipped."""

    def __init__(self, results: dict):
        self._results = results
        self.checked_paths: list[str] = []

    def __call__(self, path):
        self.checked_paths.append(path)
        return self._results[path]


class _CountedProver:
    def __init__(self, outcome):
        self._outcome = outcome
        self.calls = 0

    def __call__(self, identified_paths):
        self.calls += 1
        return self._outcome


# ---------------------------------------------------------------------------
# 1. The full real pipeline, all three stages genuinely invoked -> PASS.
# ---------------------------------------------------------------------------

def test_full_pipeline_produces_pass_only_when_all_three_stages_genuinely_run():
    enumerate_paths = _CountedEnumerator(["env_vars", "systemd_units"])
    verify = _CountedVerifier({"env_vars": (True, False), "systemd_units": (True, False)})
    prove = _CountedProver((True, "both surface classes enumerated from the live host inventory"))

    status, reason, investigation = si.run_investigation(
        "H-SEC-TEST-PIPE", enumerate_paths, verify, prove,
    )

    assert status == si.PASS
    assert enumerate_paths.calls == 1
    assert prove.calls == 1
    # Decisive: every path the enumerator produced was actually handed to
    # the verifier -- none silently skipped between enumeration and verdict.
    assert sorted(verify.checked_paths) == ["env_vars", "systemd_units"]
    assert investigation.paths_exhausted is True


# ---------------------------------------------------------------------------
# 2. A path the verifier marks unchecked -> UNKNOWN through the real pipeline.
# ---------------------------------------------------------------------------

def test_partial_verification_in_the_pipeline_yields_unknown():
    enumerate_paths = _CountedEnumerator(["env_vars", "systemd_units"])
    verify = _CountedVerifier({"env_vars": (True, False), "systemd_units": (False, False)})
    prove = _CountedProver((True, "enumeration argument present"))

    status, reason, _ = si.run_investigation("H-SEC-TEST-PIPE", enumerate_paths, verify, prove)
    assert status == si.UNKNOWN
    assert "systemd_units" in reason


# ---------------------------------------------------------------------------
# 3. THE decisive adversarial case: a completeness claim with NO argument
# must be refused -- never trusted as a bare boolean.
# ---------------------------------------------------------------------------

def test_completeness_claim_without_argument_is_refused_forces_unknown():
    enumerate_paths = _CountedEnumerator(["env_vars"])
    verify = _CountedVerifier({"env_vars": (True, False)})
    prove = _CountedProver((True, ""))  # claims proven, offers no actual argument

    status, reason, investigation = si.run_investigation(
        "H-SEC-TEST-PIPE", enumerate_paths, verify, prove,
    )
    assert status == si.UNKNOWN, (
        "a completeness claim with an empty argument is not a proof -- "
        "trusting the bare boolean is exactly the collapse FR-016 forbids"
    )
    assert investigation.path_set_completeness_proven is False


# ---------------------------------------------------------------------------
# 4. THE founding adversarial case for this integration pass: an EMPTY path
# enumeration is vacuously "exhausted" -- it must never turn into a free
# PASS on zero verified paths just because completeness was claimed.
# ---------------------------------------------------------------------------

def test_empty_path_enumeration_with_unproven_completeness_is_unknown_never_a_free_pass():
    enumerate_paths = _CountedEnumerator([])
    verify = _CountedVerifier({})
    prove = _CountedProver((False, "no argument available yet"))

    status, reason, investigation = si.run_investigation(
        "H-SEC-TEST-PIPE", enumerate_paths, verify, prove,
    )
    assert status == si.UNKNOWN
    assert status != si.PASS
    assert investigation.paths_exhausted is True, "vacuously true over an empty set -- expected"


def test_empty_path_enumeration_can_only_pass_with_a_real_argued_completeness_proof():
    """The one legitimate way an empty path list reaches PASS: a genuine,
    non-empty argument for why the space of paths is provably empty --
    never a bare True."""
    enumerate_paths = _CountedEnumerator([])
    verify = _CountedVerifier({})
    prove = _CountedProver((True, "capability requires network egress; this host has none by design, verified via iptables OUTPUT DROP"))

    status, reason, _ = si.run_investigation("H-SEC-TEST-PIPE", enumerate_paths, verify, prove)
    assert status == si.PASS


# ---------------------------------------------------------------------------
# 5. A finding anywhere in the real pipeline propagates to FAIL.
# ---------------------------------------------------------------------------

def test_a_finding_anywhere_in_the_pipeline_is_fail():
    enumerate_paths = _CountedEnumerator(["env_vars", "systemd_units"])
    verify = _CountedVerifier({"env_vars": (True, False), "systemd_units": (True, True)})
    prove = _CountedProver((True, "argument present"))

    status, reason, _ = si.run_investigation("H-SEC-TEST-PIPE", enumerate_paths, verify, prove)
    assert status == si.FAIL
    assert "systemd_units" in reason


# ---------------------------------------------------------------------------
# 6. Structural lock: no path can bypass verification -- every identified
# path is provably handed to the verifier, exactly once.
# ---------------------------------------------------------------------------

def test_every_identified_path_is_verified_exactly_once_never_skippable():
    paths = ["a", "b", "c", "d"]
    enumerate_paths = _CountedEnumerator(paths)
    verify = _CountedVerifier({p: (True, False) for p in paths})
    prove = _CountedProver((True, "argument present"))

    si.run_investigation("H-SEC-TEST-PIPE", enumerate_paths, verify, prove)
    assert sorted(verify.checked_paths) == sorted(paths)
    assert len(verify.checked_paths) == len(paths), "a path was verified more than once or skipped"
