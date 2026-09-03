"""Security posture as EVIDENCE, never as a count of problems.

Operator directive, 2026-09-03: "Le watchdog ne doit pas compter des problemes.
Il doit verifier que nous avons suffisamment de preuves pour pouvoir dire PASS."

The failure this module exists to make impossible was demonstrated twice in one
morning on this project:
  - a Dependabot query reported "30 open alerts" that were in fact the full
    history, 29 of them already fixed, and the query was truncated by pagination
    on top of that;
  - the first security watchdog reported the Claude CLI as 2.1.222 because its
    own PATH resolved the stale system copy rather than the version actually
    being served.
Both produced a confident number from an incomplete measurement. Neither could
have been caught by adding more scanners.

So a check never returns a bare boolean here. It returns an Evidence: what was
looked at, how much of it was actually covered, when, and how long that answer
stays valid. Aggregation is deliberately pessimistic -- a single unmeasured
surface makes the whole posture UNKNOWN, and UNKNOWN never decays into PASS.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Iterable, Sequence

PASS = "PASS"        # complete, fresh proof that the surface is healthy
FAIL = "FAIL"        # proof of an actual problem
UNKNOWN = "UNKNOWN"  # insufficient proof: measurement failed, or coverage is partial
STALE = "STALE"      # proof exists and was healthy, but is too old to trust now

VALID_STATUSES = (PASS, FAIL, UNKNOWN, STALE)

# Worst-wins ordering. The only property that really matters: PASS is LAST, so
# it can never mask an UNKNOWN or a STALE sitting next to it.
_SEVERITY = {FAIL: 0, UNKNOWN: 1, STALE: 2, PASS: 3}


@dataclass
class Evidence:
    """One security claim, carrying what backs it up.

    discovered/verified are the coverage pair. They are what turns "we found no
    problem" into "we looked at all of it and found no problem" -- a distinction
    that is invisible in a boolean. When they disagree, the check cannot be PASS
    no matter how clean the part that WAS examined came back.
    """

    id: str
    status: str
    detail: str = ""
    checked_at: float | None = None
    max_age_seconds: int | None = None
    discovered: int | None = None
    verified: int | None = None
    source: str = ""

    def __post_init__(self) -> None:
        if self.status not in VALID_STATUSES:
            raise ValueError(f"{self.id}: invalid status {self.status!r}")


def unknown(id: str, reason: str, source: str = "") -> Evidence:
    """The constructor to reach for whenever a measurement could not be made.

    Deliberately more convenient than building a PASS by hand: the easy path
    through this module has to be the honest one.
    """
    return Evidence(id=id, status=UNKNOWN, detail=reason, source=source)


def measured(
    id: str,
    ok: bool,
    detail: str,
    checked_at: float,
    max_age_seconds: int,
    discovered: int | None = None,
    verified: int | None = None,
    source: str = "",
) -> Evidence:
    """Build evidence from a measurement that actually ran.

    Even here PASS is not automatic: if the caller supplies a coverage pair that
    does not add up, the result is UNKNOWN rather than a PASS over a partial
    surface.
    """
    if discovered is not None and verified is not None and discovered != verified:
        return Evidence(
            id=id,
            status=UNKNOWN,
            detail=f"{detail} (coverage incomplete: {verified}/{discovered} verified)",
            checked_at=checked_at,
            max_age_seconds=max_age_seconds,
            discovered=discovered,
            verified=verified,
            source=source,
        )
    return Evidence(
        id=id,
        status=PASS if ok else FAIL,
        detail=detail,
        checked_at=checked_at,
        max_age_seconds=max_age_seconds,
        discovered=discovered,
        verified=verified,
        source=source,
    )


def apply_freshness(ev: Evidence, now: float) -> Evidence:
    """Age a PASS into STALE once its proof is older than max_age_seconds.

    Only a PASS decays. A FAIL stays a FAIL when it gets old -- an unfixed
    problem does not become a measurement gap -- and UNKNOWN is already the
    weakest state there is.
    """
    if ev.status != PASS:
        return ev
    if ev.checked_at is None or ev.max_age_seconds is None:
        return Evidence(
            id=ev.id,
            status=UNKNOWN,
            detail=f"{ev.detail} (no freshness information attached)".strip(),
            checked_at=ev.checked_at,
            max_age_seconds=ev.max_age_seconds,
            discovered=ev.discovered,
            verified=ev.verified,
            source=ev.source,
        )
    age = now - ev.checked_at
    if age > ev.max_age_seconds:
        return Evidence(
            id=ev.id,
            status=STALE,
            detail=f"{ev.detail} (proof is {int(age)}s old, limit {ev.max_age_seconds}s)".strip(),
            checked_at=ev.checked_at,
            max_age_seconds=ev.max_age_seconds,
            discovered=ev.discovered,
            verified=ev.verified,
            source=ev.source,
        )
    return ev


def aggregate(evidences: Sequence[Evidence], expected_ids: Iterable[str] = ()) -> str:
    """Collapse a set of evidence into one posture, pessimistically.

    Two rules carry the whole design:
      - an empty set is UNKNOWN, never PASS. "No check reported a problem" is
        not a security statement when no check ran;
      - an expected check that produced no evidence at all is UNKNOWN too, so
        silently dropping a check cannot turn the board green.
    """
    evs = list(evidences)
    expected = list(expected_ids)
    if not evs:
        return UNKNOWN
    seen = {e.id for e in evs}
    if any(x not in seen for x in expected):
        return UNKNOWN
    return min((e.status for e in evs), key=lambda s: _SEVERITY[s])


def to_dict(ev: Evidence) -> dict:
    return asdict(ev)


def report(evidences: Sequence[Evidence], now: float, expected_ids: Iterable[str] = ()) -> dict:
    """The machine-readable posture: the security contract in one object."""
    aged = [apply_freshness(e, now) for e in evidences]
    expected = list(expected_ids)
    missing = [x for x in expected if x not in {e.id for e in aged}]
    for m in missing:
        aged.append(unknown(m, "expected check produced no evidence"))
    return {
        "generated_at": now,
        "global_status": aggregate(aged, expected),
        "counts": {s: sum(1 for e in aged if e.status == s) for s in VALID_STATUSES},
        "evidence": [to_dict(e) for e in aged],
    }
