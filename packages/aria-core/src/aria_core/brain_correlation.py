"""Multi-brain (ON-CHAIN/SOCIAL/CHART) temporal correlation, per candidate.

03/09, operator go -- the level built right after ARIA RADAR V1
(``qualified_candidate_radar.py``), explicitly NOT the Fusion Engine yet:
"Ne modifie pas Radar V1. Construis le prochain niveau : le pipeline de
corrélation ON-CHAIN + SOCIAL, avec persistance temporelle par candidat...
Aucun score inventé, aucune décision de trade."

**What this module is.** A candidate accumulates independent signals from
up to three brains (on_chain / social / chart) over time -- they do not
need to arrive in the same second, or even the same minute (operator's own
worked example: ON-CHAIN at t0, SOCIAL at t0+9s -> 2/3, CHART at t0+45s ->
3/3). Each signal carries its OWN validity window (a brain's read can go
stale before another brain even reacts). ``correlation_state()`` reports
which brains are CURRENTLY positive and valid -- never a stored, manually
maintained status, always recomputed from the raw signal log (same
doctrine as ``gate_audit_log.py``'s ``state_at`` and the Security Scientist
plan's G1: a projection, never a persisted ground-truth verdict).

**What this module is NOT.** It never derives PASS/FAIL/ENTRY/REJECT --
``record_signal_and_check_convergence`` reports a convergence LEVEL
("2/3", "3/3") for a caller to notify on, nothing more. The Fusion Engine
(deriving an actual trade decision from a converged state) is explicitly
the NEXT, not-yet-built step -- see this module's own test suite's
``test_record_and_check_never_derives_a_trade_decision``.

**Append-only, never a fabricated negative.** A brain that never recorded
anything for a candidate is simply ABSENT from ``brains_positive`` -- never
defaulted to False. An expired signal is never deleted (same provenance
doctrine as every other observation table in this codebase): it just stops
counting toward the current state, and the row remains available for a
later replay/audit.
"""
from __future__ import annotations

from datetime import datetime, timezone

import aiosqlite

from .paths import shadow_db_path

TABLE = "brain_correlation_signal_log"
BRAINS = ("on_chain", "social", "chart")


def _db_path() -> str:
    return str(shadow_db_path())


async def _ensure_table() -> None:
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TABLE} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chain TEXT NOT NULL,
                pool_address TEXT NOT NULL,
                brain TEXT NOT NULL,
                positive INTEGER NOT NULL,
                observed_at TEXT NOT NULL,
                valid_until TEXT NOT NULL,
                recorded_at TEXT NOT NULL
            )
            """
        )
        await db.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_candidate "
            f"ON {TABLE} (chain, pool_address)"
        )
        await db.commit()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _parse(s: str) -> datetime:
    return datetime.fromisoformat(s)


async def record_signal(
    pool_address: str, chain: str, brain: str, *, positive: bool,
    observed_at: datetime | None = None, valid_for_seconds: float,
) -> None:
    """Appends one raw signal row. Never overwrites a prior signal for the
    same (candidate, brain) -- ``correlation_state`` reads the log, not a
    mutable per-brain slot."""
    if brain not in BRAINS:
        raise ValueError(f"unknown brain {brain!r}, expected one of {BRAINS}")
    await _ensure_table()
    observed = observed_at or _now()
    valid_until = observed.timestamp() + valid_for_seconds
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            f"INSERT INTO {TABLE} "
            f"(chain, pool_address, brain, positive, observed_at, valid_until, recorded_at) "
            f"VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                chain, pool_address, brain, 1 if positive else 0,
                _iso(observed),
                _iso(datetime.fromtimestamp(valid_until, tz=timezone.utc)),
                _iso(_now()),
            ),
        )
        await db.commit()


async def correlation_state(pool_address: str, chain: str, *, now: datetime | None = None) -> dict:
    """Recomputes the CURRENT convergence state from the raw log -- never a
    stored field. A brain counts as positive iff at least one of its
    recorded signals is ``positive=True`` and still within its validity
    window at ``now``."""
    await _ensure_table()
    at = now or _now()
    async with aiosqlite.connect(_db_path()) as db:
        cur = await db.execute(
            f"SELECT brain, positive, observed_at, valid_until FROM {TABLE} "
            f"WHERE chain = ? AND pool_address = ?",
            (chain, pool_address),
        )
        rows = await cur.fetchall()

    brains_positive: set[str] = set()
    observed_at_by_brain: dict[str, str] = {}
    for brain, positive, observed_at, valid_until in rows:
        if not positive:
            continue
        if _parse(observed_at) <= at <= _parse(valid_until):
            brains_positive.add(brain)
            # last-write-wins if a brain fired more than once -- the most
            # recent valid observation is the one worth displaying.
            observed_at_by_brain[brain] = observed_at

    ordered = [b for b in BRAINS if b in brains_positive]
    return {
        "brains_positive": ordered,
        "count": len(ordered),
        "level": f"{len(ordered)}/3",
        "observed_at": {b: observed_at_by_brain[b] for b in ordered},
    }


async def record_signal_and_check_convergence(
    pool_address: str, chain: str, brain: str, *, positive: bool,
    observed_at: datetime | None = None, valid_for_seconds: float,
) -> dict:
    """Same as ``record_signal`` plus a before/after comparison, so a
    caller can notify exactly once per newly-crossed convergence level --
    never re-fires "2/3" on a redundant signal that doesn't change the
    count. Returns ``{"state": <after correlation_state()>, "newly_crossed":
    "2/3"|"3/3"|None}`` -- deliberately no trade-decision field."""
    at = observed_at or _now()
    before = await correlation_state(pool_address, chain, now=at)
    await record_signal(
        pool_address, chain, brain, positive=positive,
        observed_at=observed_at, valid_for_seconds=valid_for_seconds,
    )
    after = await correlation_state(pool_address, chain, now=at)
    newly_crossed = after["level"] if after["count"] > before["count"] else None
    return {"state": after, "newly_crossed": newly_crossed}
