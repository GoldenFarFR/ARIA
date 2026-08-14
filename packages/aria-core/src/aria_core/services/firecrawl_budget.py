"""Firecrawl credit budget tracking -- 09/08, built alongside services/firecrawl.py
as a candidate crawl provider for website_substance.py (explicit operator
directive: "construis le remplacement Tavily par Firecrawl"). Real production
data then showed the required paid tier would cost far more than first
estimated -- the operator refused it ("je vais pas payer 300 balle") and this
client is now used on the FREE plan only, as a light supplement alongside a
free homemade crawler being explored separately, never as the primary
high-volume provider it was first designed for.

Same family as tavily_budget.py but a SIMPLER cost model: Firecrawl bills a flat
1 credit per page for markdown-only scrapeOptions (sourced live, docs.firecrawl.dev
+ firecrawl.dev/pricing, 09/08) -- no variable per-operation formula like Tavily's
mapping+extraction split. MONTHLY window, "use it or lose it" assumed (standard
SaaS subscription behavior -- to confirm against the real invoice once the
operator has purchased a plan).

MONTHLY_CAP_CREDITS below is set for the FREE plan (0$/month, 1,000
credits/month) -- the operator explicitly refused the Standard/Growth paid
tiers ("je vais pas payer 300 balle") when their real cost was verified
09/08 (see docs/HANDOFF_SIGNAL_CASCADE.md). Kept deliberately conservative
(fail-safe) until the operator confirms which plan was actually created --
RAISE THIS CONSTANT only after that confirmation, never assume a paid tier.

SHARED across every Firecrawl caller (today: website_substance.py only, once
wired) -- a single throughput/spend coordination point, never independent
counters silently adding up (21/07 doctrine, same as tavily_budget.py / the
shared GeckoTerminal throttle)."""
from __future__ import annotations

from datetime import datetime, timezone

from aria_core.services import resource_budget

_RESOURCE_BUDGET_PROVIDER = "firecrawl"

# Sourced (09/08, docs.firecrawl.dev/pricing, live WebFetch): Free plan =
# 0$/month, 1,000 credits/month included. 90% margin, CLAUDE.md doctrine.
# TO RAISE only once the operator confirms a paid tier was purchased.
MONTHLY_CAP_CREDITS = 900

# Sourced (09/08, docs.firecrawl.dev/pricing, live WebFetch): "Scrape" and
# "Crawl" each cost 1 credit/page for markdown-only formats (our only usage --
# other formats like JSON extraction cost more, never used here).
COST_PER_PAGE = 1


def estimate_crawl_worst_case(page_limit: int) -> int:
    """WORST-case estimate (``page_limit`` pages actually returned) --
    Firecrawl never returns more pages than the requested limit, so this
    bounds the real cost without underestimating it. Checked BEFORE the
    network call (same doctrine as tavily_budget.estimate_crawl_worst_case)."""
    return COST_PER_PAGE * max(0, int(page_limit))


# 13/08 (#302) -- delegates to resource_budget.py, the unified ledger that
# replaced this module's own firecrawl_crawl_log table + local counting
# logic. Migration is lazy and idempotent (resource_budget.py copies any
# pre-existing firecrawl_crawl_log rows in on first use, including
# caller/query, never resets a mid-month counter to zero). Function
# names/signatures below kept unchanged -- ``firecrawl.py`` (the actual API
# client, a separate module) was never touched.


def month_start(now: datetime | None = None) -> datetime:
    """Start of the current calendar month (UTC) -- same doctrine as
    tavily_budget.month_start."""
    ref = now if now is not None else datetime.now(timezone.utc)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    return ref.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


async def spent_this_month(now: datetime | None = None) -> int:
    """Sum of credits actually consumed (SUCCESSFUL crawls only) since the
    start of the current calendar month."""
    return await resource_budget.spent_in_window(_RESOURCE_BUDGET_PROVIDER, now=now)


async def remaining_budget(now: datetime | None = None) -> int:
    spent = await spent_this_month(now)
    return max(0, MONTHLY_CAP_CREDITS - spent)


async def can_spend(credits: int, now: datetime | None = None) -> bool:
    """Fail-closed: a non-positive amount is always refused; if the remaining
    balance doesn't cover the requested amount, refuse rather than get as
    close as possible to the cap."""
    return await resource_budget.can_spend(_RESOURCE_BUDGET_PROVIDER, credits, cap=MONTHLY_CAP_CREDITS, now=now)


async def record_spend(*, caller: str = "", query: str = "", credits: int = COST_PER_PAGE) -> None:
    """Only record ACTUALLY successful crawls. ``query`` truncated (ARIA's own
    operational data, never user PII) -- serves traceability, not just budget
    computation (same double purpose as tavily_search_log)."""
    await resource_budget.record_spend(
        _RESOURCE_BUDGET_PROVIDER, credits, caller=caller[:60], query=query[:300]
    )


async def monthly_status(now: datetime | None = None) -> dict:
    """Human-readable diagnostic, same doctrine as tavily_budget.monthly_status."""
    spent = await spent_this_month(now)
    return {
        "cap_credits": MONTHLY_CAP_CREDITS,
        "spent_credits": spent,
        "remaining_credits": max(0, MONTHLY_CAP_CREDITS - spent),
        "month_started_at": month_start(now).isoformat(),
    }


async def recent_crawls(limit: int = 20) -> list[dict]:
    """Traceability: the most recent crawls actually executed (root URL
    truncated, caller, cost, timestamp)."""
    rows = await resource_budget.recent_spends(_RESOURCE_BUDGET_PROVIDER, limit)
    return [
        {"caller": row["caller"], "query": row["query"], "credits": row["cost"], "created_at": row["recorded_at"]}
        for row in rows
    ]
