"""Shared data shape for the Security Scientist's Collector/Critic/Judge chain
(specs/019-security-scientist, plan.md refinement #4 / research.md #8).

Zero logic here on purpose -- this is the ONLY file both security_scientist_
observe.py (Collector) and security_scientist_critic.py (Critic) may import
from each other's side, so the Critic never reaches into the Collector's
internals, only this plain data shape."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RuntimeObservation:
    """Raw facts about one live process, from /proc -- NEVER a safe/unsafe/
    unknown field of any kind (G1). cmdline0 is the string actually passed to
    execve() (argv[0]), NOT the resolved `exe` symlink target -- verified live
    that venv python binaries are symlinks to the base interpreter, so `exe`
    cannot identify which venv launched a process (research.md #3's
    empirical correction).

    Frozen with a fixed field set on purpose: a producer literally cannot
    pass an extra conclusion-shaped field (e.g. producer_conclusion="SAFE")
    through this type -- Python raises TypeError on an unexpected keyword,
    which is G1 enforced by the type system, not by convention."""

    pid: int
    surface_id: str          # f"proc-{pid}-{start_time}" -- stable across one process's lifetime
    cmdline0: str             # argv[0] as actually invoked
    exe: str                  # real resolved interpreter/binary identity (not venv-specific)
    cwd: str
    argv_fingerprint: str      # sha256(cmdline)[:16], never raw argv (secrets discipline)
    start_time: str            # /proc/pid/stat field 22, part of the surface_id
    env_var_names: tuple[str, ...] = field(default_factory=tuple)  # names only, never values


# The eight named failure codes a Critic may raise (plan.md refinement #4,
# data-model.md's mapping table). Any single one present blocks PASS at the
# Judge, regardless of how clean the raw observation otherwise looks.
FAILURE_CODES = frozenset({
    "COVERAGE_UNKNOWN",
    "IDENTITY_MISMATCH",
    "STALE_SOURCE",
    "WRONG_EXECUTABLE",
    "UNOBSERVABLE",
    "SELF_DEPENDENCY",
    "SCOPE_TOO_NARROW",
    "NON_REPRODUCIBLE",
})


@dataclass(frozen=True)
class SelfCritique:
    """Structured self-critique of one Observation (G3) -- authored by a role
    structurally independent from whatever produced the Observation
    (security_scientist_critic.py, never security_scientist_observe.py).

    Each of the seven fields is True (verified), False (checked and failed),
    or None (not checked -- an honest "don't know", never defaulted to True).
    `SELF_ATTACK_INCOMPLETE` (any field is None or False) blocks PASS at the
    Judge exactly as hard as a named failure_code does."""

    coverage_complete: bool | None
    runtime_identity_verified: bool | None
    lookahead_checked: bool | None
    measurement_independence_checked: bool | None
    instrument_integrity_checked: bool | None
    hypothesis_scope_checked: bool | None
    reproducibility_checked: bool | None
    failure_codes: tuple[str, ...] = field(default_factory=tuple)
    reason: str = ""

    def __post_init__(self) -> None:
        unknown_codes = set(self.failure_codes) - FAILURE_CODES
        if unknown_codes:
            raise ValueError(f"unrecognized failure code(s): {sorted(unknown_codes)}")

    @property
    def self_attack_complete(self) -> bool:
        """False (SELF_ATTACK_INCOMPLETE) if any check is unresolved/failed
        or any failure code was raised -- the single gate the Judge reads."""
        fields = (
            self.coverage_complete, self.runtime_identity_verified, self.lookahead_checked,
            self.measurement_independence_checked, self.instrument_integrity_checked,
            self.hypothesis_scope_checked, self.reproducibility_checked,
        )
        return all(f is True for f in fields) and not self.failure_codes
