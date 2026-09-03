#!/usr/bin/env python3
"""FR-015/016 (specs/019-security-scientist): high-stakes safety
investigations. Distinguishes three coverage claims that must never be
collapsed into one (data-model.md's SecurityInvestigation entity, frozen):

  PATHS_IDENTIFIED              -- the list of paths to rule out exists
  PATHS_EXHAUSTED                -- every entry on that list has been checked
  PATH_SET_COMPLETENESS_PROVEN   -- there is a positive argument the list
                                     itself cannot be missing a relevant path

"Every known path checks out" (paths_exhausted) is a strictly weaker claim
than "the known-path list cannot be missing a path" (completeness_proven).
Collapsing the two is exactly how "I checked everything I thought of"
quietly becomes "therefore it's safe" -- the failure mode this module
exists to make structurally impossible: derive_verdict can only return PASS
when both are true, and the two UNKNOWN branches carry distinct, named
reasons so a reader can always tell which of the two claims is missing.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

PASS = "PASS"
FAIL = "FAIL"
UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class PathCheck:
    path: str
    checked: bool
    finding: bool = False  # True == the capability was found working via this path


@dataclass(frozen=True)
class SecurityInvestigation:
    investigation_id: str
    paths: tuple[PathCheck, ...]
    path_set_completeness_proven: bool

    @property
    def paths_exhausted(self) -> bool:
        """Computed from the actual per-path checked booleans -- never an
        independently-settable field a caller could set True while some
        path remains unchecked (data-model.md: status is always a computed
        projection, never a stored ground-truth field)."""
        return all(p.checked for p in self.paths)

    @property
    def paths_with_findings(self) -> tuple[str, ...]:
        return tuple(p.path for p in self.paths if p.checked and p.finding)


def derive_verdict(investigation: SecurityInvestigation) -> tuple[str, str]:
    """Mirrors data-model.md's derivation rule verbatim:

        FAIL     if paths_with_findings is non-empty
        UNKNOWN  if not paths_exhausted
        UNKNOWN  if paths_exhausted and not path_set_completeness_proven
                 (labeled "path-exhausted, model-unproven")
        PASS     only if paths_exhausted AND path_set_completeness_proven
                 AND paths_with_findings is empty
    """
    findings = investigation.paths_with_findings
    if findings:
        return FAIL, f"capability confirmed working via: {', '.join(findings)}"

    if not investigation.paths_exhausted:
        unchecked = [p.path for p in investigation.paths if not p.checked]
        return UNKNOWN, f"path list only partially checked, unchecked: {', '.join(unchecked)}"

    if not investigation.path_set_completeness_proven:
        return UNKNOWN, (
            "path-exhausted, model-unproven -- every known path checks out, "
            "but there is no proof the path list itself cannot be missing a path"
        )

    return PASS, (
        f"all {len(investigation.paths)} identified paths checked, "
        "path-set completeness proven, none found the capability working"
    )


def run_investigation(
    investigation_id: str,
    enumerate_paths: Callable[[], list[str]],
    verify_path: Callable[[str], tuple[bool, bool]],
    prove_completeness: Callable[[list[str]], tuple[bool, str]],
) -> tuple[str, str, SecurityInvestigation]:
    """The real 4-stage pipeline: path enumeration -> path verification ->
    completeness proof -> verdict derivation. This is the structural
    guarantee FR-015/016 requires beyond derive_verdict() alone -- a verdict
    can only be produced by genuinely running all three upstream stages,
    never by constructing a SecurityInvestigation directly and skipping them.

    `verify_path` is called once per path the enumerator actually produced
    (never fewer -- no path can be silently skipped between enumeration and
    verdict) and returns (checked, finding) for that specific path.

    `prove_completeness` receives the identified path list and returns
    (proven, argument). A bare `proven=True` with an empty argument is
    refused here and downgraded to unproven -- a completeness claim with no
    actual argument is not a proof, and trusting it is exactly the collapse
    (paths_exhausted quietly becoming "therefore safe") FR-016 exists to
    forbid. This is the adversarial case an empty path enumeration surfaces:
    an empty list is vacuously "exhausted" (all() over nothing is True), so
    without this guard a caller could reach PASS on zero verified paths
    just by claiming completeness with no justification.
    """
    identified = enumerate_paths()
    checks = tuple(
        PathCheck(path=p, checked=checked, finding=finding)
        for p in identified
        for checked, finding in (verify_path(p),)
    )
    proven, argument = prove_completeness(identified)
    if proven and not argument.strip():
        proven = False

    investigation = SecurityInvestigation(
        investigation_id=investigation_id, paths=checks, path_set_completeness_proven=proven,
    )
    status, reason = derive_verdict(investigation)
    return status, reason, investigation
