"""Post-qualification Early-Life observation window (04/09, operator go --
"Chantier A", strictly observation-only: "Il peut mesurer et conserver,
sans declencher de trade... il ne doit pas decider retroactivement qu'un
token etait bon ou mauvais").

**The gap this closes.** `onchain_pool_discovery.OnChainPoolDiscoveryFeed`
already records a raw activity snapshot (`onchain_activity_observation`)
every cycle -- but only for candidates still in `self._candidates`, i.e.
still PENDING qualification. The instant a candidate qualifies it is
removed from that dict (`expired_keys`/`self._candidates.pop`), so its
observation stream stops at that exact moment. Confirmed live on $LEGS
(03/09, pool `0xf6214b4907a2871408a568c585947ec16b6dc8ea`): qualified via
`cold_read` on the very first check, exactly ONE row ever recorded in
`onchain_activity_observation_log`, every activity field NULL -- zero
trajectory data, even though the operator explicitly asked to be able to
reconstruct "t=0 creation, t=5s X swaps, t=10s volume=X... t=120s
acheteurs=...".

**Raw observations, never derived features (operator's own requirement,
verbatim: "il faut enregistrer les fenetres et les observations BRUTES
necessaires pour recalculer les features. Sinon dans trois semaines on
aura acceleration=4.72 mais impossible de savoir avec quelles observations
elle avait ete calculee").** This module computes nothing -- it only
schedules repeated calls into the existing, already-tested, append-only
`onchain_activity_observation.record_observation`, mirroring EXACTLY the
call shape `onchain_pool_discovery.py` already uses for pending candidates
(same field list, same `quote_is_weth`-gated `eth_usd_rate_at_observation`
rule) so a pool's trajectory is one continuous, uniformly-shaped series
whether the rows were recorded before or after qualification. Any feature
(acceleration, buyer growth...) is a separate, on-demand READ of this raw
history, computed later, never stored here -- so a future recalculation
can always prove exactly which observations were available at time t.

**Never touches Entry/Exit.** No code path here calls `record_signals`,
`advance_exit_simulation`, or any trading decision -- per the operator's
explicit scoping of this chantier: "Je ne toucherais pas encore a
Entry/Exit ni a la logique de la poche autorisee."

**Bounded window, not indefinite.** `list_active`/`advance_tracking_cycle`
only consider a candidate while `now - qualified_at < window_seconds`
(default 300s / 5 minutes -- covers the full early-life trajectory the
operator described, t=0 through t=120s and beyond, without tracking a pool
forever). A candidate past the window is simply no longer polled; its
tracking row stays in the table (never deleted) as a factual record of
when tracking started and stopped."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import aiosqlite

from . import onchain_activity_observation
from .paths import shadow_db_path

logger = logging.getLogger(__name__)

TABLE = "early_life_tracking"

DEFAULT_WINDOW_SECONDS = 300.0

_ensured_db_paths: set[str] = set()


def _db_path() -> str:
    return str(shadow_db_path())


def _activity_db_path() -> str:
    return onchain_activity_observation._db_path()  # noqa: SLF001 -- same DB, test convenience alias


async def _ensure_table() -> None:
    path = _db_path()
    if path in _ensured_db_paths:
        return
    async with aiosqlite.connect(path) as db:
        await db.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TABLE} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chain TEXT NOT NULL,
                pool_address TEXT NOT NULL,
                token_address TEXT NOT NULL,
                qualified_at TEXT NOT NULL,
                started_at TEXT NOT NULL,
                UNIQUE(chain, pool_address)
            )
            """
        )
        await db.execute(f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_chain ON {TABLE} (chain, qualified_at)")
        await db.commit()
    _ensured_db_paths.add(path)


async def start_tracking(
    chain: str, pool_address: str, token_address: str, *, qualified_at: datetime | None = None,
) -> None:
    """Registers a just-qualified candidate for post-qualification
    observation. Idempotent -- a pool already tracked (same chain +
    pool_address) is never re-inserted, so a candidate re-notified isn't
    double-tracked."""
    await _ensure_table()
    at = qualified_at or datetime.now(timezone.utc)
    started_at = datetime.now(timezone.utc)
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            f"""
            INSERT OR IGNORE INTO {TABLE} (chain, pool_address, token_address, qualified_at, started_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (chain, pool_address, token_address, at.isoformat(), started_at.isoformat()),
        )
        await db.commit()


async def list_active(
    chain: str, *, now: datetime | None = None, window_seconds: float = DEFAULT_WINDOW_SECONDS,
) -> list[dict]:
    """Candidates for ``chain`` still within their observation window
    (``now - qualified_at < window_seconds``). Never mutates the table --
    an expired candidate simply stops appearing here, its row is never
    deleted."""
    await _ensure_table()
    at = now or datetime.now(timezone.utc)
    cutoff = (at - timedelta(seconds=window_seconds)).isoformat()
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            f"SELECT chain, pool_address, token_address, qualified_at FROM {TABLE} "
            f"WHERE chain = ? AND qualified_at >= ?",
            (chain, cutoff),
        )
        rows = await cur.fetchall()
    return [dict(row) for row in rows]


async def advance_tracking_cycle(
    chain: str, *, ws_feed, now: datetime | None = None, window_seconds: float = DEFAULT_WINDOW_SECONDS,
) -> int:
    """For every active candidate on ``chain``, pulls one snapshot from
    ``ws_feed`` and records it via the existing, already-tested
    ``onchain_activity_observation.record_observation`` -- the exact same
    call shape ``onchain_pool_discovery.py`` uses for PENDING candidates,
    so a pool's row series is uniform whether recorded before or after
    qualification. Best-effort per candidate: one snapshot failure never
    stops the others. Returns the number of snapshots successfully
    recorded."""
    at = now or datetime.now(timezone.utc)
    active = await list_active(chain, now=at, window_seconds=window_seconds)
    recorded = 0
    for cand in active:
        pool_address = cand["pool_address"]
        try:
            snapshot = ws_feed.get_snapshot(pool_address)
            await onchain_activity_observation.record_observation(
                chain=chain, pool_address=pool_address, token_address=cand["token_address"],
                available=snapshot.available, family=snapshot.family if snapshot.available else None,
                swap_count=snapshot.swap_count if snapshot.available else None,
                cumulative_volume_quote=(
                    snapshot.cumulative_volume_quote if snapshot.available else None
                ),
                distinct_traders_count=(
                    snapshot.distinct_traders_count if snapshot.available else None
                ),
                last_swap_age_seconds=snapshot.stale_seconds if snapshot.available else None,
                buy_count=snapshot.buy_count if snapshot.available else None,
                sell_count=snapshot.sell_count if snapshot.available else None,
                undetermined_count=snapshot.undetermined_count if snapshot.available else None,
                buy_volume_quote=snapshot.buy_volume_quote if snapshot.available else None,
                sell_volume_quote=snapshot.sell_volume_quote if snapshot.available else None,
                undetermined_volume_quote=(
                    snapshot.undetermined_volume_quote if snapshot.available else None
                ),
                liquidity_added_quote=(
                    snapshot.liquidity_added_quote if snapshot.available else None
                ),
                liquidity_removed_quote=(
                    snapshot.liquidity_removed_quote if snapshot.available else None
                ),
                liquidity_added_raw=snapshot.liquidity_added_raw if snapshot.available else None,
                liquidity_removed_raw=snapshot.liquidity_removed_raw if snapshot.available else None,
                price_quote=snapshot.price_quote if snapshot.available else None,
                price_usd=snapshot.price_usd if snapshot.available else None,
                reserve_usd=snapshot.reserve_usd if snapshot.available else None,
                raw_liquidity=snapshot.raw_liquidity if snapshot.available else None,
                quote_reserve_raw=snapshot.quote_reserve_raw if snapshot.available else None,
                eth_usd_rate_at_observation=(
                    ws_feed.onchain_eth_usd_rate()
                    if snapshot.available and snapshot.quote_is_weth
                    else None
                ),
            )
            recorded += 1
        except Exception as exc:  # noqa: BLE001 -- one candidate's failure must never stop the cycle
            logger.info("early_life_observation: snapshot failed for %s (%s)", pool_address, exc)
    return recorded
