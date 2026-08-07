"""Operator mobile account -- dedicated login (username + password + TOTP) for the
Android fallback channel (Item #201, plan at /root/.claude/plans/fizzy-plotting-map.md).

Never carries ADMIN_API_SECRET to the client -- that pattern is exactly why the old
/cockpit page was removed (commit 7766834b, "repeated operator-secret confusion").
This is a SEPARATE, additive auth path living in vanguard/backend/app/auth/ (like
access_code.py/privy_sessions.py) -- aria_core.public_mode.require_operator (the
legacy server-to-server secret+TOTP path) is never touched or replaced.

Deliberately NO hard lockout after repeated failures: this is a FALLBACK channel
whose whole purpose is availability during an incident (e.g. Telegram down) --
letting an attacker who merely guesses the single username lock the operator out
right when they need it would defeat the channel's own purpose. Only a capped,
progressive slowdown is enforced here; the caller (login route) is responsible for
actually sleeping `login_delay_seconds()` before processing an attempt. A second,
SSH-independent way to reset `failed_attempts` is a Telegram `/unlockmobile`
command (owner-gated, added alongside the CLI `--unlock` flag) -- see
gen-operator-account.py.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone

import aiosqlite

from app.paths import auth_db_path

DB_PATH = str(auth_db_path())

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 64

# Progressive login slowdown -- capped, never an unbounded doubling (would become
# punitive for an operator who simply mistypes a few times in a row).
_LOGIN_DELAYS_SECONDS = (0.0, 2.0, 4.0, 8.0)
_LOGIN_DELAY_CAP_SECONDS = 8.0

DEFAULT_ROLE = "owner"


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS operator_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                password_salt TEXT NOT NULL,
                totp_secret TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'owner',
                failed_attempts INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                last_login_at TEXT
            )
            """
        )
        await db.commit()


def _hash_password(password: str, salt: bytes) -> str:
    derived = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=_SCRYPT_DKLEN,
    )
    return derived.hex()


async def create_or_replace_account(
    *, username: str, password: str, totp_secret: str, role: str = DEFAULT_ROLE,
) -> int:
    """Creates the operator account, or replaces it entirely if `username` already
    exists (re-enrollment) -- resets failed_attempts too, a fresh enrollment is not
    meant to inherit a stale lockout history."""
    await _ensure_table()
    salt = secrets.token_bytes(16)
    password_hash = _hash_password(password, salt)
    now = _now().isoformat()

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO operator_accounts
                (username, password_hash, password_salt, totp_secret, role, failed_attempts, created_at)
            VALUES (?, ?, ?, ?, ?, 0, ?)
            ON CONFLICT(username) DO UPDATE SET
                password_hash = excluded.password_hash,
                password_salt = excluded.password_salt,
                totp_secret = excluded.totp_secret,
                role = excluded.role,
                failed_attempts = 0
            """,
            (username, password_hash, salt.hex(), totp_secret, role, now),
        )
        await db.commit()
        cursor = await db.execute("SELECT id FROM operator_accounts WHERE username = ?", (username,))
        row = await cursor.fetchone()
    return int(row[0])


async def get_account(username: str) -> dict | None:
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM operator_accounts WHERE username = ?", (username,))
        row = await cursor.fetchone()
    return dict(row) if row else None


async def get_the_account() -> dict | None:
    """08/07 -- Privy auth redesign: this channel has exactly ONE operator
    account by design (no username/multi-user concept), so the Privy login
    path needs to bind an invite code to THE account without asking for a
    username. Returns the single row, or None if the account was never
    provisioned (gen-operator-account.py not yet run)."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM operator_accounts LIMIT 1")
        row = await cursor.fetchone()
    return dict(row) if row else None


async def get_account_by_id(account_id: int) -> dict | None:
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM operator_accounts WHERE id = ?", (account_id,))
        row = await cursor.fetchone()
    return dict(row) if row else None


def verify_password(account: dict, password: str) -> bool:
    """Constant-time comparison against the stored hash."""
    salt = bytes.fromhex(account["password_salt"])
    candidate = _hash_password(password, salt)
    return secrets.compare_digest(candidate, account["password_hash"])


def login_delay_seconds(failed_attempts: int) -> float:
    """Delay to impose BEFORE processing a login attempt, capped -- never an
    unbounded doubling. The caller must actually sleep this long; this function
    is pure (no I/O) so it's trivially testable."""
    if failed_attempts <= 0:
        return 0.0
    idx = min(failed_attempts, len(_LOGIN_DELAYS_SECONDS) - 1)
    return min(_LOGIN_DELAYS_SECONDS[idx], _LOGIN_DELAY_CAP_SECONDS)


async def record_login_failure(account_id: int) -> None:
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE operator_accounts SET failed_attempts = failed_attempts + 1 WHERE id = ?",
            (account_id,),
        )
        await db.commit()


async def record_login_success(account_id: int) -> None:
    await _ensure_table()
    now = _now().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE operator_accounts SET failed_attempts = 0, last_login_at = ? WHERE id = ?",
            (now, account_id),
        )
        await db.commit()


async def reset_failed_attempts(username: str) -> bool:
    """Used by both `gen-operator-account.py --unlock` (SSH) and the Telegram
    `/unlockmobile` command (owner-only, no SSH needed) -- two independent ways to
    clear a progressive-slowdown history, since neither is guaranteed to be
    reachable during every possible incident. Returns False if the account doesn't
    exist (never raises)."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "UPDATE operator_accounts SET failed_attempts = 0 WHERE username = ?",
            (username,),
        )
        await db.commit()
        return cursor.rowcount > 0


async def replace_password(username: str, new_password: str) -> bool:
    """Also resets failed_attempts -- a changed password must never leave the
    account stuck behind a stale failure count."""
    account = await get_account(username)
    if account is None:
        return False
    salt = secrets.token_bytes(16)
    password_hash = _hash_password(new_password, salt)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE operator_accounts SET password_hash = ?, password_salt = ?, failed_attempts = 0 WHERE username = ?",
            (password_hash, salt.hex(), username),
        )
        await db.commit()
    return True


async def replace_totp_secret(username: str, new_totp_secret: str) -> bool:
    account = await get_account(username)
    if account is None:
        return False
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE operator_accounts SET totp_secret = ? WHERE username = ?",
            (new_totp_secret, username),
        )
        await db.commit()
    return True
