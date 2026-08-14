"""Write-audit trail for the vector memory (14/08, #166) — persistent, queryable
record of every accepted OR rejected write attempt into ``aria_cognitive_vectors``.

Deliberately WRITE-only (never a per-search log) — the memory-poisoning risk this
defends against (OWASP ASI06, docs/HANDOFF_LANCEDB.md) is content written and later
retrieved, not the act of reading itself. A rejected attempt (injection marker,
schema validation failure) is logged with the SAME weight as an accepted one:
knowing an attack was repelled is as important as knowing what was kept.

Same pattern as ``system_issues.py`` (aiosqlite, ``aria_db_path()``, fail-safe —
never raises into the caller, a broken audit log must never block a real write)."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import aiosqlite

from aria_core.paths import aria_db_path

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())


def _db_path() -> str:
    return DB_PATH


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _ensure_table() -> None:
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS lancedb_write_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                entry_type TEXT NOT NULL,
                written_by TEXT NOT NULL,
                accepted INTEGER NOT NULL,
                reason TEXT NOT NULL DEFAULT ''
            )
            """
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_lancedb_write_audit_ts "
            "ON lancedb_write_audit (ts)"
        )
        await db.commit()


async def log_write_attempt(
    entry_type: str, written_by: str, *, accepted: bool, reason: str = ""
) -> None:
    """Records one write attempt. Never raises — a broken audit DB must never
    block or fail the real store() it's observing."""
    try:
        await _ensure_table()
        async with aiosqlite.connect(_db_path()) as db:
            await db.execute(
                "INSERT INTO lancedb_write_audit (ts, entry_type, written_by, accepted, reason) "
                "VALUES (?, ?, ?, ?, ?)",
                (_now_iso(), entry_type, written_by, 1 if accepted else 0, reason[:500]),
            )
            await db.commit()
    except Exception as exc:
        logger.warning("lancedb write audit failed (non-blocking): %s", exc)


async def recent_write_attempts(limit: int = 50) -> list[dict]:
    """Most recent attempts first, accepted and rejected alike. ``[]`` on any
    failure — a diagnostic read must never raise either."""
    try:
        await _ensure_table()
        async with aiosqlite.connect(_db_path()) as db:
            db.row_factory = aiosqlite.Row
            rows = await (
                await db.execute(
                    "SELECT ts, entry_type, written_by, accepted, reason "
                    "FROM lancedb_write_audit ORDER BY id DESC LIMIT ?",
                    (max(1, min(limit, 500)),),
                )
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception as exc:
        logger.warning("lancedb write audit read failed: %s", exc)
        return []
