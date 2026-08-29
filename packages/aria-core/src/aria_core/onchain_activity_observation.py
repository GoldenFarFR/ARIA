"""Observation-only log of on-chain swap activity per pool (29/08,
operator-directed) -- companion to ``discovery_liquidity_observation.py``,
same doctrine, different axis: that module answers "how much liquidity",
this one answers "how much activity" (swaps/volume/distinct traders),
kept deliberately separate so neither is ever silently blended into the
other before either is even proven informative.

**Why this exists.** `EVMSwapWebSocketFeed` already computes `swap_count`/
`cumulative_volume_quote`/`distinct_traders_count` per pool from events it
decodes anyway (zero extra RPC call) -- but nothing persists them. They
live only in the feed's own in-process state, so no series can be
reconstructed after the fact, and every restart silently loses whatever
history existed. This module closes that gap, strictly as an observation:
it never gates discovery, never triggers a network call, never chooses a
threshold or formula. The goal is to make the three axes -- A (absolute
activity), B (relative activity), C (acceleration) -- reconstructible
LATER from real data, not to compute any of them now.

**V2 is structurally biased, on purpose left visible, never hidden.**
`EVMSwapWebSocketFeed` only decodes V2's `Sync` event, which also fires on
Mint/Burn (add/remove liquidity) -- see that module's own "one real
casualty" comment on `pool.swap_count += 1`. `activity_quality` records
this explicitly (``"v2_biased"`` vs ``"v3v4_clean"``) so a future analysis
can filter or split by it rather than averaging V2's inflated count into a
V3/V4 clean one and getting a number that means nothing.

**Deltas, not just cumulatives.** A raw cumulative counter is fine for
counting but breaks the moment the feed process restarts (counters reset
to zero in memory, `EVMSwapWebSocketFeed` has no persistence of its own).
This module keeps a small in-memory cache of the last observation per pool
(``_last_observed``) so each new observation can also record
``swaps_delta``/``volume_quote_delta``/``traders_delta`` against it.  That
cache is itself in-process state -- it goes empty on every restart, same
as the feed it mirrors -- so the FIRST observation of a pool since a
restart marks ``baseline_reset=True`` and leaves the deltas ``NULL``
rather than fabricating a delta against a counter that silently reset to
zero underneath it.

**Buy/sell flow (29/08, brique 2/5, operator GO after brique 1's production
checkpoint).** Same doctrine, one more axis: `EVMSwapWebSocketFeed` now also
classifies each decoded Swap's direction (see its own `EVMSwapSnapshot`
comment for the V2/V3/V4 sign convention) into `buy_count`/`sell_count`/
`undetermined_count` and their matching quote volumes. This module records
the cumulative values plus deltas for `buy_count`/`sell_count`/
`buy_volume_quote`/`sell_volume_quote` (same restart-safe `baseline_reset`
handling). `undetermined_count`/`undetermined_volume_quote` are recorded as
cumulatives only, no delta -- not requested, and the invariant
(`buy_count + sell_count + undetermined_count == swap_count`) is already
checkable from the cumulative columns alone. Still no score, no threshold,
no `net_flow`/`buy_pressure` computed or stored here -- `net_flow_quote` is
a read-time property on `EVMSwapSnapshot`, never persisted, and building an
actual pressure/acceleration feature is explicitly out of scope for this
brique.

**Strictly log-only, best-effort.** Same contract as
``discovery_liquidity_observation.py``: never influences the caller's own
decision, never triggers a network call, records only values already
computed by the feed. A logging failure must never break discovery.

**`None` stays `None`.** When the feed has no live snapshot for a pool
(``not snapshot.available``), every activity field is recorded as ``NULL``
-- never a fabricated ``0``, which would be indistinguishable from "we
checked and it's genuinely zero" (see this module's own tests)."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import aiosqlite

from . import db_migrations
from .paths import shadow_db_path

logger = logging.getLogger(__name__)

TABLE = "onchain_activity_observation_log"

_ensured_db_paths: set[str] = set()

# In-process only, one entry per (chain, pool_address) -- (swap_count,
# cumulative_volume_quote, distinct_traders_count, buy_count, sell_count,
# buy_volume_quote, sell_volume_quote) as of the last recorded observation.
# Empty on import, so the first observation of any pool after a process
# restart always finds nothing here and marks baseline_reset.
_last_observed: dict[str, tuple[int, float, int, int, int, float, float]] = {}


def _cache_key(chain: str, pool_address: str) -> str:
    return f"{chain}:{pool_address}"


def _db_path() -> str:
    return str(shadow_db_path())


def _activity_quality(family: str | None) -> str | None:
    if family is None:
        return None
    if family == "v2":
        return "v2_biased"
    if family in ("v3", "v4"):
        return "v3v4_clean"
    return "unknown"


async def _ensure_table(db_path: str | None = None) -> None:
    path = db_path or _db_path()
    if path in _ensured_db_paths:
        return
    async with aiosqlite.connect(path) as db:
        await db.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TABLE} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                observed_at TEXT NOT NULL,
                chain TEXT NOT NULL,
                pool_address TEXT NOT NULL,
                token_address TEXT NOT NULL,
                family TEXT,
                activity_quality TEXT,
                swap_count INTEGER,
                cumulative_volume_quote REAL,
                distinct_traders_count INTEGER,
                last_swap_age_seconds REAL,
                swaps_delta INTEGER,
                volume_quote_delta REAL,
                traders_delta INTEGER,
                buy_count INTEGER,
                sell_count INTEGER,
                undetermined_count INTEGER,
                buy_volume_quote REAL,
                sell_volume_quote REAL,
                undetermined_volume_quote REAL,
                buy_count_delta INTEGER,
                sell_count_delta INTEGER,
                buy_volume_quote_delta REAL,
                sell_volume_quote_delta REAL,
                baseline_reset INTEGER NOT NULL
            )
            """
        )
        await db.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_pool "
            f"ON {TABLE} (chain, pool_address, observed_at)"
        )
        # 29/08, brique 2/5 -- catches up a table created before buy/sell
        # columns existed. CREATE TABLE IF NOT EXISTS above already covers a
        # fresh database; this is only needed for one already running in
        # production with the older schema.
        await db_migrations.ensure_columns(db, TABLE, [
            ("buy_count", "INTEGER"),
            ("sell_count", "INTEGER"),
            ("undetermined_count", "INTEGER"),
            ("buy_volume_quote", "REAL"),
            ("sell_volume_quote", "REAL"),
            ("undetermined_volume_quote", "REAL"),
            ("buy_count_delta", "INTEGER"),
            ("sell_count_delta", "INTEGER"),
            ("buy_volume_quote_delta", "REAL"),
            ("sell_volume_quote_delta", "REAL"),
        ])
        await db.commit()
    _ensured_db_paths.add(path)


async def record_observation(
    *,
    chain: str,
    pool_address: str,
    token_address: str,
    available: bool,
    family: str | None,
    swap_count: int | None,
    cumulative_volume_quote: float | None,
    distinct_traders_count: int | None,
    last_swap_age_seconds: float | None,
    buy_count: int | None = None,
    sell_count: int | None = None,
    undetermined_count: int | None = None,
    buy_volume_quote: float | None = None,
    sell_volume_quote: float | None = None,
    undetermined_volume_quote: float | None = None,
    db_path: str | None = None,
) -> None:
    """One row per candidate check where the feed was asked for a snapshot.
    ``available=False`` (no live snapshot for this pool yet) records every
    activity field as NULL and never touches the delta cache -- the next
    real snapshot still compares against the last REAL observation, not
    against this gap. Never raises -- a failure here must never turn a
    real discovery cycle into a broken one.

    Buy/sell params default to ``None`` (rather than being required) so an
    existing caller upgraded only for the liquidity/activity axes keeps
    working unchanged -- brique 2/5, 29/08."""
    path = db_path or _db_path()
    key = _cache_key(chain, pool_address)

    if not available:
        swaps_delta = volume_quote_delta = traders_delta = None
        buy_count_delta = sell_count_delta = None
        buy_volume_quote_delta = sell_volume_quote_delta = None
        baseline_reset = False
        family = None
        activity_quality = None
        swap_count = cumulative_volume_quote = distinct_traders_count = None
        last_swap_age_seconds = None
        buy_count = sell_count = undetermined_count = None
        buy_volume_quote = sell_volume_quote = undetermined_volume_quote = None
    else:
        activity_quality = _activity_quality(family)
        prior = _last_observed.get(key)
        if prior is None:
            swaps_delta = volume_quote_delta = traders_delta = None
            buy_count_delta = sell_count_delta = None
            buy_volume_quote_delta = sell_volume_quote_delta = None
            baseline_reset = True
        else:
            (
                prior_swaps, prior_volume, prior_traders,
                prior_buy_count, prior_sell_count,
                prior_buy_volume, prior_sell_volume,
            ) = prior
            swaps_delta = None if swap_count is None else swap_count - prior_swaps
            volume_quote_delta = (
                None if cumulative_volume_quote is None else cumulative_volume_quote - prior_volume
            )
            traders_delta = (
                None if distinct_traders_count is None else distinct_traders_count - prior_traders
            )
            buy_count_delta = None if buy_count is None else buy_count - prior_buy_count
            sell_count_delta = None if sell_count is None else sell_count - prior_sell_count
            buy_volume_quote_delta = (
                None if buy_volume_quote is None else buy_volume_quote - prior_buy_volume
            )
            sell_volume_quote_delta = (
                None if sell_volume_quote is None else sell_volume_quote - prior_sell_volume
            )
            baseline_reset = False
        _last_observed[key] = (
            swap_count or 0, cumulative_volume_quote or 0.0, distinct_traders_count or 0,
            buy_count or 0, sell_count or 0,
            buy_volume_quote or 0.0, sell_volume_quote or 0.0,
        )

    try:
        await _ensure_table(path)
        async with aiosqlite.connect(path) as db:
            await db.execute(
                f"""
                INSERT INTO {TABLE}
                    (observed_at, chain, pool_address, token_address, family,
                     activity_quality, swap_count, cumulative_volume_quote,
                     distinct_traders_count, last_swap_age_seconds,
                     swaps_delta, volume_quote_delta, traders_delta,
                     buy_count, sell_count, undetermined_count,
                     buy_volume_quote, sell_volume_quote, undetermined_volume_quote,
                     buy_count_delta, sell_count_delta,
                     buy_volume_quote_delta, sell_volume_quote_delta,
                     baseline_reset)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.now(timezone.utc).isoformat(),
                    chain, pool_address, token_address, family, activity_quality,
                    swap_count, cumulative_volume_quote, distinct_traders_count,
                    last_swap_age_seconds, swaps_delta, volume_quote_delta, traders_delta,
                    buy_count, sell_count, undetermined_count,
                    buy_volume_quote, sell_volume_quote, undetermined_volume_quote,
                    buy_count_delta, sell_count_delta,
                    buy_volume_quote_delta, sell_volume_quote_delta,
                    int(baseline_reset),
                ),
            )
            await db.commit()
    except Exception as exc:  # noqa: BLE001 -- observation must never break discovery
        logger.info("onchain_activity_observation: record failed for %s (%s)", pool_address, exc)
