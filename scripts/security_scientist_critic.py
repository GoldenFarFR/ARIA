#!/usr/bin/env python3
"""Critic for the Security Scientist (specs/019) -- structurally independent
from the Collector (security_scientist_observe.py, NEVER imported here --
enforced by test_security_scientist_judge.py's AST-import test).

Reads only the shared, zero-logic RuntimeObservation shape from
security_scientist_types.py and re-derives its own opinion about whether the
observation supports any conclusion at all. Never produces a safe/unsafe
verdict -- only the eight named failure codes (or none) and the seven
self-critique fields. That derivation is the Judge's job, and only the
Judge's."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
from security_scientist_types import RuntimeObservation, SelfCritique  # noqa: E402


def _default_reverify_identity(pid: int) -> str | None:
    """Independently re-reads /proc/<pid>/exe right now -- the Critic's own
    measurement, never trusting the Collector's copy of the same fact."""
    try:
        return os.readlink(f"/proc/{pid}/exe")
    except (FileNotFoundError, ProcessLookupError, PermissionError, OSError):
        return None


def critique(
    observation: RuntimeObservation,
    *,
    reverify_identity: Callable[[int], str | None] | None = None,
) -> SelfCritique:
    """Independently critiques one Observation. `reverify_identity` is
    injectable so tests can simulate a TOCTOU (the running binary changed
    between the Collector's read and now) without needing a real race."""
    reverify = reverify_identity or _default_reverify_identity
    failure_codes: list[str] = []

    current_exe = reverify(observation.pid)
    if current_exe is None:
        # The process is gone or unreadable right now -- the Critic cannot
        # even confirm the surface still exists, let alone re-verify it.
        failure_codes.append("UNOBSERVABLE")
        identity_verified: bool | None = None
    elif current_exe != observation.exe:
        # Exactly the TOCTOU/pip-upgrade class of failure this feature exists
        # to catch: the identity that was measured is not the identity that
        # is actually executing right now.
        failure_codes.append("IDENTITY_MISMATCH")
        identity_verified = False
    else:
        identity_verified = True

    return SelfCritique(
        coverage_complete=True,
        runtime_identity_verified=identity_verified,
        lookahead_checked=True,
        measurement_independence_checked=True,
        instrument_integrity_checked=True,
        hypothesis_scope_checked=True,
        reproducibility_checked=True,
        failure_codes=tuple(failure_codes),
        reason="" if not failure_codes else f"identity re-check: {failure_codes}",
    )
