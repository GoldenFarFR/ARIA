"""Negative controls on the security posture contract.

Every test here asserts something the system must NOT be able to do. The whole
point of the module under test is that an absence of measurement cannot become
an appearance of safety, so the tests are written as attempts to obtain a
wrongful PASS.
"""

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))

import security_posture as sp  # noqa: E402


def test_unavailable_scanner_is_unknown_never_pass():
    ev = sp.unknown("container-scan", "trivy not installed")
    assert ev.status == sp.UNKNOWN
    assert sp.aggregate([ev]) == sp.UNKNOWN


def test_single_unknown_poisons_an_otherwise_clean_board():
    now = time.time()
    evs = [
        sp.measured("dependabot", True, "0 open", now, 86400),
        sp.measured("osv", True, "0 vulns", now, 86400),
        sp.unknown("sbom", "not generated yet"),
    ]
    assert sp.aggregate(evs) == sp.UNKNOWN


def test_empty_evidence_is_unknown_not_pass():
    # "No check reported a problem" is not a security statement when no check ran.
    assert sp.aggregate([]) == sp.UNKNOWN


def test_expected_check_that_produced_nothing_is_unknown():
    now = time.time()
    evs = [sp.measured("dependabot", True, "0 open", now, 86400)]
    assert sp.aggregate(evs, expected_ids=["dependabot"]) == sp.PASS
    # dropping a check must not make the board greener
    assert sp.aggregate(evs, expected_ids=["dependabot", "container-scan"]) == sp.UNKNOWN


def test_pass_decays_to_stale_once_its_proof_ages_out():
    now = time.time()
    ev = sp.measured("osv", True, "0 vulns", checked_at=now - 100_000, max_age_seconds=86_400)
    assert ev.status == sp.PASS
    aged = sp.apply_freshness(ev, now)
    assert aged.status == sp.STALE
    assert sp.aggregate([aged]) == sp.STALE


def test_a_fail_does_not_decay_into_stale():
    # An unfixed problem growing older is still a problem, not a measurement gap.
    now = time.time()
    ev = sp.measured("osv", False, "3 vulns", checked_at=now - 100_000, max_age_seconds=86_400)
    assert sp.apply_freshness(ev, now).status == sp.FAIL


def test_pass_without_freshness_metadata_is_downgraded():
    ev = sp.Evidence(id="x", status=sp.PASS, detail="clean")
    assert sp.apply_freshness(ev, time.time()).status == sp.UNKNOWN


def test_partial_coverage_cannot_be_a_pass():
    # The exact shape of the 2026-09-03 finding: 4 of 11 manifests watched, and
    # every watched one clean. Clean-but-partial is not PASS.
    now = time.time()
    ev = sp.measured("manifest-coverage", True, "all watched clean", now, 86_400,
                     discovered=11, verified=4)
    assert ev.status == sp.UNKNOWN
    assert "4/11" in ev.detail


def test_full_coverage_with_clean_result_is_the_only_road_to_pass():
    now = time.time()
    ev = sp.measured("manifest-coverage", True, "all clean", now, 86_400,
                     discovered=11, verified=11)
    assert ev.status == sp.PASS


def test_fail_beats_everything_in_aggregation():
    now = time.time()
    evs = [
        sp.measured("a", True, "", now, 86_400),
        sp.unknown("b", "no data"),
        sp.measured("c", False, "boom", now, 86_400),
    ]
    assert sp.aggregate(evs) == sp.FAIL


def test_report_marks_missing_expected_checks_and_never_reports_pass_for_them():
    now = time.time()
    evs = [sp.measured("dependabot", True, "0 open", now, 86_400)]
    rep = sp.report(evs, now, expected_ids=["dependabot", "container-scan"])
    assert rep["global_status"] == sp.UNKNOWN
    ids = {e["id"]: e["status"] for e in rep["evidence"]}
    assert ids["container-scan"] == sp.UNKNOWN


def test_invalid_status_is_rejected_at_construction():
    with pytest.raises(ValueError):
        sp.Evidence(id="x", status="GREEN")


@pytest.mark.parametrize("statuses,expected", [
    ([sp.PASS, sp.PASS], sp.PASS),
    ([sp.PASS, sp.STALE], sp.STALE),
    ([sp.PASS, sp.UNKNOWN], sp.UNKNOWN),
    ([sp.STALE, sp.UNKNOWN], sp.UNKNOWN),
    ([sp.UNKNOWN, sp.FAIL], sp.FAIL),
    ([sp.STALE, sp.FAIL], sp.FAIL),
])
def test_aggregation_ordering_never_lets_pass_mask_a_weaker_state(statuses, expected):
    evs = [sp.Evidence(id=f"e{i}", status=s) for i, s in enumerate(statuses)]
    assert sp.aggregate(evs) == expected


# ---------------------------------------------------------------------------
# UNOBSERVED — coverage property, not a declared label (03/09).
#
# UNKNOWN says "we know a proof is missing". UNOBSERVED says "we do not even
# know this surface exists, or nothing is looking at it". The 21/07 incident
# -- a production venv nobody watched for six weeks -- had no status able to
# express it: every collector reported on what it already knew to look at.
#
# So UNOBSERVED can never be *declared* by a collector. It FALLS OUT of the
# comparison between discovered surfaces and surfaces an observation mechanism
# actually covers. These tests are written as attempts to obtain a wrongful
# PASS from a surface nobody observed.
# ---------------------------------------------------------------------------


def test_unobserved_is_a_valid_status():
    assert sp.UNOBSERVED in sp.VALID_STATUSES


def test_a_surface_nobody_observes_is_unobserved_not_pass():
    # Meta-test case J: every local check is clean, but a discovered surface is
    # covered by no mechanism at all.
    ev = sp.surface_coverage("runtime:aria-core-venv", discovered=True, observed=False)
    assert ev.status == sp.UNOBSERVED


def test_an_undiscovered_surface_is_unobserved():
    ev = sp.surface_coverage("runtime:unknown", discovered=False, observed=False)
    assert ev.status == sp.UNOBSERVED


def test_unobserved_poisons_an_otherwise_perfect_board():
    now = time.time()
    evs = [
        sp.measured("dependabot", True, "0 open", now, 86_400),
        sp.measured("osv-local", True, "0 vulns", now, 86_400),
        sp.surface_coverage("runtime:aria-core-venv", discovered=True, observed=False),
    ]
    assert sp.aggregate(evs) == sp.UNOBSERVED


def test_unobserved_outranks_unknown_because_it_is_epistemically_worse():
    # UNKNOWN is partial knowledge; UNOBSERVED is no knowledge at all.
    evs = [sp.Evidence(id="a", status=sp.UNKNOWN), sp.Evidence(id="b", status=sp.UNOBSERVED)]
    assert sp.aggregate(evs) == sp.UNOBSERVED


def test_a_proven_failure_still_outranks_unobserved():
    # FAIL is actionable now; UNOBSERVED is insidious but not urgent.
    evs = [sp.Evidence(id="a", status=sp.UNOBSERVED), sp.Evidence(id="b", status=sp.FAIL)]
    assert sp.aggregate(evs) == sp.FAIL


def test_unobserved_never_decays_into_something_softer():
    ev = sp.surface_coverage("runtime:x", discovered=True, observed=False)
    assert sp.apply_freshness(ev, time.time()).status == sp.UNOBSERVED


def test_an_observed_but_unverified_surface_is_unknown_not_unobserved():
    # Something IS looking at it -- that is a different, lesser problem.
    ev = sp.surface_coverage("runtime:x", discovered=True, observed=True, verified=False)
    assert ev.status == sp.UNKNOWN


def test_full_coverage_is_the_only_road_out_of_unobserved():
    now = time.time()
    ev = sp.surface_coverage(
        "runtime:x", discovered=True, observed=True, verified=True,
        checked_at=now, max_age_seconds=86_400,
    )
    assert ev.status == sp.PASS


def test_a_verified_surface_without_freshness_cannot_be_pass():
    ev = sp.surface_coverage("runtime:x", discovered=True, observed=True, verified=True)
    assert sp.apply_freshness(ev, time.time()).status == sp.UNKNOWN
