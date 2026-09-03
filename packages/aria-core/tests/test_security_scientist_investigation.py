"""FR-015/016 (specs/019-security-scientist): a high-stakes safety
investigation must distinguish PATHS_IDENTIFIED / PATHS_EXHAUSTED /
PATH_SET_COMPLETENESS_PROVEN and never collapse the last two into one flag --
that collapse is exactly how "checked everything I thought of" quietly
becomes "therefore safe" (data-model.md's SecurityInvestigation entity,
frozen). Four scenarios, written before any implementation exists:

1. full list, all checked, completeness proven, no findings -> PASS
2. list only partially checked -> UNKNOWN (never PASS, FR-016)
3. list fully checked but completeness NOT proven -> UNKNOWN, distinct reason
4. a finding on any checked path -> FAIL, regardless of exhaustion state
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
import security_scientist_investigation as si  # noqa: E402


def _investigation(*, paths, completeness_proven):
    return si.SecurityInvestigation(
        investigation_id="H-SEC-TEST",
        paths=tuple(si.PathCheck(path=p, checked=c, finding=f) for p, c, f in paths),
        path_set_completeness_proven=completeness_proven,
    )


def test_full_list_all_checked_completeness_proven_no_findings_is_pass():
    inv = _investigation(
        paths=[("env_vars", True, False), ("systemd_units", True, False)],
        completeness_proven=True,
    )
    status, reason = si.derive_verdict(inv)
    assert status == si.PASS
    assert "completeness" in reason.lower()


def test_partial_list_checked_is_unknown_never_pass():
    inv = _investigation(
        paths=[("env_vars", True, False), ("systemd_units", False, False)],
        completeness_proven=True,
    )
    status, reason = si.derive_verdict(inv)
    assert status == si.UNKNOWN
    assert status != si.PASS
    assert "systemd_units" in reason


def test_full_list_checked_but_completeness_unproven_is_unknown_distinct_reason():
    inv = _investigation(
        paths=[("env_vars", True, False), ("systemd_units", True, False)],
        completeness_proven=False,
    )
    status, reason = si.derive_verdict(inv)
    assert status == si.UNKNOWN
    assert "model-unproven" in reason or "completeness" in reason.lower()

    # Decisive: this must be a DIFFERENT reason than the partial-list case --
    # collapsing the two into the same message is exactly the failure mode
    # this FR exists to prevent (a reader could no longer tell which of the
    # two distinct claims is actually missing).
    partial = _investigation(
        paths=[("env_vars", True, False), ("systemd_units", False, False)],
        completeness_proven=True,
    )
    _, partial_reason = si.derive_verdict(partial)
    assert partial_reason != reason


def test_a_finding_is_fail_regardless_of_exhaustion_or_completeness():
    inv = _investigation(
        paths=[("env_vars", True, True)],  # checked, and the capability WAS found working
        completeness_proven=False,  # would otherwise be UNKNOWN -- FAIL must still win
    )
    status, reason = si.derive_verdict(inv)
    assert status == si.FAIL
    assert "env_vars" in reason


def test_paths_exhausted_is_computed_never_a_settable_flag():
    """paths_exhausted must be a derived property of the actual per-path
    checked booleans, never an independently-settable field a caller could
    set True while individual paths remain unchecked (data-model.md's
    'status is a computed projection, never a stored ground-truth field')."""
    inv = _investigation(
        paths=[("a", True, False), ("b", False, False)],
        completeness_proven=True,
    )
    assert inv.paths_exhausted is False
    assert not hasattr(si.SecurityInvestigation, "paths_exhausted") or isinstance(
        type(inv).paths_exhausted, property
    )
