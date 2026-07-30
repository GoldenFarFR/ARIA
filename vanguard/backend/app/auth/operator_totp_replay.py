"""One-time-use guard for the operator mobile TOTP codes (Item #201, Phase 3 --
plan at /root/.claude/plans/fizzy-plotting-map.md).

`aria_core.admin_totp.verify_totp` is stateless by design (a pure RFC 6238
check): the same code stays valid for its whole ±1-step tolerance window, so a
code captured once could otherwise be replayed against /stop or /resume -- the
two routes that can actually hurt. This module is the missing "used once" state.

Why a dedicated table with `UNIQUE(account_id, totp_code)` rather than a
read-then-write counter: the database itself rejects the second INSERT of the
same pair, so two simultaneous calls carrying the same code can never both pass
a check-then-mark race. No application-level locking to get right.

Scope: the MOBILE account path only (session + that account's own TOTP secret).
The legacy server-to-server path (`X-Admin-Secret`/`X-Admin-Totp`, a DIFFERENT
`ADMIN_TOTP_SECRET`) keeps its existing protection without this extra counter --
an assumed difference in level, documented in the plan: that path serves
scripts, not a human app reusing one code repeatedly.

Never stores anything secret in the long run: a consumed TOTP code is dead
material by construction (`verify_totp` can no longer accept it once its window
has passed), and rows are purged as soon as they expire.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import aiosqlite

from app.paths import auth_db_path

DB_PATH = str(auth_db_path())

# 30s step with verify_totp's ±1-step tolerance => a code can be accepted over a
# 90s span at most. Keeping a used code exactly that long is enough; past it, the
# row protects nothing verify_totp would still accept.
REPLAY_WINDOW_SECONDS = 90


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS operator_used_totp_codes (
                account_id INTEGER NOT NULL,
                totp_code TEXT NOT NULL,
                used_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                UNIQUE(account_id, totp_code)
            )
            """
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_operator_used_totp_expires_at "
            "ON operator_used_totp_codes (expires_at)"
        )
        await db.commit()


async def purge_expired() -> int:
    """Drops rows whose code can no longer be accepted by `verify_totp` anyway.
    Called from `claim_code` so the table can never grow unbounded, without
    depending on a separate cron being alive."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "DELETE FROM operator_used_totp_codes WHERE expires_at < ?", (_now().isoformat(),),
        )
        await db.commit()
        return cursor.rowcount


async def claim_code(*, account_id: int, totp_code: str) -> bool:
    """Consumes a TOTP code for this account. True on first use, False if the
    code was already consumed (replay) -- decided by the UNIQUE constraint, not
    by a prior SELECT, so concurrent callers can never both win.

    Call this only AFTER `verify_totp` accepted the code: an invalid code must
    never leave a row behind (it would let an attacker fill the table with
    garbage, and there is nothing to protect about a code that was never valid).
    """
    now = _now()
    await purge_expired()
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO operator_used_totp_codes (account_id, totp_code, used_at, expires_at) "
                "VALUES (?, ?, ?, ?)",
                (
                    account_id,
                    totp_code,
                    now.isoformat(),
                    (now + timedelta(seconds=REPLAY_WINDOW_SECONDS)).isoformat(),
                ),
            )
            await db.commit()
    except aiosqlite.IntegrityError:
        return False
    return True
