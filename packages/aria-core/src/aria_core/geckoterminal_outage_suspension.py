"""Auto-armed, auto-expiring GeckoTerminal outage suspension -- same
doctrine as ``goplus_quota_suspension.py`` (built 10/08), applied here after
a real incident found 17/08: 605 consecutive HTTP 429s over 9h40+ with zero
gap, confirmed via a live probe to be an account/IP-level block (a bare,
UNAUTHENTICATED call to the most basic possible endpoint (``/networks``)
also returned 429 -- not a quota exhausted on one specific key, not a
per-endpoint rate limit). The module-level adaptive throttle
(``GeckoTerminalClient._record_rate_limit``) already exists but caps its
own backoff at 3x the floor (12s) -- it keeps retrying forever at a fixed
worst-case pace instead of ever giving the account real time to recover,
which is exactly the failure mode observed: hundreds of wasted, doomed
requests spread across 10 hours, each one plausibly extending whatever
block/quota window is in effect.

Unlike a Blockscout-style multi-hour infra incident (fixed window), this
could be a monthly quota (dead for days) OR a Cloudflare-level burst block
(dead for minutes) -- the real cause was NOT conclusively identified in this
session (dashboard check requested from the operator, see
``docs/HANDOFF_PIPELINE_MOMENTUM.md`` 17/08 entry). Rather than guess, this
module self-calibrates via exponential backoff exactly like
``goplus_quota_suspension.py``: starts short (15min, cheap to be wrong if
the real cause turns out to be a long quota freeze) and doubles on each
post-expiry probe that still fails, capped at 24h (deliberately far below
GoPlus's 48h ceiling -- GeckoTerminal is a load-bearing dependency for the
whole system, better to keep probing more often even at the cost of a few
more wasted 429s than to black out this central provider for two full
days on a wrong guess). A single real success at any point immediately
resets the backoff to its floor and disarms.

SQL plumbing shared via ``single_row_state.SingleRowStore`` -- see that
module's own docstring.

**17/08, ``db_path`` made overridable (real incident)**: this module used to
hard-code ``aria_db_path()``, so the standalone shadow process (its own
``GeckoTerminalClient`` instance, running outside Docker) shared the exact
same suspension state as the prod container -- a 429 streak seen ONLY by the
shadow could arm a suspension that then silently blocked prod's own
GeckoTerminal calls too, on top of the separate ``database is locked`` issue
this same incident surfaced (see ``paths.shadow_db_path``). Every function
now takes an optional ``db_path`` (default ``None`` -- resolves to
``aria_db_path()``, i.e. every existing call site keeps its exact prior
behavior unchanged); only ``shadow_persistent.py``'s dedicated client passes
``shadow_db_path()`` instead, giving it its own independent circuit breaker."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from aria_core.paths import aria_db_path
from aria_core.single_row_state import SingleRowStore, parse_iso

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())

# 5 consecutive real 429s (never a generic network failure) before the FIRST
# armament -- higher than GoPlus's 3 since GeckoTerminal carries far more
# legitimate traffic and the module-level adaptive throttle already absorbs
# an isolated burst; this only fires once THAT mechanism has clearly failed
# to help.
_ARM_AFTER_CONSECUTIVE_RATE_LIMIT_FAILURES = 5

_INITIAL_SUSPEND_SECONDS = 15 * 60
_MAX_SUSPEND_SECONDS = 24 * 3600

_TABLE = "geckoterminal_outage_suspension_state"
_COLUMNS = [
    ("consecutive_rate_limit_failures", "INTEGER NOT NULL DEFAULT 0", 0),
    ("suspended_until", "TEXT", None),
    ("current_backoff_seconds", "INTEGER NOT NULL DEFAULT 0", 0),
]


def _store(db_path: str | None = None) -> SingleRowStore:
    return SingleRowStore(db_path or DB_PATH, _TABLE, _COLUMNS)


async def is_suspended(db_path: str | None = None) -> bool:
    """Checked FIRST, before even attempting a network call -- fail-closed,
    never a wasted request against an account already known to be blocked."""
    row = await _store(db_path).read("suspended_until")
    until = parse_iso(row[0]) if row else None
    return until is not None and datetime.now(timezone.utc) < until


async def record_rate_limit_failure(db_path: str | None = None) -> bool:
    """Called only on a REAL HTTP 429 -- never a generic timeout/5xx, which
    stays governed by the client's own existing retry logic, zero coupling
    to it. Returns True only on the call that ARMS the suspension for the
    first time (caller logs a loud WARNING only in that case)."""
    now = datetime.now(timezone.utc)

    def _apply(row):
        prev_failures, prev_backoff = row or (0, 0)
        failures = prev_failures + 1

        suspended_until_value = None
        backoff_value = prev_backoff
        just_armed = False
        if failures >= _ARM_AFTER_CONSECUTIVE_RATE_LIMIT_FAILURES:
            if prev_backoff and prev_backoff > 0:
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

    just_armed, suspended_until_value = await _store(db_path).mutate(
        ("consecutive_rate_limit_failures", "current_backoff_seconds"), _apply
    )
    if just_armed:
        # 17/08 -- labels the source so a shadow-armed suspension is never
        # mistaken for a prod-affecting one in the Telegram notification
        # (the two now have fully independent state, see module docstring).
        await _notify_armed(suspended_until_value, source="shadow" if db_path else "prod")
    return just_armed


async def _notify_armed(suspended_until_iso: str | None, *, source: str = "prod") -> None:
    from aria_core.gateway.telegram_bot import send_message

    until_dt = parse_iso(suspended_until_iso)
    until_str = until_dt.strftime("%Y-%m-%d %H:%M UTC") if until_dt else "?"
    scope = (
        "-- n'affecte QUE le shadow paper-only, jamais le pipeline de trading reel"
        if source == "shadow" else ""
    )
    await send_message(
        f"🛡️ Suspension automatique GeckoTerminal activée ({source}) {scope}-- "
        f"{_ARM_AFTER_CONSECUTIVE_RATE_LIMIT_FAILURES} rate-limits consécutifs malgré le throttle "
        "adaptatif deja au maximum. Cause reelle (quota mensuel vs blocage IP temporaire) pas "
        f"confirmee -- backoff exponentiel auto-calibrant. Prochaine tentative apres {until_str} "
        "(recul si elle echoue encore, reprise immediate des qu'un appel reussit). "
        "Aucune action requise."
    )


async def record_success(db_path: str | None = None) -> None:
    """A real call succeeded -- reset the streak AND the backoff to their
    floor, disarm immediately (never wait for the window to expire)."""
    await _store(db_path).write(
        {
            "consecutive_rate_limit_failures": 0,
            "suspended_until": None,
            "current_backoff_seconds": 0,
        }
    )
