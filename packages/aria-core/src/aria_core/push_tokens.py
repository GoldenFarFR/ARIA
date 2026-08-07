"""Expo push tokens for the operator mobile app (Item #201 follow-up, 07/08).

Single-operator system -- no account scoping needed, just the raw Expo push
token (already unique per app install) as primary key. Same storage pattern
as member_memory.py: reads/writes the auth DB the host (vanguard/backend)
points to via ``host_hooks.auth_db_path()``, never aria-core's own DATA_DIR
(this table is operator-identity data, lives next to operator_sessions).
"""

from __future__ import annotations

from datetime import datetime, timezone

import aiosqlite

from aria_core.integrations.host_hooks import auth_db_path, init_auth_db


async def _ensure_table() -> None:
    await init_auth_db()
    async with aiosqlite.connect(str(auth_db_path())) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS push_tokens (
                token TEXT PRIMARY KEY,
                installation_id TEXT,
                registered_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL
            )
            """
        )
        await db.commit()


async def register_push_token(token: str, installation_id: str | None = None) -> None:
    """Upsert -- called on every app launch (idempotent), not just first
    install, so ``last_seen_at`` stays a real liveness signal."""
    token = (token or "").strip()
    if not token:
        return
    await _ensure_table()
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(str(auth_db_path())) as db:
        await db.execute(
            """
            INSERT INTO push_tokens (token, installation_id, registered_at, last_seen_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(token) DO UPDATE SET
                installation_id = excluded.installation_id,
                last_seen_at = excluded.last_seen_at
            """,
            (token, installation_id, now, now),
        )
        await db.commit()


async def list_push_tokens() -> list[str]:
    await _ensure_table()
    async with aiosqlite.connect(str(auth_db_path())) as db:
        cursor = await db.execute("SELECT token FROM push_tokens")
        rows = await cursor.fetchall()
    return [str(row[0]) for row in rows]


async def unregister_push_token(token: str) -> None:
    """Called when Expo reports a token as dead (DeviceNotRegistered) --
    never guessed speculatively, only on that explicit signal."""
    await _ensure_table()
    async with aiosqlite.connect(str(auth_db_path())) as db:
        await db.execute("DELETE FROM push_tokens WHERE token = ?", (token,))
        await db.commit()
