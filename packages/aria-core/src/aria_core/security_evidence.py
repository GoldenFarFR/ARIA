"""Persistence for the Security Scientist (specs/019-security-scientist).

Dedicated file (security_scientist.db, NOT aria.db) -- deliberate deviation
from gate_audit_log.py's shared aria.db, decided during plan review: the
Security Scientist must keep working even if aria-api's own database is
locked or the container is stopped (independence invariant, I2 in the
approved plan). Same query/history discipline as gate_audit_log.py's
state_at() otherwise.

Three tables:
  security_observations     immutable, one row per raw measurement (G2
                             provenance: observer_version/environment_identity/
                             observed_at/recorded_at). payload_json is
                             schema-checked to reject any key whose name is a
                             conclusion (status/verdict/safe/unsafe/pass/fail)
                             -- a Collector cannot smuggle a verdict in even
                             informally (G1).
  security_rejected_evidence append-only audit of observations that could not
                             be accepted -- never usable to derive a verdict,
                             itself the numerator of "false certainty
                             prevented" (spec.md SC-007).
  security_evaluations      derived-only, append-only. No column here is ever
                             UPDATEd -- "current status" is always the latest
                             row, state_at() the latest row at-or-before a
                             past instant. There is no column anywhere named
                             `status` that an application can overwrite in
                             place, which is what makes "verdict is always a
                             computed projection, never a stored ground-truth
                             fact" (plan.md refinement #2) structural rather
                             than a convention.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import aiosqlite

from aria_core.paths import data_dir

DB_PATH_DEFAULT = None  # resolved lazily via _db_path() so tests can monkeypatch DB_PATH


def _default_db_path() -> str:
    return str(data_dir() / "security_scientist.db")


DB_PATH = _default_db_path()

# Reserved surface_id representing the discovery pass itself (research.md #7) --
# distinct from any individual process surface.
INVENTORY_SURFACE_ID = "host-runtime-inventory"

# Reserved surface_id for the scheduler's own liveness (research.md #12) --
# written by run.sh AFTER attempting a cycle (success or failure explicit),
# never before: writing "ok" before the attempt would let a crashed/timed-out
# cycle look identical to a successful one.
OBSERVER_SURFACE_ID = "security-scientist-observer"

_UNOBSERVED = "UNOBSERVED"  # mirrors security_posture.UNOBSERVED without importing
                            # across the scripts/ <-> aria_core boundary

# G1, mechanical: a raw observation payload may never carry a key whose
# semantics IS a conclusion. Checked case-insensitively so "Status"/"SAFE"
# etc. cannot slip through by casing.
_FORBIDDEN_PAYLOAD_KEYS = frozenset(
    {"status", "verdict", "safe", "unsafe", "pass", "fail", "passed", "failed"}
)

_VALID_STATUSES = frozenset({"PASS", "FAIL", "UNOBSERVED", "UNKNOWN", "STALE"})


def _now() -> float:
    return datetime.now(timezone.utc).timestamp()


def _db_path() -> str:
    return DB_PATH


async def _ensure_tables() -> None:
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS security_observations (
                observation_id TEXT PRIMARY KEY,
                surface_id TEXT NOT NULL,
                mission_id TEXT,
                experiment_id TEXT,
                observer_version TEXT NOT NULL,
                environment_identity TEXT NOT NULL,
                observed_at REAL NOT NULL,
                recorded_at REAL NOT NULL,
                payload_json TEXT NOT NULL
            )
            """
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_sec_obs_surface "
            "ON security_observations (surface_id, observed_at)"
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS security_rejected_evidence (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                attempted_at REAL NOT NULL,
                surface_id TEXT,
                reason TEXT NOT NULL,
                payload_json TEXT
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS security_evaluations (
                evaluation_id TEXT PRIMARY KEY,
                surface_id TEXT NOT NULL,
                status TEXT NOT NULL,
                evaluated_at REAL NOT NULL,
                observations_used TEXT NOT NULL,
                self_critique_id TEXT NOT NULL,
                detail TEXT
            )
            """
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_sec_eval_surface "
            "ON security_evaluations (surface_id, evaluated_at)"
        )
        await db.commit()


async def _reject(surface_id: str | None, reason: str, payload: dict | None) -> None:
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            "INSERT INTO security_rejected_evidence "
            "(attempted_at, surface_id, reason, payload_json) VALUES (?, ?, ?, ?)",
            (_now(), surface_id, reason, json.dumps(payload) if payload is not None else None),
        )
        await db.commit()


async def record_observation(
    surface_id: str,
    payload: dict,
    *,
    observer_version: str,
    environment_identity: dict | None,
    observed_at: float | None = None,
    recorded_at: float | None = None,
    mission_id: str | None = None,
    experiment_id: str | None = None,
) -> str | None:
    """Records a raw observation -- returns its id, or None (and logs a
    rejection) if it lacks provenance or smuggles a conclusion (G1/G2)."""
    await _ensure_tables()

    forbidden = _FORBIDDEN_PAYLOAD_KEYS & {k.lower() for k in payload}
    if forbidden:
        await _reject(
            surface_id,
            f"payload contains conclusion-shaped key(s): {sorted(forbidden)}",
            payload,
        )
        return None
    if not observer_version:
        await _reject(surface_id, "missing provenance: observer_version", payload)
        return None
    if not environment_identity:
        await _reject(surface_id, "missing provenance: environment_identity", payload)
        return None

    now = _now()
    observation_id = str(uuid.uuid4())
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            "INSERT INTO security_observations "
            "(observation_id, surface_id, mission_id, experiment_id, observer_version, "
            "environment_identity, observed_at, recorded_at, payload_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                observation_id, surface_id, mission_id, experiment_id, observer_version,
                json.dumps(environment_identity), observed_at or now, recorded_at or now,
                json.dumps(payload),
            ),
        )
        await db.commit()
    return observation_id


async def list_observations(surface_id: str) -> list[dict]:
    await _ensure_tables()
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        rows = await (
            await db.execute(
                "SELECT * FROM security_observations WHERE surface_id = ? ORDER BY observed_at",
                (surface_id,),
            )
        ).fetchall()
    return [
        {
            **dict(r),
            "payload": json.loads(r["payload_json"]),
            "environment_identity": json.loads(r["environment_identity"]),
        }
        for r in rows
    ]


async def list_surface_ids(prefix: str) -> list[str]:
    """Every distinct surface_id ever observed matching a prefix -- used to
    detect a surface that disappeared between passes (research.md's
    disposable-process gate test)."""
    await _ensure_tables()
    async with aiosqlite.connect(_db_path()) as db:
        rows = await (
            await db.execute(
                "SELECT DISTINCT surface_id FROM security_observations WHERE surface_id LIKE ?",
                (prefix + "%",),
            )
        ).fetchall()
    return [r[0] for r in rows]


async def list_rejected(surface_id: str | None = None) -> list[dict]:
    await _ensure_tables()
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        if surface_id is None:
            rows = await (
                await db.execute("SELECT * FROM security_rejected_evidence ORDER BY attempted_at")
            ).fetchall()
        else:
            rows = await (
                await db.execute(
                    "SELECT * FROM security_rejected_evidence WHERE surface_id = ? "
                    "ORDER BY attempted_at",
                    (surface_id,),
                )
            ).fetchall()
    return [dict(r) for r in rows]


async def record_evaluation(
    surface_id: str,
    status: str,
    *,
    observations_used: list[str],
    self_critique_id: str,
    detail: str = "",
    evaluated_at: float | None = None,
) -> str:
    """Appends a derived evaluation. Never updates a prior row -- there is no
    UPDATE path in this module, by design (see module docstring)."""
    if status not in _VALID_STATUSES:
        raise ValueError(f"invalid status {status!r}, must be one of {sorted(_VALID_STATUSES)}")
    await _ensure_tables()
    evaluation_id = str(uuid.uuid4())
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            "INSERT INTO security_evaluations "
            "(evaluation_id, surface_id, status, evaluated_at, observations_used, "
            "self_critique_id, detail) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                evaluation_id, surface_id, status, evaluated_at or _now(),
                json.dumps(observations_used), self_critique_id, detail,
            ),
        )
        await db.commit()
    return evaluation_id


async def list_evaluations(surface_id: str) -> list[dict]:
    await _ensure_tables()
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        rows = await (
            await db.execute(
                "SELECT * FROM security_evaluations WHERE surface_id = ? ORDER BY evaluated_at",
                (surface_id,),
            )
        ).fetchall()
    return [dict(r) for r in rows]


async def state_at(surface_id: str, at: float) -> dict | None:
    """The evaluation that was true for surface_id at instant `at` -- the
    latest evaluation whose evaluated_at <= at. Mirrors gate_audit_log.py's
    state_at(gate, at), applied to a surface instead of a gate."""
    await _ensure_tables()
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        row = await (
            await db.execute(
                "SELECT * FROM security_evaluations WHERE surface_id = ? AND evaluated_at <= ? "
                "ORDER BY evaluated_at DESC LIMIT 1",
                (surface_id, at),
            )
        ).fetchone()
    return dict(row) if row else None


async def reported_status(surface_id: str, at: float) -> str:
    """The one canonical way any consumer reads a surface's status -- never
    treats 'no evaluation recorded' as PASS, or as anything but UNOBSERVED
    (SS-I1: absence of observation is never absence of reality)."""
    row = await state_at(surface_id, at)
    return row["status"] if row else _UNOBSERVED


async def last_discovery_pass_age(now: float, surface_id: str = INVENTORY_SURFACE_ID) -> float | None:
    """How long ago the last discovery pass recorded anything -- None (never
    0) when no pass has ever run, so an absent pass is never silently treated
    as a fresh one (research.md #7)."""
    await _ensure_tables()
    async with aiosqlite.connect(_db_path()) as db:
        row = await (
            await db.execute(
                "SELECT MAX(recorded_at) FROM security_observations WHERE surface_id = ?",
                (surface_id,),
            )
        ).fetchone()
    last = row[0] if row else None
    if last is None:
        return None
    return now - last
