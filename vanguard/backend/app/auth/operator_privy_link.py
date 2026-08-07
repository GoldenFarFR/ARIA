"""Binds a single Privy identity to the operator account (07/08, mobile app
auth redesign) -- MetaMask-style flow: a one-time invite code proves "this is
really the operator" ONCE, then the Privy identity IS the login forever after.

Deliberately separate from vanguard/src's own privy_sessions.py (Aria Market
member sessions, multi-member by design) -- this app has exactly ONE
authorized operator, so `operator_privy_link` enforces a single row rather
than a table of members. Also separate from operator_account.py's
password+TOTP account (untouched, stays the legacy/recovery path) -- this
module only ever produces the (privy_did -> account_id) binding; the caller
is responsible for creating the actual operator_session afterward via the
existing operator_session.create_operator_session, same as a password login.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone

import aiosqlite

from app.paths import auth_db_path

DB_PATH = str(auth_db_path())

# Short enough to type/read aloud if relayed verbally, long enough (5 bytes ->
# 8 base32-ish chars via token_hex on 5 bytes = 10 hex chars) that guessing it
# before the operator uses it is not a realistic attack -- this code proves
# possession of a channel the operator already controls (Telegram), it is not
# itself the sole security boundary.
_INVITE_CODE_BYTES = 5


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _ensure_tables() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS operator_privy_link (
                privy_did TEXT PRIMARY KEY,
                account_id INTEGER NOT NULL,
                linked_at TEXT NOT NULL
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS operator_invite_codes (
                code TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                used_at TEXT,
                used_by_privy_did TEXT
            )
            """
        )
        await db.commit()


async def generate_invite_code() -> str:
    """One-time code, never expires on its own (the operator consumes it
    within minutes in practice) -- consumption is what invalidates it, not a
    clock. Multiple codes can be outstanding at once (e.g. a stale one from an
    earlier attempt); each is independently single-use."""
    await _ensure_tables()
    code = secrets.token_hex(_INVITE_CODE_BYTES)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO operator_invite_codes (code, created_at) VALUES (?, ?)",
            (code, _now().isoformat()),
        )
        await db.commit()
    return code


async def get_linked_account_id(privy_did: str) -> int | None:
    """None if this Privy identity has never been linked -- the login route
    reads this to decide whether an invite code is required."""
    await _ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT account_id FROM operator_privy_link WHERE privy_did = ?", (privy_did,),
        )
        row = await cursor.fetchone()
    return int(row[0]) if row else None


async def link_with_invite_code(*, privy_did: str, account_id: int, code: str) -> bool:
    """Atomically consumes `code` and creates the permanent link -- returns
    False (never raises) if the code doesn't exist or was already used, so the
    caller can return a generic 403 without distinguishing the two (same
    doctrine as _require_fresh_totp's shared error message)."""
    await _ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "UPDATE operator_invite_codes SET used_at = ?, used_by_privy_did = ? "
            "WHERE code = ? AND used_at IS NULL",
            (_now().isoformat(), privy_did, code),
        )
        if cursor.rowcount == 0:
            await db.commit()
            return False
        await db.execute(
            """
            INSERT INTO operator_privy_link (privy_did, account_id, linked_at)
            VALUES (?, ?, ?)
            ON CONFLICT(privy_did) DO UPDATE SET account_id = excluded.account_id
            """,
            (privy_did, account_id, _now().isoformat()),
        )
        await db.commit()
    return True
