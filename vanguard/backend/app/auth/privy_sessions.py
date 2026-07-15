"""Create Aria Market member sessions from Privy / X identity."""

from __future__ import annotations

from datetime import datetime, timezone

import aiosqlite

from app.auth.access_code import DB_PATH, create_session, init_auth_db


async def _ensure_session_columns() -> None:
    await init_auth_db()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS user_links (
                privy_did TEXT PRIMARY KEY,
                twitter_username TEXT NOT NULL,
                linked_at TEXT NOT NULL
            )
            """
        )
        cursor = await db.execute("PRAGMA table_info(sessions)")
        cols = {row[1] for row in await cursor.fetchall()}
        migrations = [
            ("privy_did", "TEXT"),
            ("twitter_username", "TEXT"),
            ("x_linked", "INTEGER NOT NULL DEFAULT 0"),
            ("x_link_skipped", "INTEGER NOT NULL DEFAULT 0"),
            ("via_code", "INTEGER NOT NULL DEFAULT 0"),
        ]
        for name, col_type in migrations:
            if name not in cols:
                await db.execute(f"ALTER TABLE sessions ADD COLUMN {name} {col_type}")
        await db.commit()


async def lookup_linked_handle(privy_did: str) -> str | None:
    """Return member handle for a returning Privy user (no identity token needed)."""
    await _ensure_session_columns()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT twitter_username FROM user_links WHERE privy_did = ?",
            (privy_did,),
        )
        row = await cursor.fetchone()
    return str(row[0]) if row and row[0] else None


async def login_with_privy(
    *,
    privy_did: str,
    twitter_username: str,
    ttl_hours: int = 24,
) -> tuple[str, datetime, bool]:
    """Returns (token, expires, is_new_member) -- `is_new_member` is True only on this
    privy_did's very first link (no prior `user_links` row), used to pick between
    welcome_site_access()/welcome_site_return() (narrative.py) at the call site."""
    await _ensure_session_columns()
    now = datetime.now(timezone.utc).isoformat()

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT twitter_username FROM user_links WHERE privy_did = ?",
            (privy_did,),
        )
        existing = await cursor.fetchone()
        is_new_member = existing is None
        if existing and existing[0] != twitter_username:
            raise ValueError("This X account is already linked to another member profile")

        await db.execute(
            """
            INSERT INTO user_links (privy_did, twitter_username, linked_at)
            VALUES (?, ?, ?)
            ON CONFLICT(privy_did) DO UPDATE SET
                twitter_username = excluded.twitter_username,
                linked_at = excluded.linked_at
            """,
            (privy_did, twitter_username, now),
        )
        await db.commit()

    token, expires = await create_session(ttl_hours=ttl_hours)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE sessions
            SET privy_did = ?, twitter_username = ?, x_linked = 1, x_link_skipped = 0, via_code = 0
            WHERE token = ?
            """,
            (privy_did, twitter_username, token),
        )
        await db.commit()
    return token, expires, is_new_member