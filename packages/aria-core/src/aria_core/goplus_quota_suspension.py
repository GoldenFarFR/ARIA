"""Auto-armed, auto-expiring GoPlus monthly-quota suspension -- replaces
``GOPLUS_QUOTA_SUSPENDED_UNTIL`` (a hardcoded date constant in
``services/goplus.py``, required a code edit + commit + deploy every time
the real renewal date needed correcting -- same operational friction as
the pre-10/08 holder-concentration bypass, found the same day while
extending that automation to a second manual mechanism in the codebase).

Detects a SUSTAINED quota exhaustion from the client's OWN precise
rate-limit signal (HTTP 429 or the GoPlus-specific ``{"code": 4029}``
body -- never a generic network failure, which stays governed by
``GoPlusClient``'s own existing circuit breaker, zero coupling to it).
Arms itself once ``_ARM_AFTER_CONSECUTIVE_RATE_LIMIT_FAILURES`` is
reached, disarms itself the instant a real call succeeds again.

Unlike the Blockscout outage bypass (a multi-hour infra incident, fixed
window), a monthly CU quota can legitimately stay dead for DAYS -- probing
every single call during that time would be wasteful and pointless.
Instead, the suspension window backs off exponentially on each
re-armament (``_INITIAL_SUSPEND_SECONDS`` 12h -> doubles each time the
window's first post-expiry probe still fails, capped at
``_MAX_SUSPEND_SECONDS`` 48h) -- a single real success at any point
immediately resets the backoff to its floor and disarms."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import aiosqlite

from aria_core.paths import aria_db_path

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())

# 3 consecutive real rate-limit signals (never generic failures) before the
# FIRST armament -- avoids suspending on a single isolated 429/4029.
_ARM_AFTER_CONSECUTIVE_RATE_LIMIT_FAILURES = 3

# 10/08 -- first suspension window; doubles on each further probe failure
# (see record_rate_limit_failure), capped so a dead-for-weeks quota never
# waits longer than 48h between probes.
_INITIAL_SUSPEND_SECONDS = 12 * 3600
_MAX_SUSPEND_SECONDS = 48 * 3600

_ROW_ID = 1


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS goplus_quota_suspension_state (
                id INTEGER PRIMARY KEY,
                consecutive_rate_limit_failures INTEGER NOT NULL DEFAULT 0,
                suspended_until TEXT,
                current_backoff_seconds INTEGER NOT NULL DEFAULT 0,
                last_updated_at TEXT NOT NULL
            )
            """
        )
        await db.execute(
            "INSERT OR IGNORE INTO goplus_quota_suspension_state "
            "(id, consecutive_rate_limit_failures, suspended_until, current_backoff_seconds, last_updated_at) "
            "VALUES (?, 0, NULL, 0, ?)",
            (_ROW_ID, datetime.now(timezone.utc).isoformat()),
        )
        await db.commit()


def _parse(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None


async def is_suspended() -> bool:
    """Checked FIRST, before even attempting a network call -- same
    short-circuit spirit as the constant it replaces."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (
            await db.execute(
                "SELECT suspended_until FROM goplus_quota_suspension_state WHERE id = ?",
                (_ROW_ID,),
            )
        ).fetchone()
    until = _parse(row[0]) if row else None
    return until is not None and datetime.now(timezone.utc) < until


async def record_rate_limit_failure() -> bool:
    """Called only on a REAL rate-limit signal (HTTP 429 or GoPlus code
    4029) -- never a generic failure. Returns True only on the call that
    ARMS the suspension for the first time (caller logs a loud WARNING
    only in that case)."""
    await _ensure_table()
    now = datetime.now(timezone.utc)
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (
            await db.execute(
                "SELECT consecutive_rate_limit_failures, current_backoff_seconds "
                "FROM goplus_quota_suspension_state WHERE id = ?",
                (_ROW_ID,),
            )
        ).fetchone()
        prev_failures, prev_backoff = row or (0, 0)
        failures = prev_failures + 1

        suspended_until_value = None
        backoff_value = prev_backoff
        just_armed = False
        if failures >= _ARM_AFTER_CONSECUTIVE_RATE_LIMIT_FAILURES:
            if prev_backoff and prev_backoff > 0:
                # Already suspended at least once since the last success --
                # this failure is a post-expiry probe that failed again,
                # back off further rather than probing every single call.
                backoff_value = min(prev_backoff * 2, _MAX_SUSPEND_SECONDS)
            else:
                backoff_value = _INITIAL_SUSPEND_SECONDS
                just_armed = True
            suspended_until_value = (now + timedelta(seconds=backoff_value)).isoformat()

        await db.execute(
            "UPDATE goplus_quota_suspension_state SET consecutive_rate_limit_failures = ?, "
            "suspended_until = ?, current_backoff_seconds = ?, last_updated_at = ? WHERE id = ?",
            (failures, suspended_until_value, backoff_value, now.isoformat(), _ROW_ID),
        )
        await db.commit()
    return just_armed


async def record_success() -> None:
    """A real call succeeded -- reset the streak AND the backoff to their
    floor, disarm immediately (never wait for the window to expire)."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE goplus_quota_suspension_state SET consecutive_rate_limit_failures = 0, "
            "suspended_until = NULL, current_backoff_seconds = 0, last_updated_at = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), _ROW_ID),
        )
        await db.commit()
