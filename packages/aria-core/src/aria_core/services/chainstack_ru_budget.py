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

**Batched writes, cached reads -- required at the real call rate.** Solana
polling alone runs up to 45 req/s (``solana_rpc_budget.DEFAULT_RATE_PER_
SECOND``); a naive INSERT/SELECT per call would put up to 45 writes and 45
reads a second on the SAME SQLite file every other module in this process
also uses. ``record_usage_fast`` is synchronous and in-memory only -- it
never touches the DB, a caller in the hot polling loop calls it after every
real RPC call at zero I/O cost. A caller's own periodic report pass (already
existing in shadow_persistent.py for the curve tracker) calls
``flush_pending()`` every few seconds to persist the accumulated total in
ONE write per chain. ``can_spend``/``remaining_today`` read the DB at most
once every ``_READ_CACHE_SECONDS`` per chain (folding in whatever is still
pending, unflushed) rather than on every call -- precise enough for a daily
cap, cheap enough for the real call rate.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import aiosqlite

from aria_core.paths import aria_db_path

# Per-chain caps, operator-set 25/08 after real per-chain usage data made a
# single shared number wrong: Base barely spends anything (2 509 RU used by
# 20:33 UTC, ~2 940/day projected) while Robinhood's ~100ms block time makes
# it structurally far more expensive to watch (measured peak 36 213 RU/hour
# on 25/08, later found to be a now-fixed keepalive bug -- see evm_swap_ws.py's
# ``_check_idle_newheads``/``_check_budget_circuit_breaker`` docstrings for
# that incident). Same total as the old shared 200k x 3 (600k/day), just
# redistributed -- no extra cost against the Growth plan's shared pool.
#
# 29/08, operator-directed real incident: Base's 25k/day cap (calibrated on a
# NORMAL day with no restarts) was blown by 17:10:20Z the same day three
# on-chain-sensor briques were deployed in a few hours -- each restart forces
# a fresh _resubscribe() and a real 4 161 RU spike was measured in the single
# hour (16h UTC) covering two of those restarts, on top of the ~1000-1600/hour
# baseline. Raised to 100k (4x headroom for a multi-restart day). Solana cut
# to 0 (fail-closed immediately, same circuit-breaker mechanism) to offset the
# increase and stay within the Growth plan's shared 20M/month pool (100k+0+
# 400k=500k/day, BELOW the prior 600k/day total) -- explicit operator
# trade-off, not a calibration of Solana's own real usage. Real, current
# impact: solana_late_bonding_shadow's sourcing (pumpfun_curve_tracker.py) and
# pricing (pumpswap_ws.py) both gate on chain_spend("solana") -- a 0 cap stops
# both immediately, fail-closed, until the operator raises this back up.
DAILY_UNIT_CAP_PER_CHAIN: dict[str, int] = {
    "base": 100_000,
    "solana": 0,
    "robinhood": 400_000,
}
# 30/08 incident (reverted, see system_issues #279 and
# docs/HANDOFF_PIPELINE_MOMENTUM.md): Brique 6's Groupe C research backfill
# (session scratchpad, not production) consumed the day's Robinhood cap by
# ~19:44 UTC (403 568/400 000), blocking real production discovery. Cap was
# temporarily raised to 550_000 (19:49:30Z, commit a56e2511) to let both
# production and the backfill run through the rest of the day, then reverted
# here once the backfill concluded (20:55:36Z, final usage 512 828 RU) --
# this was never a new calibration of Robinhood's real sustained capacity.
# Fallback for any future 4th chain never explicitly calibrated above --
# same conservative default the shared constant used before this split.
_DEFAULT_CAP = 200_000


def cap_for(chain: str) -> int:
    return DAILY_UNIT_CAP_PER_CHAIN.get(chain, _DEFAULT_CAP)

# How long a cached `used_today` DB read stays valid before the next
# can_spend/remaining_today call re-queries. Short enough that a chain
# crossing the cap is caught within a few seconds, long enough to keep the
# real call rate off SQLite (see module docstring).
_READ_CACHE_SECONDS = 5.0

# In-memory, per-chain accumulator -- see record_usage_fast/flush_pending.
_pending_units: dict[str, int] = {}
# chain -> (time.monotonic() of last DB read, used_today() value at that read)
_read_cache: dict[str, tuple[float, int]] = {}


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


async def _used_today_uncached(chain: str, now: datetime | None = None) -> int:
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


async def used_today(chain: str, now: datetime | None = None) -> int:
    """DB total as of the last cached read (at most ``_READ_CACHE_SECONDS``
    old), PLUS whatever this process has accumulated since but not yet
    flushed -- precise enough for a daily cap without a DB read on every
    call. Pass ``now`` (a specific historical moment) to bypass the cache
    entirely, e.g. from a test."""
    if now is not None:
        return await _used_today_uncached(chain, now) + _pending_units.get(chain, 0)
    cached = _read_cache.get(chain)
    monotonic_now = time.monotonic()
    if cached is None or (monotonic_now - cached[0]) >= _READ_CACHE_SECONDS:
        db_value = await _used_today_uncached(chain)
        _read_cache[chain] = (monotonic_now, db_value)
        cached = _read_cache[chain]
    return cached[1] + _pending_units.get(chain, 0)


async def remaining_today(chain: str, now: datetime | None = None) -> int:
    used = await used_today(chain, now)
    return max(0, cap_for(chain) - used)


async def can_spend(chain: str, now: datetime | None = None) -> bool:
    """Fail-closed: when in doubt, refuse rather than risk exceeding the cap.

    25/08, operator request: notify the moment a chain's cap is actually
    reached, not just log it silently. ``_record_cap_alert_if_new`` is a
    cheap no-op once today's alert already exists (UNIQUE(chain, day)), so
    calling it on every refused call costs one INSERT OR IGNORE only after
    the cap is already hit -- never on the hot, spendable path."""
    spendable = await remaining_today(chain, now) > 0
    if not spendable:
        await _record_cap_alert_if_new(chain, now)
    return spendable


def record_usage_fast(chain: str, units: int) -> None:
    """Synchronous, in-memory only -- zero I/O, safe to call after every real
    RPC call in a hot polling loop (see module docstring). A caller's own
    periodic pass must call ``flush_pending()`` to actually persist this."""
    _pending_units[chain] = _pending_units.get(chain, 0) + units


async def flush_pending() -> None:
    """Persists every chain's accumulated record_usage_fast() calls in one
    write per chain, then clears the in-memory accumulator. Best-effort: a
    flush failure leaves the pending units in memory for the next attempt
    rather than silently losing them, but never blocks/crashes the caller's
    own loop over it."""
    global _pending_units
    to_flush = {chain: units for chain, units in _pending_units.items() if units}
    if not to_flush:
        return
    try:
        await _ensure_table()
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(str(aria_db_path())) as db:
            await db.executemany(
                "INSERT INTO chainstack_ru_log (chain, units, purpose, created_at) VALUES (?, ?, ?, ?)",
                [(chain, units, "flushed_batch", now) for chain, units in to_flush.items()],
            )
            await db.commit()
    except Exception:  # noqa: BLE001 -- keep the pending units for the next flush attempt
        return
    for chain in to_flush:
        _pending_units[chain] = _pending_units.get(chain, 0) - to_flush[chain]
        if _pending_units[chain] <= 0:
            _pending_units.pop(chain, None)
    _read_cache.clear()  # next used_today() re-reads the DB with the fresh total


async def record_usage(chain: str, units: int, *, purpose: str = "") -> None:
    """Logs units ACTUALLY spent, immediately (one DB write) -- for a caller
    OUTSIDE a hot polling loop where the per-call I/O cost is fine (e.g. a
    one-off backfill, a test). Hot-path callers use record_usage_fast +
    flush_pending instead (see module docstring)."""
    await _ensure_table()
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(str(aria_db_path())) as db:
        await db.execute(
            "INSERT INTO chainstack_ru_log (chain, units, purpose, created_at) VALUES (?, ?, ?, ?)",
            (chain, units, purpose, now),
        )
        await db.commit()
    _read_cache.pop(chain, None)  # next read re-queries rather than serving a stale cached value


async def daily_status(chain: str, now: datetime | None = None) -> dict:
    used = await used_today(chain, now)
    cap = cap_for(chain)
    return {
        "chain": chain,
        "cap_units": cap,
        "used_units": used,
        "remaining_units": max(0, cap - used),
        "day_started_at": day_start(now).isoformat(),
    }


async def _ensure_alerts_table() -> None:
    async with aiosqlite.connect(str(aria_db_path())) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS chainstack_cap_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chain TEXT NOT NULL,
                day TEXT NOT NULL,
                cap_units INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                notified_at TEXT,
                UNIQUE(chain, day)
            )
            """
        )
        await db.commit()


async def _record_cap_alert_if_new(chain: str, now: datetime | None = None) -> None:
    """INSERT OR IGNORE on (chain, day) -- the first refusal of the day for a
    chain creates exactly one alert row, every later refusal that same day is
    a silent no-op (never re-notifies for a cap already reported)."""
    await _ensure_alerts_table()
    day = day_start(now).date().isoformat()
    async with aiosqlite.connect(str(aria_db_path())) as db:
        await db.execute(
            "INSERT OR IGNORE INTO chainstack_cap_alerts (chain, day, cap_units, created_at) "
            "VALUES (?, ?, ?, ?)",
            (chain, day, cap_for(chain), datetime.now(timezone.utc).isoformat()),
        )
        await db.commit()


async def pop_unsent_cap_alerts() -> list[dict]:
    """Read + mark-as-sent in one pass -- for a caller's own periodic loop
    (its own Telegram/notification channel, this module never picks one
    itself: the standalone shadow process and the Docker backend each have a
    different notifier). Never returns the same alert twice."""
    await _ensure_alerts_table()
    async with aiosqlite.connect(str(aria_db_path())) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT id, chain, day, cap_units, created_at FROM chainstack_cap_alerts "
            "WHERE notified_at IS NULL ORDER BY id ASC"
        )
        rows = [dict(r) for r in await cur.fetchall()]
        if rows:
            now_iso = datetime.now(timezone.utc).isoformat()
            await db.executemany(
                "UPDATE chainstack_cap_alerts SET notified_at = ? WHERE id = ?",
                [(now_iso, r["id"]) for r in rows],
            )
            await db.commit()
    return rows
