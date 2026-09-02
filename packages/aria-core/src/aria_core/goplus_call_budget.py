"""Real GoPlus consumption meter (operator P0 #3, 02/09: "conserver la mesure
des appels GoPlus pour enfin connaitre notre consommation reelle").

Why this exists: nothing in the dome ever counted a GoPlus call. The client's
own comment states the situation plainly -- the only verified limit is the
150 CU/min rate on the dashboard, and *"no monthly/daily GoPlus cap has ever
been confirmed -- no number invented here"*. So the sole protection today is
REACTIVE (a circuit breaker after 5 consecutive failures, plus an automatic
quota suspension): the system discovers it has run out only once GoPlus starts
refusing. That is a blind spot, not a design -- a budget nobody counts cannot
be planned against, and the watchlist's own sizing math (2000 slots, ~288s per
token) was derived from a monthly figure the code itself flags as unverified.

Deliberately a METER, never a gate. It records what actually happened and
answers "how much do we really consume"; it never blocks a call, never
suspends anything, and never invents a ceiling. Turning a measured number into
a real cap is a separate, later decision that belongs to the operator -- and it
needs this data to be made honestly in the first place.

One row per UTC day per outcome, so a day's real cost is one cheap query and
the failure share is visible next to it (a day at 3000 calls with 40% errors
is a very different fact from 3000 clean calls). Best-effort throughout: a
meter that broke the call it measures would be worse than no meter.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import aiosqlite

from aria_core.paths import aria_db_path

logger = logging.getLogger(__name__)

TABLE = "goplus_call_log"

_DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    day TEXT NOT NULL,
    outcome TEXT NOT NULL,
    calls INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, outcome)
)
"""


async def _ensure_table(db) -> None:
    await db.execute(_DDL)


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


async def record_call(outcome: str) -> None:
    """Counts one real network call to GoPlus. ``outcome`` is coarse on
    purpose -- 'ok', 'rate_limited', 'error' -- because the useful question is
    "how much did we spend and how much of it was wasted", not a per-endpoint
    breakdown nobody would act on. Never raises: measurement must never be
    able to break the path it measures."""
    try:
        async with aiosqlite.connect(str(aria_db_path())) as db:
            await _ensure_table(db)
            await db.execute(
                f"INSERT INTO {TABLE} (day, outcome, calls) VALUES (?, ?, 1) "
                "ON CONFLICT(day, outcome) DO UPDATE SET calls = calls + 1",
                (_today(), (outcome or "unknown").strip().lower()),
            )
            await db.commit()
    except Exception as exc:  # noqa: BLE001 -- a meter never breaks its subject
        logger.debug("goplus_call_budget: record failed (%s)", exc)


async def usage(days: int = 7) -> list[dict]:
    """Real consumption, most recent day first: one entry per day with the
    per-outcome split and the total actually spent."""
    cutoff = (datetime.now(timezone.utc).date() - timedelta(days=max(0, days - 1))).isoformat()
    try:
        async with aiosqlite.connect(str(aria_db_path())) as db:
            await _ensure_table(db)
            db.row_factory = aiosqlite.Row
            rows = await (
                await db.execute(
                    f"SELECT day, outcome, calls FROM {TABLE} WHERE day >= ? ORDER BY day DESC",
                    (cutoff,),
                )
            ).fetchall()
    except Exception as exc:  # noqa: BLE001
        logger.debug("goplus_call_budget: usage read failed (%s)", exc)
        return []

    by_day: dict[str, dict] = {}
    for r in rows:
        entry = by_day.setdefault(r["day"], {"day": r["day"], "total": 0})
        entry[r["outcome"]] = r["calls"]
        entry["total"] += r["calls"]
    return list(by_day.values())


async def today_total() -> int:
    """Calls made so far today -- the single number to compare against any
    future ceiling, once a real one is ever confirmed."""
    for entry in await usage(days=1):
        if entry["day"] == _today():
            return int(entry["total"])
    return 0
