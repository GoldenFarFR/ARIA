"""Firecrawl credit budget tracking -- 09/08, built alongside services/firecrawl.py
as the crawl provider REPLACING Tavily in website_substance.py (explicit operator
directive: "construis le remplacement Tavily par Firecrawl" -- Tavily's required
volume for 100%-in-7-days web-column coverage, ~13,000 crawls/week, sits ~2.8x
beyond its highest priced tier; Firecrawl's Standard plan covers it in one tier).

Same family as tavily_budget.py but a SIMPLER cost model: Firecrawl bills a flat
1 credit per page for markdown-only scrapeOptions (sourced live, docs.firecrawl.dev
+ firecrawl.dev/pricing, 09/08) -- no variable per-operation formula like Tavily's
mapping+extraction split. MONTHLY window, "use it or lose it" assumed (standard
SaaS subscription behavior -- to confirm against the real invoice once the
operator has purchased a plan).

MONTHLY_CAP_CREDITS below assumes the Standard plan (83$/month, 100,000
credits/month) -- the tier the cost comparison in docs/HANDOFF_SIGNAL_CASCADE.md
was built against. RECALIBRATE THIS CONSTANT the day the operator confirms which
tier was actually purchased (a different tier changes the real quota).

SHARED across every Firecrawl caller (today: website_substance.py only, once
wired) -- a single throughput/spend coordination point, never independent
counters silently adding up (21/07 doctrine, same as tavily_budget.py / the
shared GeckoTerminal throttle)."""
from __future__ import annotations

from datetime import datetime, timezone

import aiosqlite

from aria_core.paths import aria_db_path

# Sourced (09/08, docs.firecrawl.dev/pricing, live WebFetch): Standard plan =
# 83$/month, 100,000 credits/month included. 90% margin, CLAUDE.md doctrine.
# TO RECALIBRATE once the operator confirms the real purchased tier.
MONTHLY_CAP_CREDITS = 90_000

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


async def _ensure_table() -> None:
    async with aiosqlite.connect(str(aria_db_path())) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS firecrawl_crawl_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                caller TEXT NOT NULL DEFAULT '',
                query TEXT NOT NULL DEFAULT '',
                credits INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        await db.commit()


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
    await _ensure_table()
    start = month_start(now).isoformat()
    async with aiosqlite.connect(str(aria_db_path())) as db:
        row = await (
            await db.execute(
                "SELECT COALESCE(SUM(credits), 0) FROM firecrawl_crawl_log WHERE created_at >= ?",
                (start,),
            )
        ).fetchone()
    return int(row[0]) if row else 0


async def remaining_budget(now: datetime | None = None) -> int:
    spent = await spent_this_month(now)
    return max(0, MONTHLY_CAP_CREDITS - spent)


async def can_spend(credits: int, now: datetime | None = None) -> bool:
    """Fail-closed: a non-positive amount is always refused; if the remaining
    balance doesn't cover the requested amount, refuse rather than get as
    close as possible to the cap."""
    if credits <= 0:
        return False
    remaining = await remaining_budget(now)
    return credits <= remaining


async def record_spend(*, caller: str = "", query: str = "", credits: int = COST_PER_PAGE) -> None:
    """Only record ACTUALLY successful crawls. ``query`` truncated (ARIA's own
    operational data, never user PII) -- serves traceability, not just budget
    computation (same double purpose as tavily_search_log)."""
    await _ensure_table()
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(str(aria_db_path())) as db:
        await db.execute(
            "INSERT INTO firecrawl_crawl_log (caller, query, credits, created_at) VALUES (?, ?, ?, ?)",
            (caller[:60], query[:300], credits, now),
        )
        await db.commit()


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
    await _ensure_table()
    async with aiosqlite.connect(str(aria_db_path())) as db:
        cursor = await db.execute(
            "SELECT caller, query, credits, created_at FROM firecrawl_crawl_log "
            "ORDER BY id DESC LIMIT ?",
            (max(1, min(limit, 200)),),
        )
        rows = await cursor.fetchall()
    return [
        {"caller": row[0], "query": row[1], "credits": row[2], "created_at": row[3]}
        for row in rows
    ]
