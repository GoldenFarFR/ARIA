"""X request cap for the momentum pipeline's conviction diligence (19/07).

Re-enables X reading (cut off on 11/07 to control pay-per-use cost, cf.
CLAUDE.md) but BOUNDED -- same doctrine as ``x402_budget.py``: hard cap, never
exceeded, rolling calendar week (Monday 00:00 UTC), append-only.

Deliberate difference from x402_budget.py: this one counts REQUESTS, not
dollars. The exact cost per X read call depends on the operator's real
subscription tier (``x_publication_policy.py`` already documents a $5/month
subscription for PUBLICATION, a separate line item) -- never verified for
READING in this session, hence never invented here. ``WEEKLY_REQUEST_CAP`` is
a conservative cap, cautious by design; to adjust once the real reading tier
is known, not before.

Only counts X calls (``search_recent_tweets``/``fetch_user_recent_tweets``) --
never Tavily calls (already a separate provider/budget, unrelated to the
11/07 X cutoff)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import aiosqlite

from aria_core.paths import aria_db_path

DB_PATH = str(aria_db_path())

WEEKLY_REQUEST_CAP = 100

_COLUMNS = ["id", "purpose", "contract", "status", "reason", "created_at"]


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS x_research_request_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                purpose TEXT NOT NULL,
                contract TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            )
            """
        )
        await db.commit()


def week_start(now: datetime | None = None) -> datetime:
    """Start of the current calendar week (Monday 00:00 UTC) -- same formula
    as x402_budget.week_start, never duplicated by importing, deliberately
    rewritten here since the two modules remain structurally separate
    (different scopes)."""
    ref = now if now is not None else datetime.now(timezone.utc)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    monday = ref - timedelta(days=ref.weekday())
    return monday.replace(hour=0, minute=0, second=0, microsecond=0)


async def used_this_week(now: datetime | None = None) -> int:
    """Counts requests ACTUALLY made (status='ok') since the start of the
    calendar week. 'blocked' attempts never count against the cap."""
    await _ensure_table()
    start = week_start(now).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (
            await db.execute(
                "SELECT COUNT(*) FROM x_research_request_log WHERE status = 'ok' AND created_at >= ?",
                (start,),
            )
        ).fetchone()
    return int(row[0]) if row else 0


async def remaining_budget(now: datetime | None = None) -> int:
    used = await used_this_week(now)
    return max(0, WEEKLY_REQUEST_CAP - used)


async def can_spend(now: datetime | None = None) -> bool:
    """Fail-closed: when in doubt, refuse rather than risk exceeding the cap."""
    remaining = await remaining_budget(now)
    return remaining > 0


async def record_request(*, purpose: str, contract: str = "", status: str, reason: str = "") -> None:
    """Logs an X request attempt (``status`` in {"ok", "blocked"}) -- never
    only the successes, a cap refusal must remain traced."""
    await _ensure_table()
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO x_research_request_log (purpose, contract, status, reason, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (purpose, contract, status, reason, now),
        )
        await db.commit()


async def weekly_status(now: datetime | None = None) -> dict:
    used = await used_this_week(now)
    return {
        "cap_requests": WEEKLY_REQUEST_CAP,
        "used_requests": used,
        "remaining_requests": max(0, WEEKLY_REQUEST_CAP - used),
        "week_started_at": week_start(now).isoformat(),
    }
