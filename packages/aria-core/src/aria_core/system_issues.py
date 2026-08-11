"""Centralized "GitHub Issues"-style registry for anomalies detected by any
watchdog or audit across the system (11/08, explicit operator request: "je
veut que se soit comme github si il y a un probleme tu a des notification
dans une fichier et cest a toi de les fermer ou de les reparer").

Before this module, each watchdog (paper-watchdog, signal-cascade-watch,
memory-watch, log-health-watch, circuit-breaker-watch, v8-watch, vc-watch)
wrote its own findings into its OWN markdown log, under its OWN directory --
useful as a detailed trail, but nothing surfaced them at the start of a
session the way ``signal_cascade_triage_queue`` already does for cascade
candidates (via ``.claude/hooks/signal-cascade-queue-reminder.sh``). This
module generalizes that exact same proven pattern (persistent queue, read at
SessionStart, a Claude Code session is the one expected to act on it) to
EVERY source of anomaly, not just the signal cascade.

Deliberately a single shared table, not one per source: the whole point is
ONE place to check, not N logs nobody remembers to open. ``source`` free-text
(the watchdog/audit name) rather than an enum -- new sources are added by
simply writing a new value here, never a schema migration.

Bash watchdogs write directly via ``sqlite3`` (never Python, same doctrine as
every other mechanical watchdog on this project -- see the module docstring
convention already established by ``goplus_watchlist.py``/``candle_history.py``
for the exact SQL shape a bash script should use). This module is the
CONVENIENCE layer for anything already running in Python (a session itself,
a future audit script) -- both write to the exact same table."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import aiosqlite

from aria_core.paths import aria_db_path

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())

_VALID_SEVERITIES = frozenset({"info", "warning", "critical"})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _db_path() -> str:
    return DB_PATH


async def _ensure_table() -> None:
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS system_issues (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                title TEXT NOT NULL,
                detail TEXT NOT NULL DEFAULT '',
                severity TEXT NOT NULL DEFAULT 'warning',
                status TEXT NOT NULL DEFAULT 'open',
                dedup_key TEXT,
                opened_at TEXT NOT NULL,
                closed_at TEXT,
                closed_reason TEXT
            )
            """
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_system_issues_open "
            "ON system_issues (status, source)"
        )
        await db.commit()


async def open_issue(
    source: str, title: str, *, detail: str = "", severity: str = "warning",
    dedup_key: str | None = None,
) -> int:
    """Opens a new issue, or returns the id of an already-OPEN issue with the
    same ``(source, dedup_key)`` if ``dedup_key`` is given -- a watchdog that
    re-detects the SAME ongoing anomaly every passage must never spam a new
    row each time (same hysteresis doctrine as ``vc-watch/run.sh``'s own
    ``alert-state.tsv``). Pass ``dedup_key=None`` (default) for a genuinely
    one-off event where every occurrence deserves its own row."""
    severity = severity if severity in _VALID_SEVERITIES else "warning"
    await _ensure_table()
    async with aiosqlite.connect(_db_path()) as db:
        if dedup_key:
            row = await (
                await db.execute(
                    "SELECT id FROM system_issues WHERE source = ? AND dedup_key = ? AND status = 'open'",
                    (source, dedup_key),
                )
            ).fetchone()
            if row is not None:
                return int(row[0])
        cur = await db.execute(
            "INSERT INTO system_issues (source, title, detail, severity, status, dedup_key, opened_at) "
            "VALUES (?, ?, ?, ?, 'open', ?, ?)",
            (source, title, detail, severity, dedup_key, _now_iso()),
        )
        await db.commit()
        return int(cur.lastrowid)


async def close_issue(issue_id: int, reason: str) -> bool:
    """Closes an issue -- ``reason`` is mandatory (same doctrine as
    ``signal_cascade_convergence.record_triage_decision``: a close is a
    decision with real reasoning, never a bare status flip). Returns False
    if the issue doesn't exist or is already closed (idempotent no-op, never
    raises)."""
    if not reason or not reason.strip():
        return False
    await _ensure_table()
    async with aiosqlite.connect(_db_path()) as db:
        cur = await db.execute(
            "UPDATE system_issues SET status = 'closed', closed_at = ?, closed_reason = ? "
            "WHERE id = ? AND status = 'open'",
            (_now_iso(), reason.strip(), issue_id),
        )
        await db.commit()
        return cur.rowcount > 0


async def list_open(*, source: str | None = None, limit: int | None = None) -> list[dict]:
    """Open issues, most severe and most recent first -- the read side for
    the SessionStart hook and for any session wanting the full backlog
    (the hook itself only surfaces a short top-N, this returns everything
    when ``limit`` is None)."""
    await _ensure_table()
    severity_rank = "CASE severity WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END"
    query = f"SELECT * FROM system_issues WHERE status = 'open'"
    params: list = []
    if source:
        query += " AND source = ?"
        params.append(source)
    query += f" ORDER BY {severity_rank} ASC, opened_at DESC"
    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(query, params)).fetchall()
    return [dict(r) for r in rows]


async def count_open() -> int:
    await _ensure_table()
    async with aiosqlite.connect(_db_path()) as db:
        row = await (
            await db.execute("SELECT COUNT(*) FROM system_issues WHERE status = 'open'")
        ).fetchone()
    return int(row[0]) if row else 0
