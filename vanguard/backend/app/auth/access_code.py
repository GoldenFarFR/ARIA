from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiosqlite

from app.paths import auth_db_path

_DB_FILE = auth_db_path()
DB_PATH = str(_DB_FILE)


async def init_auth_db() -> None:
    _DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )
            """
        )
        await db.commit()


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def create_session(ttl_hours: int = 24) -> tuple[str, datetime]:
    await init_auth_db()
    token = secrets.token_urlsafe(32)
    now = _now()
    expires = now + timedelta(hours=ttl_hours)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO sessions (token, created_at, expires_at) VALUES (?, ?, ?)",
            (token, now.isoformat(), expires.isoformat()),
        )
        await db.commit()

    return token, expires


async def verify_session(token: str | None) -> bool:
    if not token:
        return False
    await init_auth_db()

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT expires_at FROM sessions WHERE token = ?",
            (token,),
        )
        row = await cursor.fetchone()

    if not row:
        return False

    expires = datetime.fromisoformat(row[0])
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    return _now() < expires


async def purge_expired() -> None:
    await init_auth_db()
    now = _now().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
        await db.commit()