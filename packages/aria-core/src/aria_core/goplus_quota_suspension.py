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
immediately resets the backoff to its floor and disarms.

SQL plumbing shared with ``holder_concentration_outage_bypass.py`` via
``single_row_state.SingleRowStore`` (factored out 10/08, same day both
were built with the same hand-duplicated shape) -- the arm/disarm POLICY
below (exponential backoff, doubling) stays specific to this module."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from aria_core.paths import aria_db_path
from aria_core.single_row_state import SingleRowStore, parse_iso

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

_TABLE = "goplus_quota_suspension_state"
_COLUMNS = [
    ("consecutive_rate_limit_failures", "INTEGER NOT NULL DEFAULT 0", 0),
    ("suspended_until", "TEXT", None),
    ("current_backoff_seconds", "INTEGER NOT NULL DEFAULT 0", 0),
]


def _store() -> SingleRowStore:
    # Constructed fresh on every call (cheap -- just 3 attributes) so a
    # test monkeypatching the module-level DB_PATH after import is always
    # honored, never frozen at import time.
    return SingleRowStore(DB_PATH, _TABLE, _COLUMNS)


async def is_suspended() -> bool:
    """Checked FIRST, before even attempting a network call -- same
    short-circuit spirit as the constant it replaces."""
    row = await _store().read("suspended_until")
    until = parse_iso(row[0]) if row else None
    return until is not None and datetime.now(timezone.utc) < until


async def record_rate_limit_failure() -> bool:
    """Called only on a REAL rate-limit signal (HTTP 429 or GoPlus code
    4029) -- never a generic failure. Returns True only on the call that
    ARMS the suspension for the first time (caller logs a loud WARNING
    only in that case)."""
    now = datetime.now(timezone.utc)

    def _apply(row):
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

        values = {
            "consecutive_rate_limit_failures": failures,
            "suspended_until": suspended_until_value,
            "current_backoff_seconds": backoff_value,
        }
        return values, (just_armed, suspended_until_value)

    just_armed, suspended_until_value = await _store().mutate(
        ("consecutive_rate_limit_failures", "current_backoff_seconds"), _apply
    )
    if just_armed:
        await _notify_armed(suspended_until_value)
    return just_armed


async def _notify_armed(suspended_until_iso: str | None) -> None:
    """10/08 -- one-time Telegram notice exactly when the suspension first
    arms (never repeated on subsequent backoff extensions -- caller only
    invokes this on ``just_armed``)."""
    from aria_core.gateway.telegram_bot import send_message

    until_dt = parse_iso(suspended_until_iso)
    until_str = until_dt.strftime("%Y-%m-%d %H:%M UTC") if until_dt else "?"
    await send_message(
        "🛡️ Suspension automatique GoPlus activée -- quota CU probablement épuisé "
        f"({_ARM_AFTER_CONSECUTIVE_RATE_LIMIT_FAILURES} rate-limits consécutifs). "
        f"Prochaine tentative après {until_str} (recul exponentiel si elle échoue encore, "
        "réarmement immédiat dès qu'un appel réussit). Aucune action requise."
    )


async def record_success() -> None:
    """A real call succeeded -- reset the streak AND the backoff to their
    floor, disarm immediately (never wait for the window to expire)."""
    await _store().write(
        {
            "consecutive_rate_limit_failures": 0,
            "suspended_until": None,
            "current_backoff_seconds": 0,
        }
    )
