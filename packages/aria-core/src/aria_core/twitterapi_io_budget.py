"""Proactive TwitterAPI.io prepaid-credit runway monitor -- built 13/08
after a real incident: the account's prepaid credit silently ran to zero
for ~24h (root-caused the same day while investigating a Tavily-saturation
question), pushing ``x_substance.py``'s and ``conviction_research.py``'s
fallback traffic onto Tavily for that whole window with no alert anywhere
-- the client itself (``services/twitterapi_io.py``) only ever degrades to
``None`` on ANY failure, never distinguishing "no credits" from a generic
outage.

Unlike ``goplus_quota_suspension.py`` (REACTIVE -- arms only after a real
rate-limit signal), TwitterAPI.io exposes a real balance-check endpoint
(``fetch_credit_balance``), so this module can warn BEFORE exhaustion
instead of after. Estimates a projected runway from the delta between two
successive balance readings (this cycle's own cadence provides the time
axis) rather than alerting on a guessed absolute credit count -- a fixed
number would be meaningless without knowing the account's real burn rate,
which varies with how much sourcing activity is running.

No suspension/circuit-breaker here (unlike GoPlus/Firecrawl): TwitterAPI.io
already has a graceful fallback (Tavily) wired into every caller, so this
module's only job is VISIBILITY -- give the operator enough notice to
recharge before the fallback traffic accumulates real Tavily cost for a
whole day, as it did in the incident that motivated this.

SQL plumbing shared via ``single_row_state.SingleRowStore`` (same pattern
as ``goplus_quota_suspension.py``/``holder_concentration_outage_bypass.py``)."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from aria_core.paths import aria_db_path
from aria_core.services.twitterapi_io import fetch_credit_balance
from aria_core.single_row_state import SingleRowStore, parse_iso

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())

# 13/08 -- the real incident this module targets lasted ~24h before being
# noticed. Alerting once the projected runway drops under that same window
# gives at least a day's notice before the NEXT exhaustion, instead of
# discovering it only once traffic has already silently shifted to Tavily.
_LOW_RUNWAY_HOURS_THRESHOLD = 24.0

_TABLE = "twitterapi_io_budget_state"
_COLUMNS = [
    ("last_balance", "INTEGER", None),
    ("last_checked_at", "TEXT", None),
    ("alerted", "INTEGER NOT NULL DEFAULT 0", 0),
]


def _store() -> SingleRowStore:
    # Fresh instance per call (cheap) so a test monkeypatching the
    # module-level DB_PATH after import is always honored.
    return SingleRowStore(DB_PATH, _TABLE, _COLUMNS)


async def check_and_alert() -> dict:
    """Reads the real balance, projects a runway from the delta against the
    previous reading, and sends a ONE-TIME Telegram alert (hysteresis via
    the ``alerted`` flag -- never repeats while still low, re-arms only
    after the runway recovers, e.g. a recharge). Returns a small dict for
    the caller's own logging, never raises."""
    balance = await fetch_credit_balance()
    if balance is None:
        # A failed balance check is a DIFFERENT kind of problem (network/key
        # issue) already covered by the client's own dome doctrine -- never
        # alert on this path, never treat it as "zero credits".
        logger.info("twitterapi_io_budget: balance check failed, skipping this cycle")
        return {"checked": False}

    now = datetime.now(timezone.utc)

    def _apply(row):
        prev_balance, prev_checked_at_raw, prev_alerted = row or (None, None, 0)
        prev_checked_at = parse_iso(prev_checked_at_raw)

        runway_hours = None
        if prev_balance is not None and prev_checked_at is not None:
            elapsed_hours = (now - prev_checked_at).total_seconds() / 3600
            consumed = prev_balance - balance.recharge_credits
            if elapsed_hours > 0 and consumed > 0:
                burn_per_hour = consumed / elapsed_hours
                if burn_per_hour > 0:
                    runway_hours = balance.recharge_credits / burn_per_hour

        is_low = runway_hours is not None and runway_hours < _LOW_RUNWAY_HOURS_THRESHOLD
        should_alert = is_low and not prev_alerted

        values = {
            "last_balance": balance.recharge_credits,
            "last_checked_at": now.isoformat(),
            "alerted": 1 if is_low else 0,
        }
        return values, (should_alert, runway_hours)

    should_alert, runway_hours = await _store().mutate(
        ("last_balance", "last_checked_at", "alerted"), _apply
    )
    if should_alert:
        await _notify_low_runway(balance.recharge_credits, runway_hours)

    return {
        "checked": True,
        "balance": balance.recharge_credits,
        "runway_hours": runway_hours,
        "alerted": should_alert,
    }


async def _notify_low_runway(balance: int, runway_hours: float | None) -> None:
    from aria_core.gateway.telegram_bot import send_message

    runway_str = f"~{runway_hours:.0f}h" if runway_hours is not None else "?"
    await send_message(
        "⚠️ Crédit TwitterAPI.io bas -- solde restant "
        f"{balance:,} crédits, autonomie projetée {runway_str} au rythme actuel "
        "(recherche X/conviction va basculer sur le fallback Tavily payant une "
        "fois épuisé). Recharger sur le dashboard TwitterAPI.io."
    )
