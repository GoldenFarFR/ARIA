#!/usr/bin/env python3
"""Judge for the Security Scientist (specs/019) -- structurally independent
from both the Collector and the Critic (neither is imported here, enforced
by test_security_scientist_judge.py's AST-import test).

Reads only raw facts (RuntimeObservation) and a self-critique (SelfCritique)
and derives a verdict using the existing, 18-negative-test-proven
security_posture.py contract -- never invents a second status vocabulary.

G1, enforced by the signature itself, not by discipline: judge() has no
parameter through which a producer-supplied conclusion could enter. Calling
it with an extra kwarg (e.g. producer_conclusion="SAFE") raises TypeError --
there is no channel for a lie to travel through."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import security_posture as sp  # noqa: E402
from security_scientist_types import RuntimeObservation, SelfCritique  # noqa: E402

MAX_AGE_SECONDS_DEFAULT = 3600


def judge(
    observation: RuntimeObservation,
    critique: SelfCritique,
    *,
    verified: bool,
    now: float,
    max_age_seconds: int = MAX_AGE_SECONDS_DEFAULT,
) -> sp.Evidence:
    """Derives a verdict from raw facts + critique ONLY. `SELF_ATTACK_
    INCOMPLETE` (any critique field unresolved/false, or any failure code
    present) forces UNKNOWN regardless of how clean `verified` looks --
    the Judge never lets a producer's claimed coverage substitute for a
    critic that actually certified it."""
    if not critique.self_attack_complete:
        detail = f"self-attack incomplete: {critique.reason}" if critique.reason else "self-attack incomplete"
        return sp.surface_coverage(
            observation.surface_id, discovered=True, observed=True, verified=False,
            detail=detail, checked_at=now, max_age_seconds=max_age_seconds,
            source="security_scientist_judge",
        )
    return sp.surface_coverage(
        observation.surface_id, discovered=True, observed=True, verified=verified,
        detail="self-attack complete", checked_at=now, max_age_seconds=max_age_seconds,
        source="security_scientist_judge",
    )


def judge_unavailable(surface_id: str, *, now: float, reason: str) -> sp.Evidence:
    """A surface known to exist but that could not be observed at all right
    now -- this project's existing UNOBSERVED (see data-model.md's explicit
    mapping: "I cannot even guarantee I looked" is UNOBSERVED, distinct from
    UNKNOWN's "I looked and couldn't prove enough"). Never UNKNOWN, never
    PASS -- surface_coverage(observed=False) makes this the only reachable
    outcome, not a policy choice made here."""
    return sp.surface_coverage(
        surface_id, discovered=True, observed=False,
        detail=reason, checked_at=now, source="security_scientist_judge",
    )
