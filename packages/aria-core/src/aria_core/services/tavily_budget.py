"""Tavily credit budget tracking (free "Researcher" tier) — 22/07.

Same family as ``blockscout_credit_budget.py`` but a MONTHLY window, not a
daily one -- the real structure of the Tavily plan (verified on the real
billing dashboard, 22/07, cf. ``docs/api-rate-limit-calibration.md``): **1000
credits/month, "use it or lose it"** (no rollover to the next month), 1 credit
per "basic" search, 2 per "advanced" search. No req/min rate limit
documented anywhere for this provider -- this budget protects against
EXHAUSTING the monthly plan, not against a 429 (cf. the "Two constraint
families" section of the calibration doc).

"90% of real capacity" doctrine (CLAUDE.md, 21/07): hard cap set to
900 (90% of 1000).

SHARED across ALL Tavily callers (``web_verify.fetch_web_snippets``
for operator/visitor factual questions, and the future self-training cycle
``tavily_learning.py``) -- a single throughput coordination point,
never two independent counters silently adding up
(same doctrine as the shared GeckoTerminal throttle, 21/07 incident).
Wired directly into ``services/tavily.py::TavilyClient.search()``, never
in each individual caller.

The ``tavily_search_log`` log serves a DOUBLE purpose: (1) computing the
consumed budget, (2) traceability -- the operator can see WHAT was searched
and BY WHOM (``caller``), not just how many credits were spent
(explicit operator request, 22/07: "I also need to be able to know
what ARIA is searching for on Tavily").
"""
from __future__ import annotations

from datetime import datetime, timezone

from aria_core.services import resource_budget

_RESOURCE_BUDGET_PROVIDER = "tavily"

# 13/08 (#302) -- delegates to resource_budget.py, the unified ledger that
# replaced this module's own tavily_search_log table + local counting logic
# (the 27/07 DB_PATH-at-import fix that used to live here is now
# resource_budget.py's problem to solve once, not one of six). Migration is
# lazy and idempotent (resource_budget.py copies any pre-existing
# tavily_search_log rows in on first use, including caller/query, never
# resets a mid-month counter to zero). Function names/signatures below kept
# unchanged -- ``tavily.py`` (the actual API client, a separate module) was
# never touched.

# Sourced (22/07, real Tavily billing dashboard, "Researcher" plan): 1000
# credits/month, use-it-or-lose-it. 90% margin, CLAUDE.md doctrine.
MONTHLY_CAP_CREDITS = 900

# Sourced (official Tavily doc): "basic" search = 1 credit, "advanced" = 2.
COST_BASIC = 1
COST_ADVANCED = 2


def cost_for_search(search_depth: str) -> int:
    """Real credit cost for this search depth."""
    return COST_ADVANCED if (search_depth or "").strip().lower() == "advanced" else COST_BASIC


# 23/07 -- extract()/crawl() added (X read routing to Tavily + full site
# extraction for future Website/Docs Substance signals). Sourced against
# the official Tavily doc (WebFetch, 23/07): extraction = 1 "basic" credit /
# 2 "advanced" credits PER BATCH OF 5 URLs (rounded up); crawl = same
# extraction cost PER PAGE ACTUALLY RETURNED + 1 "mapping" credit per
# batch of 10 pages returned.
def cost_for_extract(extract_depth: str, url_count: int) -> int:
    """Real credit cost for extracting ``url_count`` URLs."""
    per_batch = COST_ADVANCED if (extract_depth or "").strip().lower() == "advanced" else COST_BASIC
    batches = max(1, -(-max(0, int(url_count)) // 5))  # ceil(url_count / 5), never 0
    return per_batch * batches


def cost_for_crawl(extract_depth: str, page_count: int) -> int:
    """Real credit cost for a crawl that returned ``page_count`` pages
    (mapping + extraction) -- used to RECORD the actual spend after the
    fact (the number of pages actually returned is only known after
    the call). See ``estimate_crawl_worst_case`` for the budget check
    BEFORE the call (against the requested limit, never a not-yet-known
    result)."""
    mapping = max(1, -(-max(0, int(page_count)) // 10))  # ceil(page_count / 10)
    return mapping + cost_for_extract(extract_depth, page_count)


def estimate_crawl_worst_case(extract_depth: str, page_limit: int) -> int:
    """WORST-case estimate (``page_limit`` pages actually returned) --
    Tavily never returns more pages than the requested limit, so this
    figure bounds the real cost without ever underestimating it. Checked BEFORE
    the network call (same doctrine as ``can_spend`` for ``search``)."""
    return cost_for_crawl(extract_depth, page_limit)


def month_start(now: datetime | None = None) -> datetime:
    """Start of the current calendar month (UTC) -- the provider's "use it or
    lose it" window, never a rolling all-time cumulative total."""
    ref = now if now is not None else datetime.now(timezone.utc)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    return ref.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


async def spent_this_month(now: datetime | None = None) -> int:
    """Sum of credits actually consumed (SUCCESSFUL searches only
    -- a failure never debits a real credit on Tavily's side) since the start
    of the current calendar month."""
    return await resource_budget.spent_in_window(_RESOURCE_BUDGET_PROVIDER, now=now)


async def remaining_budget(now: datetime | None = None) -> int:
    spent = await spent_this_month(now)
    return max(0, MONTHLY_CAP_CREDITS - spent)


async def can_spend(credits: int = COST_BASIC, now: datetime | None = None) -> bool:
    """Fail-closed: a non-positive amount is always refused; if the remaining
    balance doesn't cover the requested amount, we refuse rather than get as
    close as possible to the cap."""
    return await resource_budget.can_spend(_RESOURCE_BUDGET_PROVIDER, credits, cap=MONTHLY_CAP_CREDITS, now=now)


async def record_spend(*, caller: str = "", query: str = "", credits: int = COST_BASIC) -> None:
    """Only record ACTUALLY successful searches. ``query`` is
    truncated (ARIA's own operational data, never user PII) -- serves
    traceability, not just budget computation."""
    await resource_budget.record_spend(
        _RESOURCE_BUDGET_PROVIDER, credits, caller=caller[:60], query=query[:300]
    )


async def monthly_status(now: datetime | None = None) -> dict:
    """Human-readable diagnostic, same doctrine as ``blockscout_credit_budget.daily_status``."""
    spent = await spent_this_month(now)
    return {
        "cap_credits": MONTHLY_CAP_CREDITS,
        "spent_credits": spent,
        "remaining_credits": max(0, MONTHLY_CAP_CREDITS - spent),
        "month_started_at": month_start(now).isoformat(),
    }


async def recent_searches(limit: int = 20) -> list[dict]:
    """Traceability: the most recent searches actually executed (query
    truncated, caller, cost, timestamp) -- answers "what is ARIA
    searching for on Tavily", not just the budget consumed."""
    rows = await resource_budget.recent_spends(_RESOURCE_BUDGET_PROVIDER, limit)
    return [
        {"caller": row["caller"], "query": row["query"], "credits": row["cost"], "created_at": row["recorded_at"]}
        for row in rows
    ]
