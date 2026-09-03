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
