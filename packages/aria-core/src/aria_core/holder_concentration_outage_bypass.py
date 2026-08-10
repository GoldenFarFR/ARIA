"""Auto-armed, auto-expiring outage bypass for
``momentum_entry._check_holder_concentration`` -- built 10/08 after real
operator feedback during a sustained Blockscout outage. The pre-existing
manual bypass (``ARIA_HOLDER_CONCENTRATION_OUTAGE_BYPASS_UNTIL`` env var,
06/08) required an operator ``.env`` edit + a full redeploy EVERY time it
needed (re)arming -- real friction during a real outage that already
happened twice ("c'est chiant... je vais finir par le supprimer se
truc"). That manual path stays available untouched (an independent,
operator-controlled override) -- this module only adds an AUTOMATIC one.

Detects a SUSTAINED outage from the guardrail's OWN real failure signal
(consecutive calls that exhausted every real path -- free/Pro Blockscout,
the on-chain rescue, the paid x402 fallback) -- zero coupling to
``services/blockscout.py``'s internal circuit-breaker state, this module
only sees what the guardrail itself experiences. Arms itself for a bounded
window once ``_ARM_AFTER_CONSECUTIVE_FAILURES`` is reached, disarms itself
the instant a REAL verdict succeeds again (Blockscout recovered) OR the
window expires -- whichever comes first. Renewed (window pushed forward)
on every failure while already armed, so a longer outage is covered
without operator involvement -- never left armed indefinitely once
failures stop.

SQL plumbing shared with ``goplus_quota_suspension.py`` via
``single_row_state.SingleRowStore`` (factored out 10/08, same day both
were built with the same hand-duplicated shape) -- the arm/disarm POLICY
below (fixed-window backoff, no doubling) stays specific to this module."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from aria_core.paths import aria_db_path
from aria_core.single_row_state import SingleRowStore, parse_iso

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())

# Same threshold as services/blockscout.py's own circuit breaker
# (_FAIL_STREAK_WARN_THRESHOLD) -- 3 consecutive real guardrail failures
# (every path exhausted, not just one provider) already indicates a
# sustained outage rather than an isolated blip.
_ARM_AFTER_CONSECUTIVE_FAILURES = 3

# 10/08 -- a few hours comfortably covers most real Blockscout outages
# observed so far without operator involvement; extended automatically on
# each further failure while already armed (see record_unavailable), so a
# longer outage stays covered -- never left armed once failures stop.
_AUTO_ARM_DURATION_SECONDS = 2 * 3600

_TABLE = "holder_concentration_outage_bypass_state"
_COLUMNS = [
    ("consecutive_failures", "INTEGER NOT NULL DEFAULT 0", 0),
    ("armed_until", "TEXT", None),
    ("armed_at", "TEXT", None),
]


def _store() -> SingleRowStore:
    # Constructed fresh on every call (cheap -- just 3 attributes) so a
    # test monkeypatching the module-level DB_PATH after import is always
    # honored, never frozen at import time.
    return SingleRowStore(DB_PATH, _TABLE, _COLUMNS)


async def is_armed() -> bool:
    """Checked FIRST, before recording anything -- a fresh-armament WARNING
    is only ever logged once (see record_unavailable's return value), never
    on every single check while already armed."""
    row = await _store().read("armed_until")
    armed_until = parse_iso(row[0]) if row else None
    return armed_until is not None and datetime.now(timezone.utc) < armed_until


async def record_unavailable() -> bool:
    """Called every time the guardrail exhausts every real path. Returns
    True only on the call that CROSSES the arming threshold from a
    not-currently-armed state (fresh armament -- caller logs a loud
    WARNING only in that case), False otherwise (already armed, or not yet
    at threshold)."""
    now = datetime.now(timezone.utc)

    def _apply(row):
        prev_failures, prev_armed_until_raw, prev_armed_at = row or (0, None, None)
        failures = prev_failures + 1
        was_armed = (parse_iso(prev_armed_until_raw) or datetime.min.replace(tzinfo=timezone.utc)) > now

        armed_until_value = prev_armed_until_raw
        armed_at_value = prev_armed_at
        just_armed = False
        if failures >= _ARM_AFTER_CONSECUTIVE_FAILURES:
            armed_until_value = (now + timedelta(seconds=_AUTO_ARM_DURATION_SECONDS)).isoformat()
            just_armed = not was_armed
            if just_armed:
                armed_at_value = now.isoformat()

        values = {
            "consecutive_failures": failures,
            "armed_until": armed_until_value,
            "armed_at": armed_at_value,
        }
        return values, (just_armed, armed_until_value)

    just_armed, armed_until_value = await _store().mutate(
        ("consecutive_failures", "armed_until", "armed_at"), _apply
    )
    if just_armed:
        await _notify_armed(armed_until_value)
    return just_armed


async def _notify_armed(armed_until_iso: str | None) -> None:
    """10/08 -- one-time Telegram notice exactly when the bypass first arms
    (never repeated while already armed -- caller only invokes this on
    ``just_armed``). Distinct from the per-refusal ``ACHAT REFUSÉ`` alert
    (paper_trader.py): those stop firing once armed, this is the one signal
    that tells the operator WHY without them having to check logs."""
    from aria_core.gateway.telegram_bot import send_message

    until_text = parse_iso(armed_until_iso)
    until_str = until_text.strftime("%H:%M UTC") if until_text else "?"
    await send_message(
        "🛡️ Bypass automatique activé -- concentration des détenteurs invérifiable "
        f"depuis {_ARM_AFTER_CONSECUTIVE_FAILURES} tentatives consécutives (Blockscout probablement en panne). "
        f"Les candidats passent sans ce contrôle jusqu'à {until_str} (prolongé automatiquement si la panne continue, "
        "désarmé immédiatement dès qu'un vrai contrôle réussit). Aucune action requise."
    )


async def record_available() -> None:
    """Called the instant a REAL verdict succeeds (any path). Resets the
    failure streak AND disarms the auto-bypass immediately -- never waits
    for the fixed window to expire once Blockscout has demonstrably
    recovered. Never touches the independent manual env-var override."""
    await _store().write({"consecutive_failures": 0, "armed_until": None})
