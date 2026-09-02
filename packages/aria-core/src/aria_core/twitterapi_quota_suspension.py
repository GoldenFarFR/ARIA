"""Self-arming suspension for TwitterAPI.io -- acts on 429s instead of logging them.

Why this exists (02/09, operator: *"au dela de la securite il faut quelque
chose qui s'en occupe pour regler le probleme et prendre une decision"*).
Counting rate limits and surfacing them in a return value is detection; it
leaves the problem to whoever reads the logs next. This decides.

**Deliberately the same shape as ``goplus_quota_suspension``**, not a new
design: same ``SingleRowStore`` backend, same arm-after-N / disarm-on-first-
success contract, same exponential backoff, same one-shot Telegram notice.
Two suspensions that behave differently for the same class of problem would
be two things to remember instead of one -- and the repo already paid for
that lesson when the holder-concentration bypass and the GoPlus one were
found to be the same mechanism written twice.

**What differs, and why.** GoPlus backs off in HALF-DAYS because a monthly CU
quota can legitimately stay dead for days. TwitterAPI.io is prepaid per tweet
($0.15/1k) with no monthly ceiling, so a 429 here means *instantaneous* rate
pressure (the documented ceiling is 200 QPS), not an exhausted allowance. The
window therefore starts at MINUTES: suspending for 12 hours over a burst
would throw away a whole day of social collection for a problem that clears
itself in seconds.

**Only a real 429 arms this.** A network failure, a 500, or an unreadable body
are faults, not capacity signals -- routing them here would suspend collection
because a server hiccuped. Same discipline as the GoPlus module's refusal to
treat a generic failure as a quota signal.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from aria_core.paths import aria_db_path
from aria_core.single_row_state import SingleRowStore, parse_iso

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())
_TABLE = "twitterapi_quota_suspension"
_COLUMNS = {
    "consecutive_rate_limit_failures": "INTEGER NOT NULL DEFAULT 0",
    "suspended_until": "TEXT",
    "current_backoff_seconds": "INTEGER NOT NULL DEFAULT 0",
}

# Three consecutive 429s, like GoPlus: one is noise, two can be a coincidence
# of two parallel cycles, three is a pattern worth acting on.
_ARM_AFTER_CONSECUTIVE_RATE_LIMIT_FAILURES = 3

# Minutes, not hours -- see the module docstring. 5 minutes doubling to a
# 2-hour ceiling: long enough to let a burst clear, short enough that a
# transient throttle never costs a day of collection.
_INITIAL_SUSPEND_SECONDS = 5 * 60
_MAX_SUSPEND_SECONDS = 2 * 3600


def _store() -> SingleRowStore:
    # Rebuilt per call so a test monkeypatching DB_PATH after import is
    # honored -- same reason as the GoPlus module.
    return SingleRowStore(DB_PATH, _TABLE, _COLUMNS)


async def is_suspended() -> bool:
    """Checked BEFORE any network call, so a suspended window costs nothing."""
    row = await _store().read("suspended_until")
    until = parse_iso(row[0]) if row else None
    return until is not None and datetime.now(timezone.utc) < until


async def record_rate_limit_failure() -> bool:
    """Call ONLY on a real HTTP 429. Returns True on the call that arms it."""
    now = datetime.now(timezone.utc)

    def _apply(row):
        prev_failures, prev_backoff = row or (0, 0)
        failures = prev_failures + 1
        suspended_until_value = None
        backoff_value = prev_backoff
        just_armed = False
        if failures >= _ARM_AFTER_CONSECUTIVE_RATE_LIMIT_FAILURES:
            if prev_backoff and prev_backoff > 0:
                # A post-expiry probe failed again: back off further rather
                # than retrying on every subsequent call.
                backoff_value = min(prev_backoff * 2, _MAX_SUSPEND_SECONDS)
            else:
                backoff_value = _INITIAL_SUSPEND_SECONDS
                just_armed = True
            suspended_until_value = (now + timedelta(seconds=backoff_value)).isoformat()
        return (
            {
                "consecutive_rate_limit_failures": failures,
                "suspended_until": suspended_until_value,
                "current_backoff_seconds": backoff_value,
            },
            (just_armed, suspended_until_value),
        )

    just_armed, suspended_until_value = await _store().mutate(
        ("consecutive_rate_limit_failures", "current_backoff_seconds"), _apply
    )
    if just_armed:
        await _notify_armed(suspended_until_value)
    return just_armed


async def _notify_armed(suspended_until_iso: str | None) -> None:
    """One notice, exactly when it arms -- never on each backoff extension."""
    try:
        from aria_core.gateway.telegram_bot import send_message

        until_dt = parse_iso(suspended_until_iso)
        until_str = until_dt.strftime("%H:%M UTC") if until_dt else "?"
        await send_message(
            "🛡️ Collecte X suspendue automatiquement -- "
            f"{_ARM_AFTER_CONSECUTIVE_RATE_LIMIT_FAILURES} rate-limits consécutifs "
            f"sur TwitterAPI.io. Reprise après {until_str} (recul exponentiel si "
            "elle échoue encore, désarmement immédiat dès qu'un appel réussit). "
            "Aucune action requise."
        )
    except Exception as exc:  # noqa: BLE001 -- a failed notice must not block the suspension
        logger.info("twitterapi_quota_suspension: notice failed (%s)", type(exc).__name__)


async def record_success() -> None:
    """A real call succeeded -- reset streak AND backoff, disarm immediately."""
    await _store().write(
        {
            "consecutive_rate_limit_failures": 0,
            "suspended_until": None,
            "current_backoff_seconds": 0,
        }
    )


async def status() -> dict:
    """Current state, for diagnostics -- never inferred from memory."""
    row = await _store().read(
        "consecutive_rate_limit_failures", "suspended_until", "current_backoff_seconds"
    )
    failures, until_iso, backoff = row if row else (0, None, 0)
    until = parse_iso(until_iso)
    return {
        "consecutive_rate_limit_failures": failures or 0,
        "suspended_until": until_iso,
        "suspended_now": bool(until and datetime.now(timezone.utc) < until),
        "current_backoff_seconds": backoff or 0,
    }
