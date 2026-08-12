"""Auto-armed Firecrawl overspend suspension -- 12/08, built after a real
incident (signal-cascade triage, wJUNO diligence): a single crawl
(seamlessprotocol.com, redirecting server-side to its own X profile) cost
156 credits -- 39% of the monthly free-plan budget in one call, ~10x the
worst-case ``firecrawl_budget.estimate_crawl_worst_case`` had budgeted for
(the estimate assumes 1 credit/page, but Firecrawl's dedicated X engine
bills ~30 credits/page, and stealth-mode-protected sites can bill 5x --
neither is knowable BEFORE the call, only from the real ``creditsUsed``
the job returns).

Unlike ``goplus_quota_suspension.py`` (armed on repeated rate-limit
SIGNALS, disarmed the instant a call succeeds again), this is armed by a
single crawl's real COST exceeding a sane ceiling -- the failure mode
here isn't "the provider is down", it's "one page was unexpectedly
expensive", so probing again soon serves no purpose. Suspension lasts
until the CURRENT budget month rolls over (matches
``firecrawl_budget.month_start`` semantics: protect what's left of this
month's allowance, auto-resume with the next month's fresh budget) rather
than an exponential backoff.

``_SINGLE_CRAWL_CREDIT_CEILING`` calibrated on the real observed
distribution in ``firecrawl_crawl_log`` (12/08): every legitimate crawl to
date topped out at 28 credits (ultraroundmoney.com/circle); the 156-credit
event is the only outlier. 30 sits just above the real legitimate max,
nowhere near the outlier -- operator-confirmed as still too high a margin
at 50 ("meme 50 sa fait trop"), tightened to 30.

SQL plumbing shared via ``single_row_state.SingleRowStore`` (same pattern
as ``goplus_quota_suspension.py``/``holder_concentration_outage_bypass.py``)."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from aria_core.paths import aria_db_path
from aria_core.single_row_state import SingleRowStore, parse_iso

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())

# 12/08 -- see module docstring: calibrated just above the real legitimate
# max observed (28 credits), operator-tightened from an initial 50.
_SINGLE_CRAWL_CREDIT_CEILING = 30

_TABLE = "firecrawl_overspend_suspension_state"
_COLUMNS = [
    ("suspended_until", "TEXT", None),
    ("armed_reason", "TEXT", None),
]


def _store() -> SingleRowStore:
    # Constructed fresh on every call (cheap) so a test monkeypatching the
    # module-level DB_PATH after import is always honored, never frozen at
    # import time -- same doctrine as goplus_quota_suspension._store.
    return SingleRowStore(DB_PATH, _TABLE, _COLUMNS)


def _next_month_start(now: datetime) -> datetime:
    if now.month == 12:
        return now.replace(year=now.year + 1, month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    return now.replace(month=now.month + 1, day=1, hour=0, minute=0, second=0, microsecond=0)


async def is_suspended() -> bool:
    """Checked BEFORE starting a crawl, alongside (never instead of) the
    normal ``firecrawl_budget.can_spend`` monthly-cap check."""
    row = await _store().read("suspended_until")
    until = parse_iso(row[0]) if row else None
    return until is not None and datetime.now(timezone.utc) < until


async def record_crawl_cost(*, credits: int, url: str, caller: str = "") -> bool:
    """Called AFTER a crawl completes with the real ``creditsUsed`` value.
    A single call exceeding the ceiling is the whole signal -- no
    consecutive-failure counter needed (this isn't a flaky-provider
    pattern, it's "one page was unexpectedly expensive"). Returns True
    only on the call that newly arms the suspension (caller doesn't need
    to act on the return value; kept for testability, same shape as
    ``goplus_quota_suspension.record_rate_limit_failure``)."""
    if credits <= _SINGLE_CRAWL_CREDIT_CEILING:
        return False

    now = datetime.now(timezone.utc)
    until = _next_month_start(now)
    reason = f"{credits} credits on a single crawl ({url[:200]}), ceiling={_SINGLE_CRAWL_CREDIT_CEILING}"

    def _apply(row):
        prev_until = parse_iso(row[0]) if row else None
        already_suspended = prev_until is not None and now < prev_until
        if already_suspended:
            return {}, False
        return {"suspended_until": until.isoformat(), "armed_reason": reason}, True

    just_armed = await _store().mutate(("suspended_until",), _apply)
    if just_armed:
        logger.warning("firecrawl_overspend_suspension: ARMED (%s) -- suspended until %s", reason, until.isoformat())
        await _notify_armed(reason, until)
    return just_armed


async def _notify_armed(reason: str, until: datetime) -> None:
    from aria_core.gateway.telegram_bot import send_message

    await send_message(
        "🛡️ Suspension automatique Firecrawl activée -- un crawl a coûté "
        f"anormalement cher ({reason}). Repli scraper maison/Tavily jusqu'au "
        f"{until.strftime('%Y-%m-%d')} (renouvellement mensuel du budget). Aucune action requise."
    )
