"""Blockscout Pro credit budget tracking (authenticated FREE tier) — 22/07.

Distinct from ``x402_budget.py``: that one caps real micropayments in
dollars ($5/week); the classic Blockscout Pro plan (the one that produced
the "Out of credits" on 22/07) is FREE — $0, no credit card, but capped in
CREDITS/DAY by the provider. Two different units, two different tracking
mechanisms, never to be confused.

Plan sourced (verified via the official Blockscout doc, 22/07): authenticated
free tier = 100,000 credits/day, 5 req/s. "90% of real capacity" doctrine
already in place elsewhere (CLAUDE.md, 21/07): hard cap set at 90,000.

22/07 (continued) -- REAL cost per endpoint corrected after directly reading
the Blockscout dashboard (operator screenshot): the generic doc ("most
standard endpoints cost 20 credits") was incomplete -- ``token-transfers``
(on ``/transactions/:hash/`` AND ``/addresses/:address_hash/``) actually
costs **30 credits/call** (357810/11927 and 203460/6782 on the real
statement), not 20. The other endpoints used by ``blockscout.py``
(holders/tokens/transactions) are indeed at 20, confirmed by the same
statement. Same lesson already learned with GoPlus/Tavily: a generic
official doc can remain incomplete on a specific case, a real dashboard
statement takes precedence. Renewal window observed on this same statement:
~12h rolling since depletion, NOT aligned on UTC midnight -- ``day_start()``
remains a reasonable approximation (simple calendar window, never verified
down to the hour on the provider's side), documented as such rather than
presented as exact.

IMPORTANT DISCOVERY (22/07, same screenshot): the two ``token-transfers``
endpoints alone account for 73.6% of the month's total consumption
(561,270 / 762,850 credits) -- and they are NOT called by the momentum
pipeline (which only uses holders/tokens/smart-contracts for the
concentration check). They belong to wallet-scoring (a wallet's transfer
history, `smart_money.py`/`get_token_transfers`) -- the real source of
pressure on this budget, not the momentum discovery this budget was meant
to protect in the first place.

Same pattern as ``x402_budget.py``: CALENDAR window (UTC midnight, not a
rolling all-time cumulative sum), append-only (no UPDATE/DELETE function),
fail-closed (in case of doubt about the balance already consumed, refuse
rather than risk overshooting).

Expected usage (``blockscout.py``): PROACTIVE, not reactive -- check
``can_spend()`` BEFORE attempting a Pro call, not only after already
receiving a 402 (the reactive fallback on 402 already exists and remains
the final safety net if this budget turns out to be miscalibrated).
"""
from __future__ import annotations

from datetime import datetime, timezone

from aria_core.services import resource_budget

_RESOURCE_BUDGET_PROVIDER = "blockscout"

# 13/08 (#302) -- delegates to resource_budget.py, the unified ledger that
# replaced this module's own blockscout_credit_log table + local counting
# logic (the 27/07 DB_PATH-at-import fix that used to live here is now
# resource_budget.py's problem to solve once, not one of six). Migration is
# lazy and idempotent (resource_budget.py copies any pre-existing
# blockscout_credit_log rows in on first use, including the ``endpoint``
# label, never resets a mid-day counter to zero). Function
# names/signatures below kept unchanged -- ``blockscout.py`` (the actual
# API client, a separate module) was never touched.

# Sourced (22/07): blog.blockscout.com, authenticated free tier = 100,000
# credits/day. 90% margin, same doctrine as the other clients calibrated
# on 21/07 (docs/api-rate-limit-calibration.md).
DAILY_CAP_CREDITS = 90_000

# Default rate (holders/tokens/transactions -- confirmed 20 credits by the
# real dashboard statement, 22/07).
DEFAULT_COST_PER_CALL = 20

# 22/07 -- REAL cost per endpoint, read directly from the Blockscout
# dashboard (not the generic doc, incomplete on this point): token-transfers
# costs 30, not 20. Key = substring present in the called path
# (``path.endswith``), never an exact match -- real endpoints contain the
# variable address/hash (e.g. ``/addresses/0xabc.../token-transfers``).
_ENDPOINT_COST_SUFFIXES: dict[str, int] = {
    "/token-transfers": 30,
}


def cost_for_endpoint(path: str) -> int:
    """Real cost in credits for THIS specific endpoint -- ``DEFAULT_COST_PER_CALL``
    (20) if not listed in ``_ENDPOINT_COST_SUFFIXES``."""
    for suffix, cost in _ENDPOINT_COST_SUFFIXES.items():
        if path.endswith(suffix):
            return cost
    return DEFAULT_COST_PER_CALL


def day_start(now: datetime | None = None) -> datetime:
    """Start of the current calendar day (00:00 UTC)."""
    ref = now if now is not None else datetime.now(timezone.utc)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    return ref.replace(hour=0, minute=0, second=0, microsecond=0)


async def spent_today(now: datetime | None = None) -> int:
    """Sum of credits actually consumed (SUCCESSFUL Pro calls only -- a
    refused/failed call never debits credits on the provider's side) since
    UTC midnight."""
    return await resource_budget.spent_in_window(_RESOURCE_BUDGET_PROVIDER, window="daily", now=now)


async def remaining_budget(now: datetime | None = None) -> int:
    spent = await spent_today(now)
    return max(0, DAILY_CAP_CREDITS - spent)


async def can_spend(credits: int = DEFAULT_COST_PER_CALL, now: datetime | None = None) -> bool:
    """Fail-closed: a non-positive amount is always refused, and if the
    remaining balance doesn't cover the requested amount, we refuse rather
    than cutting it as close to the cap as possible (leaves margin for a
    concurrent call already in flight at check time)."""
    return await resource_budget.can_spend(
        _RESOURCE_BUDGET_PROVIDER, credits, cap=DAILY_CAP_CREDITS, window="daily", now=now
    )


async def record_spend(*, endpoint: str = "", credits: int = DEFAULT_COST_PER_CALL) -> None:
    """Only record Pro calls that actually succeeded (200 OK) -- a call
    that fails (402/429/5xx/timeout) never consumed a real credit on
    Blockscout's side, recording it would fabricate data."""
    await resource_budget.record_spend(_RESOURCE_BUDGET_PROVIDER, credits, caller=endpoint)


async def daily_status(now: datetime | None = None) -> dict:
    """Readable diagnostic, same doctrine as ``x402_budget.weekly_status``."""
    spent = await spent_today(now)
    return {
        "cap_credits": DAILY_CAP_CREDITS,
        "spent_credits": spent,
        "remaining_credits": max(0, DAILY_CAP_CREDITS - spent),
        "day_started_at": day_start(now).isoformat(),
    }
