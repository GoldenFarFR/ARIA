"""Append-only auth event log for the operator mobile account (Item #201) --
same doctrine as `aria_core.kill_incident_log`: never a secret/password/TOTP in
clear, best-effort (a write failure must never break the login/logout it merely
records)."""
from __future__ import annotations

from datetime import datetime, timezone

import aiosqlite

from app.paths import auth_db_path

DB_PATH = str(auth_db_path())

EVENT_LOGIN_SUCCESS = "login_success"
EVENT_LOGIN_FAILURE = "login_failure"
EVENT_LOGOUT = "logout"
EVENT_SESSION_REVOKED = "session_revoked"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS operator_auth_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                username TEXT,
                ip TEXT,
                installation_id TEXT,
                recorded_at TEXT NOT NULL
            )
            """
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_operator_auth_log_recorded_at "
            "ON operator_auth_log (recorded_at)"
        )
        await db.commit()


async def record_event(
    *, event_type: str, username: str | None = None, ip: str | None = None,
    installation_id: str | None = None,
) -> None:
    try:
        await _ensure_table()
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO operator_auth_log (event_type, username, ip, installation_id, recorded_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (event_type, username, ip, installation_id, _now()),
            )
            await db.commit()
    except Exception:  # noqa: BLE001 -- best-effort, never blocks the caller
        pass


async def list_events(limit: int = 50) -> list[dict]:
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT event_type, username, ip, installation_id, recorded_at "
            "FROM operator_auth_log ORDER BY recorded_at DESC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]
