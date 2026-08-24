"""Chainstack request-unit budget, per chain, rolling calendar day (24/08).

Same doctrine as ``x_research_budget.py``/``x402_budget.py``: hard cap, never
exceeded, append-only log, fail-closed. Deliberate difference: this one is
keyed per CHAIN (solana/base/robinhood), not a single global counter -- three
independent chains share one Chainstack account/quota, and a runaway chain
must never be able to starve the others' share by silently eating the whole
monthly pool before they get a turn.

**Why 200k/day/chain** (operator-set, 24/08, after the Growth plan upgrade):
3 chains x 200k/day = 600k/day =~ 18M/month, under Growth's 20M/month total
with real headroom for spikes (verified live: Solana alone hit 575,978 in a
single day on 23/08, well over 200k -- this cap is a deliberate REDUCTION
from that, not a description of current behaviour). Revisit the number if a
4th chain is added or the plan tier changes again; the mechanism does not
depend on the exact figure.

**Fail-closed, degrade gracefully, never crash a caller**: ``can_spend(chain)``
answers False once a chain's daily cap is spent -- callers (pumpfun_curve_
tracker's poll_due(), any future EVM subscription re-arm) skip only the
PAID call for the rest of the day, same shape as outgoing_pause's kill-switch
check on 24/08 -- discovery/pruning/state-save keep running unconditionally,
only the metered RPC call is gated, so a paused/exhausted chain never loses
track of what it already knows.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import aiosqlite

from aria_core.paths import aria_db_path

# Operator-set 24/08, see module docstring for the arithmetic. Same cap for
# every chain today -- a dict override is deliberately NOT added until a real
# chain needs a different number (never guess a per-chain split ahead of data).
DAILY_UNIT_CAP_PER_CHAIN = 200_000


async def _ensure_table() -> None:
    async with aiosqlite.connect(str(aria_db_path())) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS chainstack_ru_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chain TEXT NOT NULL,
                units INTEGER NOT NULL,
                purpose TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            )
            """
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_chainstack_ru_log_chain_created "
            "ON chainstack_ru_log (chain, created_at)"
        )
        await db.commit()


def day_start(now: datetime | None = None) -> datetime:
    """Start of the current calendar day (00:00 UTC) -- Chainstack's own
    quota resets are not documented to the minute, UTC midnight is the
    conservative, verifiable choice (same reasoning as week_start's Monday
    00:00 UTC in the sibling budget modules)."""
    ref = now if now is not None else datetime.now(timezone.utc)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    return ref.replace(hour=0, minute=0, second=0, microsecond=0)


async def used_today(chain: str, now: datetime | None = None) -> int:
    await _ensure_table()
    start = day_start(now).isoformat()
    async with aiosqlite.connect(str(aria_db_path())) as db:
        row = await (
            await db.execute(
                "SELECT COALESCE(SUM(units), 0) FROM chainstack_ru_log "
                "WHERE chain = ? AND created_at >= ?",
                (chain, start),
            )
        ).fetchone()
    return int(row[0]) if row else 0


async def remaining_today(chain: str, now: datetime | None = None) -> int:
    used = await used_today(chain, now)
    return max(0, DAILY_UNIT_CAP_PER_CHAIN - used)


async def can_spend(chain: str, now: datetime | None = None) -> bool:
    """Fail-closed: when in doubt, refuse rather than risk exceeding the cap."""
    return await remaining_today(chain, now) > 0


async def record_usage(chain: str, units: int, *, purpose: str = "") -> None:
    """Logs units ACTUALLY spent -- callers record only real, billed RPC
    calls, never a speculative/rejected attempt (unlike x_research_budget's
    'ok'/'blocked' split, there is no "blocked but still logged" case here:
    a call skipped for being over budget was never sent, so it never cost
    anything, so there is nothing to log)."""
    await _ensure_table()
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(str(aria_db_path())) as db:
        await db.execute(
            "INSERT INTO chainstack_ru_log (chain, units, purpose, created_at) VALUES (?, ?, ?, ?)",
            (chain, units, purpose, now),
        )
        await db.commit()


async def daily_status(chain: str, now: datetime | None = None) -> dict:
    used = await used_today(chain, now)
    return {
        "chain": chain,
        "cap_units": DAILY_UNIT_CAP_PER_CHAIN,
        "used_units": used,
        "remaining_units": max(0, DAILY_UNIT_CAP_PER_CHAIN - used),
        "day_started_at": day_start(now).isoformat(),
    }
