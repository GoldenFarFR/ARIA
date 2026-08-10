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
failures stop."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import aiosqlite

from aria_core.paths import aria_db_path

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

_ROW_ID = 1


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS holder_concentration_outage_bypass_state (
                id INTEGER PRIMARY KEY,
                consecutive_failures INTEGER NOT NULL DEFAULT 0,
                armed_until TEXT,
                armed_at TEXT,
                last_updated_at TEXT NOT NULL
            )
            """
        )
        await db.execute(
            "INSERT OR IGNORE INTO holder_concentration_outage_bypass_state "
            "(id, consecutive_failures, armed_until, armed_at, last_updated_at) "
            "VALUES (?, 0, NULL, NULL, ?)",
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


async def is_armed() -> bool:
    """Checked FIRST, before recording anything -- a fresh-armament WARNING
    is only ever logged once (see record_unavailable's return value), never
    on every single check while already armed."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (
            await db.execute(
                "SELECT armed_until FROM holder_concentration_outage_bypass_state WHERE id = ?",
                (_ROW_ID,),
            )
        ).fetchone()
    armed_until = _parse(row[0]) if row else None
    return armed_until is not None and datetime.now(timezone.utc) < armed_until


async def record_unavailable() -> bool:
    """Called every time the guardrail exhausts every real path. Returns
    True only on the call that CROSSES the arming threshold from a
    not-currently-armed state (fresh armament -- caller logs a loud
    WARNING only in that case), False otherwise (already armed, or not yet
    at threshold)."""
    await _ensure_table()
    now = datetime.now(timezone.utc)
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (
            await db.execute(
                "SELECT consecutive_failures, armed_until, armed_at FROM "
                "holder_concentration_outage_bypass_state WHERE id = ?",
                (_ROW_ID,),
            )
        ).fetchone()
        prev_failures, prev_armed_until_raw, prev_armed_at = row or (0, None, None)
        failures = prev_failures + 1
        was_armed = (_parse(prev_armed_until_raw) or datetime.min.replace(tzinfo=timezone.utc)) > now

        armed_until_value = prev_armed_until_raw
        armed_at_value = prev_armed_at
        just_armed = False
        if failures >= _ARM_AFTER_CONSECUTIVE_FAILURES:
            armed_until_value = (now + timedelta(seconds=_AUTO_ARM_DURATION_SECONDS)).isoformat()
            just_armed = not was_armed
            if just_armed:
                armed_at_value = now.isoformat()

        await db.execute(
            "UPDATE holder_concentration_outage_bypass_state "
            "SET consecutive_failures = ?, armed_until = ?, armed_at = ?, last_updated_at = ? "
            "WHERE id = ?",
            (failures, armed_until_value, armed_at_value, now.isoformat(), _ROW_ID),
        )
        await db.commit()
    return just_armed


async def record_available() -> None:
    """Called the instant a REAL verdict succeeds (any path). Resets the
    failure streak AND disarms the auto-bypass immediately -- never waits
    for the fixed window to expire once Blockscout has demonstrably
    recovered. Never touches the independent manual env-var override."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE holder_concentration_outage_bypass_state "
            "SET consecutive_failures = 0, armed_until = NULL, last_updated_at = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), _ROW_ID),
        )
        await db.commit()
