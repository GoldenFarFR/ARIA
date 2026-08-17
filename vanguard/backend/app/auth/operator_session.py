"""Operator mobile sessions -- Bearer token composite `session_id.secret`
(Stripe/GitHub-like), for the Android fallback channel (Item #201).

`session_id` (UUIDv4, high entropy -- never a plain auto-increment, which would be
enumerable) identifies the row directly; `secret` (256 bits, `secrets.token_urlsafe`)
is never stored in clear, only its SHA-256 hash is (scrypt is reserved for the
human password in operator_account.py -- a secret already at 256 bits of entropy
gains nothing from an intentionally slow KDF). Verification: lookup by
`session_id`, constant-time comparison of the hash.

Sliding-window expiration: a session doesn't die as long as the app is reopened
regularly, but the row is only rewritten if `expires_at < now + _RENEWAL_THRESHOLD`
-- never on every single check, which would otherwise write to disk on every app
launch for no real benefit.
"""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import aiosqlite

from app.paths import auth_db_path

DB_PATH = str(auth_db_path())

# 07/08 -- operator request: never see the login screen again once signed in
# once, only the local biometric lock (biometricLock.ts) gates re-entry from
# then on. 10 years, not a literal "no expiry" sentinel (would need a NULL/
# special-case path threaded through every comparison below for no real
# gain) -- same practical effect, sliding window still renews it further out
# on every use via _RENEWAL_THRESHOLD below.
SESSION_TTL = timedelta(days=3650)
_RENEWAL_THRESHOLD = timedelta(hours=48)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS operator_sessions (
                session_id TEXT PRIMARY KEY,
                account_id INTEGER NOT NULL,
                secret_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                revoked_at TEXT,
                last_seen_at TEXT,
                created_ip TEXT,
                last_seen_ip TEXT,
                device_label TEXT,
                user_agent TEXT,
                installation_id TEXT
            )
            """
        )
        # 08/07 -- Privy auth redesign: a "permanent" session (SESSION_TTL
        # above) is only safe long-term if SOMETHING re-proves identity
        # periodically -- last_totp_reverify_at tracks the last time this
        # specific session did (see needs_totp_reverify below). Migration
        # (not part of CREATE TABLE) because the table already has rows in
        # prod -- same pattern as privy_sessions.py's _ensure_session_columns.
        cursor = await db.execute("PRAGMA table_info(operator_sessions)")
        cols = {row[1] for row in await cursor.fetchall()}
        if "last_totp_reverify_at" not in cols:
            await db.execute(
                "ALTER TABLE operator_sessions ADD COLUMN last_totp_reverify_at TEXT"
            )
        # 17/08 -- operator decision ("le dashboard devrait n'etre qu'une
        # lecture"): a session can now be minted READ-ONLY, so a token stolen
        # off the operator's PC (his stated threat: a worm on that machine)
        # cannot drive the action routes. Same additive-migration pattern as
        # the column above -- prod rows already exist.
        if "scope" not in cols:
            await db.execute("ALTER TABLE operator_sessions ADD COLUMN scope TEXT")
        await db.commit()


# Session scopes. A NULL scope (every session minted before 17/08) means
# FULL -- this migration must never silently downgrade the operator's live
# phone session. Any UNRECOGNISED value, however, fails CLOSED to read-only:
# a corrupted or tampered column should cost read access, never grant write.
SCOPE_FULL = "full"
SCOPE_READ_ONLY = "read_only"


def is_read_only(session: dict) -> bool:
    """Pure function over an already-fetched session row (same style as
    ``needs_totp_reverify`` -- never a second DB round trip)."""
    scope = session.get("scope")
    if scope is None or scope == SCOPE_FULL:
        return False
    return True


def _hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _split_token(token: str) -> tuple[str, str] | None:
    if not token or "." not in token:
        return None
    session_id, _, secret = token.partition(".")
    if not session_id or not secret:
        return None
    return session_id, secret


async def create_operator_session(
    *,
    account_id: int,
    installation_id: str | None = None,
    user_agent: str | None = None,
    ip: str | None = None,
    device_label: str | None = None,
    scope: str = SCOPE_FULL,
) -> str:
    """Returns the full Bearer token (`session_id.secret`) -- the ONLY time the
    raw secret exists; only its hash is persisted.

    ``scope=SCOPE_READ_ONLY`` mints a token that the action routes refuse
    (see ``require_full_session`` in operator_mobile). Anything other than the
    two known scopes is stored as read-only rather than trusted -- a caller
    passing a typo'd scope must not accidentally get write access."""
    if scope not in (SCOPE_FULL, SCOPE_READ_ONLY):
        scope = SCOPE_READ_ONLY
    await _ensure_table()
    session_id = str(uuid.uuid4())
    secret = secrets.token_urlsafe(32)
    now = _now()
    expires = now + SESSION_TTL

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO operator_sessions
                (session_id, account_id, secret_hash, created_at, expires_at,
                 last_seen_at, created_ip, last_seen_ip, device_label, user_agent, installation_id,
                 last_totp_reverify_at, scope)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id, account_id, _hash_secret(secret), now.isoformat(), expires.isoformat(),
                now.isoformat(), ip, ip, device_label, user_agent, installation_id,
                # 08/07 -- a fresh login (password or Privy, both require their
                # own real-time proof of identity) counts as an implicit
                # reverify -- the 30-day clock starts at creation, not at
                # None/never (which would demand a redundant TOTP prompt on
                # the very next launch).
                now.isoformat(),
                scope,
            ),
        )
        await db.commit()

    return f"{session_id}.{secret}"


# 08/07 -- Privy auth redesign: on top of the "permanent" sliding session
# above, the operator asked for a periodic TOTP re-check every 30 days --
# bounds how long a compromised device/session stays usable even though it
# never expires on its own otherwise.
TOTP_REVERIFY_INTERVAL = timedelta(days=30)


def needs_totp_reverify(session: dict) -> bool:
    """Pure function over an already-fetched session row (verify_operator_
    session's return value) -- never a second DB round trip just to check
    this. Missing/unparsable last_totp_reverify_at fails CLOSED (reverify
    required) -- the same "when in doubt" doctrine as everywhere else in this
    module, never silently trusted."""
    raw = session.get("last_totp_reverify_at")
    if not raw:
        return True
    try:
        last = _parse_iso(raw)
    except ValueError:
        return True
    return _now() - last >= TOTP_REVERIFY_INTERVAL


async def mark_totp_reverified(session_id: str) -> None:
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE operator_sessions SET last_totp_reverify_at = ? WHERE session_id = ?",
            (_now().isoformat(), session_id),
        )
        await db.commit()


async def verify_operator_session(token: str | None, *, ip: str | None = None) -> dict | None:
    """Verifies a Bearer token, updates `last_seen_at`/`last_seen_ip` on EVERY
    successful check (even when the sliding-window expiry isn't renewed -- a
    genuine activity trail, distinct from the expiration logic), and renews
    `expires_at` only if it's within `_RENEWAL_THRESHOLD` of expiring. Returns
    the session row (dict) on success, None otherwise -- never raises."""
    parsed = _split_token(token or "")
    if parsed is None:
        return None
    session_id, secret = parsed

    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM operator_sessions WHERE session_id = ?", (session_id,),
        )
        row = await cursor.fetchone()
    if row is None:
        return None
    session = dict(row)

    if not secrets.compare_digest(_hash_secret(secret), session["secret_hash"]):
        return None
    if session["revoked_at"] is not None:
        return None

    now = _now()
    expires_at = _parse_iso(session["expires_at"])
    if now >= expires_at:
        return None

    new_expires_at = expires_at
    if expires_at - now < _RENEWAL_THRESHOLD:
        new_expires_at = now + SESSION_TTL

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE operator_sessions SET last_seen_at = ?, last_seen_ip = ?, expires_at = ? WHERE session_id = ?",
            (now.isoformat(), ip, new_expires_at.isoformat(), session_id),
        )
        await db.commit()

    session["expires_at"] = new_expires_at.isoformat()
    session["last_seen_at"] = now.isoformat()
    session["last_seen_ip"] = ip
    return session


async def revoke_operator_session(token_or_session_id: str) -> bool:
    """Accepts either a full token or a bare session_id -- logout only ever has
    the token; revocation from an admin/CLI path may only have the session_id."""
    parsed = _split_token(token_or_session_id)
    session_id = parsed[0] if parsed else token_or_session_id
    await _ensure_table()
    now = _now().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "UPDATE operator_sessions SET revoked_at = ? WHERE session_id = ? AND revoked_at IS NULL",
            (now, session_id),
        )
        await db.commit()
        return cursor.rowcount > 0


async def revoke_all_operator_sessions(account_id: int) -> int:
    """Called on password change AND on TOTP re-enrollment -- a changed TOTP must
    invalidate existing sessions exactly like a changed password. Returns the
    number of sessions actually revoked."""
    await _ensure_table()
    now = _now().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "UPDATE operator_sessions SET revoked_at = ? WHERE account_id = ? AND revoked_at IS NULL",
            (now, account_id),
        )
        await db.commit()
        return cursor.rowcount


async def purge_expired() -> int:
    """Opportunistically called from login and from session verification, PLUS a
    dedicated daily cron -- never relying on just one of the two, so the table
    never grows unbounded between logins."""
    await _ensure_table()
    now = _now().isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("DELETE FROM operator_sessions WHERE expires_at < ?", (now,))
        await db.commit()
        return cursor.rowcount
