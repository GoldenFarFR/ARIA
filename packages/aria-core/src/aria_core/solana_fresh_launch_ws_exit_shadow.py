"""Solana "fresh launch" shadow -- WEBSOCKET-EXIT VARIANT (19/08). A SEPARATE,
PARALLEL A/B counterpart to ``solana_fresh_launch_shadow.py``, never a
replacement -- both pockets run side by side so the operator can compare
them objectively before any production change. NOT wired to the heartbeat,
NOT gated by any ``ARIA_*`` flag, silently available only.

**What is held constant vs. what changes (the whole point of this module)**:
  - ENTRY criterion: ``MAX_POOL_AGE_MINUTES`` stays IMPORTED (byte-for-byte
    shared) from ``solana_fresh_launch_shadow.py``. ``MIN_LIQUIDITY_USD`` is
    DECOUPLED as of 20/08 (own constant below, no longer imported) --
    operator-directed performance investigation found the two pockets'
    liquidity/PnL bands genuinely diverge (this pocket's own real closures
    show 6-10k$ as a WORSE band than 3-6k$, the opposite of the original
    module's confirmed 6-20k$ sweet spot), so forcing them to share one
    value would push whichever pocket's real data disagrees into its own
    dead zone. See ``MIN_LIQUIDITY_USD``'s own docstring below for the data.
  - EXIT mechanism: DIFFERENT by design. No scale-out ladder -- the position
    is held 100% until ONE of trailing_stop / liquidity_collapse / max_hold
    fires, then closed in a single shot. ``TRAILING_STOP_PCT``/
    ``MAX_HOLD_MINUTES``/``LIQUIDITY_COLLAPSE_EXIT_PCT`` are this module's
    OWN constants (not shared state, same convention as every other shadow
    pocket in this dome -- see the original module's own docstring for why),
    but set to the SAME numeric values as the original (15%/60min/50%) so
    the comparison isolates exactly one variable: how the trailing stop is
    DETECTED (continuous websocket push vs. 60s/5-candle REST polling), not
    a different threshold.
  - PRICE SOURCE: ``advance_exit_simulation`` accepts an optional
    ``ws_feed`` (a ``PumpSwapWebSocketFeed``, see
    ``services/pumpswap_ws.py``) -- when given and it reports a fresh price
    for a row's pool, that price/reserve drives the exit check; otherwise
    (feed not given, pool not PumpSwap, non-WSOL-quoted, feed stale/
    disconnected) this module falls back to the EXACT SAME REST cascade the
    original module uses (``_snapshot_with_fallback``, imported from
    ``solana_pump_shadow.py``, never reimplemented). Never a single point of
    failure: a websocket outage silently degrades this pocket to the same
    REST-polling behavior as its A/B counterpart, nothing breaks.

**Exit-check as a pure, injectable function (``evaluate_exit``)** -- the
actual guard logic (liquidity_collapse > trailing_stop > max_hold priority,
corrupted-price sanity guard) is factored into a function that accepts an
ALREADY-KNOWN ``current_price``/``reserve_usd``/``dex_id`` and does no I/O of
its own. This is what makes the SAME exit rule callable identically from a
websocket-driven per-notification check or a REST polling cycle -- the
mission's explicit design requirement, and the reason ``advance_exit_
simulation`` (the only function that touches the network/DB) is a thin
orchestration shell around it.

**Round-robin exit queue, ported from day one** (``ORDER BY
COALESCE(last_checked_at, detected_at)``) -- the exact fix for the real
starvation bug found live 19/08 in ``solana_fresh_launch_shadow.py`` (a row
whose price source keeps failing must still advance past the queue's front,
else every younger position behind it starves forever). Never reintroduced
here from scratch.

Same bright-line doctrine as every other shadow module in this dome: never
opens a real or paper-capital position, never calls
``wallet_guard``/``agent_wallet_pilot``/``paper_trader.open_position``, pure
read+log+simulate. Own dedicated SQLite table
(``solana_fresh_launch_ws_exit_shadow_log``) -- NEVER shares
``solana_fresh_launch_shadow_log`` (a shared table would make the two
pockets no longer independently comparable, and would corrupt each other's
dedup logic)."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

import aiosqlite

from aria_core.paths import shadow_db_path
from aria_core.services import dexpaprika, rugcheck
from aria_core.services.geckoterminal import (
    GeckoTerminalClient,
    PoolSnapshot,
    TrendingPool,
    geckoterminal_client,
)
from aria_core.services.pumpportal_ws import PumpPortalNewTokenEvent, PumpPortalNewTokenFeed
from aria_core.solana_fresh_launch_shadow import (
    MAX_POOL_AGE_MINUTES,
    PEAK_PRICE_SANITY_MULTIPLE,
    SUPPORT_CANDLE_INTERVAL,
    SUPPORT_CANDLE_MAX_COUNT,
)
from aria_core.solana_pump_shadow import (
    SIMULATED_TRADE_SIZE_USD,
    _apply_price_impact_and_fee,
    _minutes_since,
    _snapshot_with_fallback,
)

logger = logging.getLogger(__name__)

DB_PATH = str(shadow_db_path())

TABLE = "solana_fresh_launch_ws_exit_shadow_log"

# Own constants, deliberately the SAME numeric values as
# solana_fresh_launch_shadow.py -- see module docstring for why they are
# not shared state.
TRAILING_STOP_PCT = 15.0
MAX_HOLD_MINUTES = 60.0
LIQUIDITY_COLLAPSE_EXIT_PCT = 50.0

# 20/08, decoupled from solana_fresh_launch_shadow.MIN_LIQUIDITY_USD
# (operator-directed performance investigation, 558-closure sample). 500$-wide
# buckets inside this pocket's own operative 2000-5000$ range (bounded above
# by MAX_LIQUIDITY_USD_ENTRY, see below): the 2000-2499$ bucket alone holds
# 253/394 closures (64% of the sample) at a 3.2% winrate/0.916 avg realistic
# multiplier -- by far the worst bucket in the pocket. From 3000$ up, winrate
# roughly triples (13.8-31.3% across the 3000-4500$ buckets). Raising the
# floor to 3000 removes the dead zone while staying well inside the
# MAX_LIQUIDITY_USD_ENTRY=5000 ceiling the 19/08 finding below still confirms
# on the current sample.
MIN_LIQUIDITY_USD = 3000.0

# 19/08, operator decision after reviewing this pocket's own real closures
# (144 with a valid reserve_usd read): unlike every other liquidity check in
# this dome (a MINIMUM floor), this pocket's real data shows the opposite
# risk at the HIGH end. Bucketed by entry reserve_usd: 3990-21200$ (top
# quartile) closed at a 44.4% liquidity_collapse rate vs 11.1% for the
# bottom two quartiles; a finer 1000$-wide cut confirms >=5000$ specifically
# as the worst bucket in the whole pocket (21 cases, avg realistic_final_
# multiplier 0.702, i.e. -29.8% average) -- clearly worse than every lower
# bucket, including the 3000-3500$ one that stays net positive (+21.2%)
# despite a similar 27% collapse rate (a favorable winner/loser asymmetry
# that high-liquidity entries don't share). Working theory: on a
# support-bounce entry, unusually high liquidity at entry likely signals a
# speculative hype spike rather than a stable floor -- exactly the kind of
# pool that dumps hard once that hype fades. Applied as an entry-time reject
# (never confirmed) rather than a post-entry exit, since reserve_usd is
# already known before the simulated buy -- see
# ``_track_candidate_pumpportal``'s own call site.
MAX_LIQUIDITY_USD_ENTRY = 5000.0

# 20/08, operator-directed emergency performance investigation. This pocket
# deliberately did NOT apply FAST-DISCOVERY's holder-concentration reject --
# it was the A/B variable kept open on purpose. That A/B has now returned its
# verdict on THIS pocket's own 1003 real closures, and it is unambiguous:
#   top_holder <85%   n=267  winrate 18.7%  avg -6.19%
#   top_holder 85-92% n=63   winrate  7.9%  avg -9.43%
#   top_holder 92-97% n=136  winrate  8.1%  avg -7.27%
#   top_holder >=97%  n=529  winrate  0.8%  avg -10.30%   <-- 53% of the flow
# The >=97% band alone carried 53% of every entry this pocket made, at a 0.8%
# winrate -- statistically indistinguishable from noise, and the single
# mechanical reason WS-EXIT sat at a 9% winrate while FAST-DISCOVERY (which
# has cut this segment since 20/08) sat at 17%. Simulated over those same real
# closures, cutting >=92% takes this pocket from 7.1% to 16.6% winrate.
# Keeping the A/B open any longer would only re-confirm a settled result at
# the cost of more (fictitious) capital, so it is closed here deliberately --
# same value as FAST-DISCOVERY's own calibrated constant, applied the same
# way (an early exit on RugCheck's async backfill, never an entry gate, which
# would reintroduce the multi-minute RugCheck wait this pocket's design
# exists to avoid).
HOLDER_CONCENTRATION_REJECT_PCT = 92.0

_ensured_db_paths: set[str] = set()


def _db_path() -> str:
    return DB_PATH


async def _ensure_table() -> None:
    path = _db_path()
    if path in _ensured_db_paths:
        return
    async with aiosqlite.connect(path) as db:
        await db.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TABLE} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pool_address TEXT NOT NULL,
                token_address TEXT,
                chain TEXT NOT NULL DEFAULT 'solana',
                symbol TEXT,
                detected_at TEXT NOT NULL,
                entry_price REAL NOT NULL,
                reserve_usd REAL,
                pool_created_at TEXT,
                distance_from_support_pct REAL,
                support_range_low REAL,
                support_range_high REAL,
                support_candle_count INTEGER,
                remaining_qty REAL NOT NULL DEFAULT 1.0,
                realized_proceeds REAL NOT NULL DEFAULT 0.0,
                peak_price REAL,
                exit_reason TEXT,
                final_multiplier REAL,
                last_checked_at TEXT,
                last_price REAL,
                last_reserve_usd REAL,
                last_price_source TEXT,
                exit_price_source TEXT,
                realistic_entry_price REAL,
                realistic_realized_proceeds REAL NOT NULL DEFAULT 0.0,
                realistic_final_multiplier REAL,
                trailing_stop_pct_used REAL,
                h1_pct REAL,
                m5_pct REAL,
                h6_pct REAL,
                h24_pct REAL,
                volume_usd_24h REAL,
                transactions_24h INTEGER,
                dex_id TEXT,
                rugcheck_score INTEGER,
                rugcheck_risks TEXT,
                rugcheck_top_holder_pct REAL,
                rugcheck_creator TEXT
            )
            """
        )
        await db.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_lookup ON {TABLE} (pool_address, chain, exit_reason)"
        )
        await db.execute(f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_detected_at ON {TABLE} (detected_at)")
        # 20/08 -- hot migration for a table that already pre-exists in prod
        # (SQLite's CREATE TABLE IF NOT EXISTS above never adds columns to an
        # existing table), same pattern as agent_wallet_monitor.py/
        # paper_trader.py: PRAGMA table_info then ALTER TABLE only if missing.
        existing = {row[1] for row in await (await db.execute(f"PRAGMA table_info({TABLE})")).fetchall()}
        if "market_cap_sol_at_creation" not in existing:
            await db.execute(f"ALTER TABLE {TABLE} ADD COLUMN market_cap_sol_at_creation REAL")
        await db.commit()
    _ensured_db_paths.add(path)


async def _has_open_signal(db: aiosqlite.Connection, pool_address: str, chain: str) -> bool:
    cur = await db.execute(
        f"SELECT 1 FROM {TABLE} WHERE pool_address = ? AND chain = ? AND exit_reason IS NULL LIMIT 1",
        (pool_address, chain),
    )
    return (await cur.fetchone()) is not None


async def closures_so_far() -> int:
    await _ensure_table()
    async with aiosqlite.connect(_db_path()) as db:
        cur = await db.execute(f"SELECT COUNT(*) FROM {TABLE} WHERE exit_reason IS NOT NULL")
        (count,) = await cur.fetchone()
    return count


async def get_sync_filtered_candidates(pools: list[TrendingPool], *, chain: str = "solana") -> list[TrendingPool]:
    """Structurally the SAME predicate as
    ``solana_fresh_launch_shadow.get_sync_filtered_candidates`` -- the
    threshold VALUES (``MIN_LIQUIDITY_USD``/``MAX_POOL_AGE_MINUTES``) are
    imported, never redefined. This wrapper itself can't be imported as-is
    (it dedups against the OTHER module's own table via its own
    ``_db_path()``) -- each pocket necessarily dedups against ITS OWN open
    positions so the two pockets can independently log a signal for the
    SAME candidate pool (that's the point of the A/B: both should see the
    same entry universe, only the exit differs)."""
    candidates: list[TrendingPool] = []
    async with aiosqlite.connect(_db_path()) as db:
        for pool in pools:
            if pool.price_usd is None or pool.pool_created_at is None:
                continue
            if (pool.reserve_usd or 0.0) < MIN_LIQUIDITY_USD:
                continue
            age_minutes = (datetime.now(timezone.utc) - pool.pool_created_at).total_seconds() / 60.0
            if age_minutes < 0 or age_minutes > MAX_POOL_AGE_MINUTES:
                continue
            if await _has_open_signal(db, pool.pool_address, chain):
                continue
            candidates.append(pool)
    return candidates


async def record_signals(pools: list[TrendingPool], *, chain: str = "solana") -> dict:
    """Sourcing + dedup + log -- best-effort throughout, never raises into
    the caller. Structurally mirrors ``solana_fresh_launch_shadow.
    record_signals`` (same 3-pass connection-scoping discipline, same
    informational-only support-distance computation, same rugcheck
    enrichment) -- necessary duplication since each pocket owns its own
    persistence, but every ENTRY THRESHOLD used below is imported, never
    redefined (see module docstring)."""
    result = {"fetched": len(pools), "candidates": 0, "logged": 0}
    try:
        await _ensure_table()
        candidates = await get_sync_filtered_candidates(pools, chain=chain)
        result["candidates"] = len(candidates)

        rows_to_insert: list[tuple] = []
        candles_by_row: list[list] = []
        for pool in candidates:
            candles: list = []
            try:
                candles = await dexpaprika._fetch_one_interval(
                    pool.pool_address, chain, SUPPORT_CANDLE_INTERVAL,
                )
            except Exception as exc:  # noqa: BLE001 -- one candidate's failure never blocks the batch
                logger.info(
                    "solana_fresh_launch_ws_exit_shadow: candle fetch failed for %s (%s)",
                    pool.pool_address, exc,
                )
                candles = []

            current_price = pool.price_usd
            distance_from_support_pct: float | None = None
            support_range_low: float | None = None
            support_range_high: float | None = None
            support_candle_count = 0
            if candles:
                n = min(SUPPORT_CANDLE_MAX_COUNT, len(candles))
                last_n = candles[-n:]
                support_candle_count = n
                current_price = last_n[-1].close or current_price
                range_low = min(c.low for c in last_n)
                range_high = max(c.high for c in last_n)
                if range_low and range_high > range_low:
                    support_range_low = range_low
                    support_range_high = range_high
                    distance_from_support_pct = (current_price - range_low) / (range_high - range_low) * 100.0

            if current_price is None or current_price <= 0:
                # Same real bug guarded against as the original module (a
                # brand-new pool can legitimately report price_usd=0.0) --
                # never log an unpriceable phantom position.
                continue

            realistic_entry_price = _apply_price_impact_and_fee(
                current_price, trade_size_usd=SIMULATED_TRADE_SIZE_USD,
                reserve_usd=pool.reserve_usd, side="buy",
            )

            rugcheck_score: int | None = None
            rugcheck_risks: str | None = None
            rugcheck_top_holder_pct: float | None = None
            rugcheck_creator: str | None = None
            if pool.token_address:
                try:
                    report = await rugcheck.get_token_report(pool.token_address)
                    if report.available:
                        rugcheck_score = report.score_normalised
                        rugcheck_risks = ",".join(report.risks) if report.risks else None
                        rugcheck_top_holder_pct = report.top_holder_pct
                        rugcheck_creator = report.creator
                except Exception as exc:  # noqa: BLE001 -- enrichment must never break the log pass
                    logger.info(
                        "solana_fresh_launch_ws_exit_shadow: rugcheck lookup failed for %s (%s)",
                        pool.token_address, exc,
                    )

            rows_to_insert.append((
                pool.pool_address, pool.token_address, chain, pool.symbol,
                datetime.now(timezone.utc).isoformat(), current_price,
                pool.reserve_usd, pool.pool_created_at.isoformat(),
                distance_from_support_pct, support_range_low, support_range_high, support_candle_count,
                current_price, realistic_entry_price,
                pool.price_change_pct.get("h1"), pool.price_change_pct.get("m5"),
                pool.price_change_pct.get("h6"), pool.price_change_pct.get("h24"),
                pool.volume_usd_24h, pool.transactions_24h, pool.dex_id,
                rugcheck_score, rugcheck_risks, rugcheck_top_holder_pct, rugcheck_creator,
            ))
            candles_by_row.append(candles)

        if rows_to_insert:
            from aria_core import shadow_candle_archive

            async with aiosqlite.connect(_db_path()) as db:
                for row, candles in zip(rows_to_insert, candles_by_row):
                    cur = await db.execute(
                        f"""
                        INSERT INTO {TABLE} (
                            pool_address, token_address, chain, symbol, detected_at, entry_price,
                            reserve_usd, pool_created_at,
                            distance_from_support_pct, support_range_low, support_range_high, support_candle_count,
                            remaining_qty, realized_proceeds, peak_price,
                            realistic_entry_price,
                            h1_pct, m5_pct, h6_pct, h24_pct, volume_usd_24h, transactions_24h, dex_id,
                            rugcheck_score, rugcheck_risks, rugcheck_top_holder_pct, rugcheck_creator
                        ) VALUES (
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1.0, 0.0, ?, ?,
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                        )
                        """,
                        row,
                    )
                    new_id = cur.lastrowid
                    await db.commit()
                    if candles:
                        await shadow_candle_archive.store_candles(
                            module="solana_fresh_launch_ws_exit", position_id=new_id,
                            pool_address=row[0], chain=chain, phase="before", candles=candles,
                        )
            result["logged"] = len(rows_to_insert)
    except Exception as exc:  # noqa: BLE001 -- shadow logging must never break the caller
        logger.info("solana_fresh_launch_ws_exit_shadow: record_signals failed (%s)", exc)
    return result


# 19/08, operator decision: this pocket's own sourcing (``record_signals``
# above, DexPaprika trending-pools poll every 60s) starved out almost
# entirely once the fresh-launch discovery moved to PumpPortal's instant
# push for the FAST-DISCOVERY pocket -- 11 total positions ever logged here
# vs 188 there, and 0 currently open. The functions below give this pocket
# the SAME PumpPortal-driven sourcing as FAST-DISCOVERY (structurally
# mirroring ``solana_fresh_launch_fast_discovery_shadow.py``'s own
# ``_track_candidate``/``run_forever`` -- necessary duplication since each
# pocket owns its own persistence/schema, never reimplemented differently
# where the two are the same, e.g. ``_resolve_liquidity_snapshot`` itself is
# imported, not copied). The A/B comparison this pocket exists for now
# isolates a DIFFERENT variable than originally designed (see module
# docstring): no longer "REST polling vs websocket exit-detection" against
# the retired ORIGINAL pocket, but "single-shot exit, no holder-concentration
# filter" (this pocket) vs "single-shot exit, WITH the holder-concentration
# filter" (FAST-DISCOVERY) -- still a valid, deliberate one-variable-at-a-time
# comparison, just a different variable than 19/08's earlier note describes.


async def _has_open_or_recent_signal(db: aiosqlite.Connection, pool_address: str, chain: str) -> bool:
    """Same dedup key as ``_has_open_signal`` -- a closed row for this pool
    never blocks re-tracking it (only a currently-open one does), matching
    FAST-DISCOVERY's own dedup semantics for the same PumpPortal-driven
    sourcing pattern."""
    return await _has_open_signal(db, pool_address, chain)


# 20/08 -- how long the pre-trade gate may wait on RugCheck before giving up.
# Sized against real measurements, not guessed: RugCheck answers in ~0.17s, and
# the only real wait is this dome's OWN shared throttle (_MIN_INTERVAL_S=4.5s
# in services/rugcheck.py). 12s covers a couple of queued turns at that
# throttle while still bounding the entry delay -- past that the candidate is
# rejected rather than entered blind (see HOLDER_GATE_FAIL_CLOSED).
HOLDER_GATE_TIMEOUT_S = 12.0

# 20/08, explicit operator instruction ("ou rejette par securite"): if the
# holder concentration cannot be established in time, the order is NOT sent.
# Fail-closed, the same doctrine as every other guardrail in this project.
# The real cost of this choice is stated plainly rather than hidden: a RugCheck
# outage stops this pocket entering ENTIRELY (it does not degrade to entering
# unchecked). That is the intended trade -- an unchecked entry on this pocket
# has a measured 0.8% winrate in the >=97% band, so entering blind is strictly
# worse than not entering. `blocked_holder_gate_unavailable` in the cycle stats
# is what makes such an outage visible instead of silent.
HOLDER_GATE_FAIL_CLOSED = True


async def _holder_concentration_gate(mint: str) -> tuple[str, float | None] | None:
    """Pre-trade check. Returns ``None`` when the token is CLEARED to enter,
    or a ``(reason, pct)`` tuple when the order must not be sent.

    Never raises -- any failure resolves through ``HOLDER_GATE_FAIL_CLOSED``
    rather than propagating into the tracking loop."""
    try:
        report = await asyncio.wait_for(rugcheck.get_token_report(mint), timeout=HOLDER_GATE_TIMEOUT_S)
    except asyncio.TimeoutError:
        return ("blocked_holder_gate_unavailable: timeout", None) if HOLDER_GATE_FAIL_CLOSED else None
    except Exception as exc:  # noqa: BLE001 -- a provider error must never reach the loop
        logger.info("solana_fresh_launch_ws_exit_shadow: holder gate lookup failed for %s (%s)", mint, exc)
        return ("blocked_holder_gate_unavailable: error", None) if HOLDER_GATE_FAIL_CLOSED else None

    if not report.available or report.top_holder_pct is None:
        # An unenriched answer is NOT evidence the token is clean -- treated
        # exactly like an outage, never as an implicit pass.
        return ("blocked_holder_gate_unavailable: no data", None) if HOLDER_GATE_FAIL_CLOSED else None

    if report.top_holder_pct >= HOLDER_CONCENTRATION_REJECT_PCT:
        return (f"blocked_holder_concentration: top_holder={report.top_holder_pct:.1f}%", report.top_holder_pct)
    return None


async def _track_candidate_pumpportal(
    event: PumpPortalNewTokenEvent,
    *,
    chain: str = "solana",
    ws_feed=None,
    bonding_ws_feed=None,
    holder_gate_fn=_holder_concentration_gate,
    stats: dict | None = None,
    min_liquidity_usd: float = MIN_LIQUIDITY_USD,
    max_liquidity_usd_entry: float = MAX_LIQUIDITY_USD_ENTRY,
    max_pool_age_minutes: float = MAX_POOL_AGE_MINUTES,
    market_cap_sol_at_creation_reject_min: float | None = None,
    market_cap_sol_at_creation_reject_max: float | None = None,
    poll_interval_seconds: float | None = None,
    resolve_fn=None,
    sleep_fn=asyncio.sleep,
    time_fn=time.time,
) -> dict | None:
    """Polls a single candidate's liquidity until confirmed or abandoned --
    structurally identical to ``solana_fresh_launch_fast_discovery_shadow.
    _track_candidate`` (same ``resolve_fn`` default, same age-ceiling
    abandonment), returning a dict shaped for THIS pocket's own schema
    (no ``age_at_entry_seconds``/``market_cap_sol_at_creation`` columns
    here -- those are FAST-DISCOVERY-only fields, never stored on this
    pocket's rows). The 20/08 market-cap-at-creation reject band IS applied
    here even so -- it reads straight off the incoming ``event``, no stored
    history needed, since the dead zone is a source-side token-creation
    property shared by both pockets (same PumpPortal feed), not something
    specific to how this pocket then tracks the exit.

    ``resolve_fn``/``poll_interval_seconds`` default to FAST-DISCOVERY's own
    values via a LOCAL import (never at module load) -- that module imports
    THIS one (``evaluate_exit``/``TRAILING_STOP_PCT``), so a top-level
    import back here would be circular."""
    from aria_core.solana_fresh_launch_fast_discovery_shadow import (
        FAST_DISCOVERY_POLL_INTERVAL_SECONDS,
        MARKET_CAP_SOL_AT_CREATION_REJECT_MAX,
        MARKET_CAP_SOL_AT_CREATION_REJECT_MIN,
        _resolve_liquidity_snapshot,
    )

    resolve_fn = resolve_fn or _resolve_liquidity_snapshot
    poll_interval_seconds = poll_interval_seconds if poll_interval_seconds is not None else FAST_DISCOVERY_POLL_INTERVAL_SECONDS
    market_cap_sol_at_creation_reject_min = (
        market_cap_sol_at_creation_reject_min
        if market_cap_sol_at_creation_reject_min is not None
        else MARKET_CAP_SOL_AT_CREATION_REJECT_MIN
    )
    market_cap_sol_at_creation_reject_max = (
        market_cap_sol_at_creation_reject_max
        if market_cap_sol_at_creation_reject_max is not None
        else MARKET_CAP_SOL_AT_CREATION_REJECT_MAX
    )
    pool_address = event.bonding_curve_key
    if not pool_address:
        return None

    if (
        event.market_cap_sol is not None
        and market_cap_sol_at_creation_reject_min <= event.market_cap_sol < market_cap_sol_at_creation_reject_max
    ):
        # 20/08 -- same dead zone found on FAST-DISCOVERY's own calibration
        # data (MARKET_CAP_SOL_AT_CREATION_REJECT_MIN/MAX's docstring): both
        # pockets consume the same PumpPortalNewTokenEvent source, and the
        # dead zone is a property of the token at creation, independent of
        # which pocket then tracks the exit -- no WS-EXIT-specific history
        # is needed to justify reusing FAST-DISCOVERY's calibrated band here.
        # Rejected before add_pools() so a doomed candidate never costs a
        # websocket subscription either.
        return None

    if bonding_ws_feed is not None:
        try:
            await bonding_ws_feed.add_pools([(pool_address, event.mint)])
        except Exception as exc:  # noqa: BLE001 -- feed subscription is an enhancement, never a hard requirement
            logger.info(
                "solana_fresh_launch_ws_exit_shadow: bonding_ws_feed.add_pools failed for %s (%s)",
                pool_address, exc,
            )

    def _shed_subscription() -> None:
        # 20/08, real incident: add_pools() above subscribed this pool
        # unconditionally, but every abandonment path here used to return
        # without ever calling remove_pools() -- most PumpPortal candidates
        # never confirm liquidity (that's the filter's whole point), so
        # nearly every add_pools() call leaked a permanent subscription. At
        # ~40 events/min this silently exceeded the Solana public RPC's real
        # accountSubscribe ceiling (1000/connection, confirmed live via
        # "-32006 Too many subscriptions") within ~40 minutes of runtime,
        # taking checked_via_websocket to 0 for every pocket sharing this feed.
        for feed in (bonding_ws_feed, ws_feed):
            remove_fn = getattr(feed, "remove_pools", None)
            if remove_fn is not None:
                remove_fn([pool_address])

    while True:
        now = time_fn()
        age_seconds = now - event.detected_at
        if age_seconds >= max_pool_age_minutes * 60.0:
            _shed_subscription()
            return None

        price_usd, reserve_usd, pool_created_at, source = await resolve_fn(
            pool_address, chain=chain, ws_feed=ws_feed, bonding_ws_feed=bonding_ws_feed,
        )

        if price_usd is not None and reserve_usd is not None and reserve_usd >= max_liquidity_usd_entry:
            # 19/08 -- see MAX_LIQUIDITY_USD_ENTRY's own docstring: abandon
            # immediately rather than keep polling, since liquidity climbing
            # further while we wait only makes this candidate worse, never
            # better.
            _shed_subscription()
            return None

        if price_usd is not None and reserve_usd is not None and reserve_usd >= min_liquidity_usd:
            # 20/08 -- PRE-TRADE holder-concentration gate (operator-directed).
            # The post-entry reject ported here earlier the same day was
            # measured live and does NOT protect: of the first 4 real entries
            # at top_holder>=92%, zero were closed by it (2 exited on
            # trailing_stop after ~36s, 2 were still open past 33s) -- the
            # position has already paid entry fees and taken the loss by the
            # time RugCheck's async backfill lands. Blocking at the source is
            # the only thing that actually saves the trade.
            #
            # The old comment claiming an entry gate would reintroduce a
            # "multi-minute RugCheck wait" was measured and found WRONG: it
            # assumed filtering the ~40 events/min of the raw PumpPortal feed.
            # Only candidates that already cleared the liquidity filter ever
            # reach this line -- 1.2/min on this pocket, 2.63/min across both
            # pockets sharing rugcheck's throttle, against a 13/min budget
            # (_MIN_INTERVAL_S=4.5). Real measured latency is 0.17s once the
            # shared throttle is not the binding constraint. 5x of headroom.
            #
            # A free proxy was tried first and REJECTED on real data rather
            # than on principle: `vTokensInBondingCurve` from the creation
            # payload (zero latency, zero quota) diverged from RugCheck on
            # 4 of 14 live tokens and returned impossible values (105-107%,
            # since the field counts VIRTUAL tokens, not a supply fraction).
            gate = await holder_gate_fn(event.mint)
            if gate is not None:
                reason, _pct = gate
                if stats is not None:
                    # Counted under its own key, split on the ":" prefix, so a
                    # RugCheck outage (blocked_holder_gate_unavailable) never
                    # hides inside the normal reject count -- fail-closed must
                    # stay VISIBLE, otherwise a silent provider outage reads as
                    # "the filter is working well" while the pocket is simply
                    # not trading at all.
                    key = reason.split(":", 1)[0]
                    stats[key] = stats.get(key, 0) + 1
                logger.info(
                    "solana_fresh_launch_ws_exit_shadow: entry BLOCKED for %s (%s)",
                    pool_address, reason,
                )
                _shed_subscription()
                return None

            realistic_entry_price = _apply_price_impact_and_fee(
                price_usd, trade_size_usd=SIMULATED_TRADE_SIZE_USD, reserve_usd=reserve_usd, side="buy",
            )
            return {
                "pool_address": pool_address,
                "token_address": event.mint,
                "chain": chain,
                "symbol": event.symbol,
                "detected_at": datetime.now(timezone.utc).isoformat(),
                "entry_price": price_usd,
                "reserve_usd": reserve_usd,
                "pool_created_at": pool_created_at.isoformat() if pool_created_at is not None else None,
                "peak_price": price_usd,
                "realistic_entry_price": realistic_entry_price,
                "dex_id": source,
                "market_cap_sol_at_creation": event.market_cap_sol,
            }

        await sleep_fn(poll_interval_seconds)


async def _insert_confirmed_row_pumpportal(row: dict) -> int:
    await _ensure_table()
    async with aiosqlite.connect(_db_path()) as db:
        cur = await db.execute(
            f"""
            INSERT INTO {TABLE} (
                pool_address, token_address, chain, symbol, detected_at, entry_price,
                reserve_usd, pool_created_at, remaining_qty, realized_proceeds, peak_price,
                realistic_entry_price, dex_id, market_cap_sol_at_creation
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, 1.0, 0.0, ?, ?, ?, ?
            )
            """,
            (
                row["pool_address"], row["token_address"], row["chain"], row["symbol"], row["detected_at"],
                row["entry_price"], row["reserve_usd"], row["pool_created_at"], row["peak_price"],
                row["realistic_entry_price"], row["dex_id"], row.get("market_cap_sol_at_creation"),
            ),
        )
        new_id = cur.lastrowid
        await db.commit()
    return new_id


async def _enrich_with_rugcheck_pumpportal(
    row_id: int, mint: str, *, bonding_ws_feed=None, ws_feed=None,
) -> None:
    """Fire-and-forget rugcheck backfill, mirroring FAST-DISCOVERY's own
    ``_enrich_with_rugcheck`` -- INCLUDING its holder-concentration early
    exit as of 20/08. That filter used to be the one variable this pocket
    deliberately did not apply, so the A/B stayed meaningful; the A/B has
    since returned a settled verdict on this pocket's own real closures, see
    ``HOLDER_CONCENTRATION_REJECT_PCT``'s own docstring for the numbers."""
    try:
        report = await rugcheck.get_token_report(mint)
    except Exception as exc:  # noqa: BLE001 -- enrichment must never propagate
        logger.info("solana_fresh_launch_ws_exit_shadow: rugcheck lookup failed for %s (%s)", mint, exc)
        return
    if not report.available:
        return
    try:
        async with aiosqlite.connect(_db_path()) as db:
            await db.execute(
                f"""
                UPDATE {TABLE} SET rugcheck_score = ?, rugcheck_risks = ?,
                    rugcheck_top_holder_pct = ?, rugcheck_creator = ?
                WHERE id = ?
                """,
                (
                    report.score_normalised,
                    ",".join(report.risks) if report.risks else None,
                    report.top_holder_pct,
                    report.creator,
                    row_id,
                ),
            )
            await db.commit()
    except Exception as exc:  # noqa: BLE001 -- enrichment must never propagate
        logger.info("solana_fresh_launch_ws_exit_shadow: rugcheck backfill write failed for row %s (%s)", row_id, exc)
        return

    if report.top_holder_pct is not None and report.top_holder_pct >= HOLDER_CONCENTRATION_REJECT_PCT:
        await _reject_on_holder_concentration(row_id, bonding_ws_feed=bonding_ws_feed, ws_feed=ws_feed)


async def _reject_on_holder_concentration(row_id: int, *, bonding_ws_feed=None, ws_feed=None) -> None:
    """Closes a still-open row the moment RugCheck's backfill (see
    ``_enrich_with_rugcheck_pumpportal``) reveals ``top_holder_pct`` at/above
    ``HOLDER_CONCENTRATION_REJECT_PCT`` -- see that constant's own docstring
    for this pocket's own real win-rate evidence. ``WHERE exit_reason IS
    NULL`` makes this safe against a race with the normal exit-tracking loop
    closing the same row concurrently (trailing_stop/max_hold/
    liquidity_collapse) -- whichever writes first wins, never a double-close.

    Structurally a mirror of FAST-DISCOVERY's function of the same name; the
    ~60 duplicated lines are a known, tracked debt (backlog #329, shadow
    module unification) and deliberately NOT refactored here -- that refactor
    needs its own operator go, and this pocket was actively bleeding."""
    try:
        async with aiosqlite.connect(_db_path()) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(f"SELECT * FROM {TABLE} WHERE id = ? AND exit_reason IS NULL", (row_id,))
            row = await cur.fetchone()
            if row is None:
                return
            row = dict(row)

        entry_price = row["entry_price"]
        if not entry_price:
            return
        try:
            snapshot = await _snapshot_with_fallback(
                geckoterminal_client, row["pool_address"], row["token_address"], chain=row["chain"],
            )
        except Exception as exc:  # noqa: BLE001 -- best-effort close, never raises
            logger.info(
                "solana_fresh_launch_ws_exit_shadow: holder-concentration close snapshot failed for %s (%s)",
                row["pool_address"], exc,
            )
            return
        if not snapshot.available or snapshot.price_usd is None:
            return
        current_price = snapshot.price_usd
        remaining_qty = row["remaining_qty"] if row["remaining_qty"] is not None else 1.0
        realized_proceeds = (row["realized_proceeds"] or 0.0) + remaining_qty * current_price

        realistic_entry_price = row.get("realistic_entry_price")
        realistic_realized_proceeds = row.get("realistic_realized_proceeds") or 0.0
        realistic_final_multiplier = None
        if realistic_entry_price:
            impacted = _apply_price_impact_and_fee(
                current_price, trade_size_usd=remaining_qty * SIMULATED_TRADE_SIZE_USD,
                reserve_usd=snapshot.reserve_usd, side="sell",
            )
            if impacted is not None:
                realistic_realized_proceeds += remaining_qty * impacted
                realistic_final_multiplier = realistic_realized_proceeds / realistic_entry_price

        final_multiplier = realized_proceeds / entry_price

        async with aiosqlite.connect(_db_path()) as db:
            await db.execute(
                f"""
                UPDATE {TABLE} SET
                    remaining_qty = 0.0, realized_proceeds = ?, exit_reason = 'holder_concentration_reject',
                    final_multiplier = ?, last_checked_at = ?, last_price = ?,
                    realistic_realized_proceeds = ?, realistic_final_multiplier = ?, last_reserve_usd = ?,
                    exit_price_source = ?
                WHERE id = ? AND exit_reason IS NULL
                """,
                (
                    realized_proceeds, final_multiplier, datetime.now(timezone.utc).isoformat(),
                    current_price, realistic_realized_proceeds, realistic_final_multiplier,
                    snapshot.reserve_usd, snapshot.dex_id, row_id,
                ),
            )
            await db.commit()
        for feed in (bonding_ws_feed, ws_feed):
            remove_fn = getattr(feed, "remove_pools", None)
            if remove_fn is not None:
                remove_fn([row["pool_address"]])
        logger.info(
            "solana_fresh_launch_ws_exit_shadow: closed %s on holder_concentration_reject "
            "(top_holder_pct>=%.0f)", row["pool_address"], HOLDER_CONCENTRATION_REJECT_PCT,
        )
    except Exception as exc:  # noqa: BLE001 -- best-effort close, never raises into the enrichment task
        logger.info(
            "solana_fresh_launch_ws_exit_shadow: _reject_on_holder_concentration failed for row %s (%s)",
            row_id, exc,
        )


async def _track_and_maybe_insert_pumpportal(
    event: PumpPortalNewTokenEvent, *, chain: str, ws_feed, bonding_ws_feed=None,
    semaphore: asyncio.Semaphore, stats: dict, in_flight: set[str] | None = None,
) -> None:
    """20/08 -- ``in_flight`` dedup checked BEFORE the semaphore (not after):
    a candidate PumpPortal keeps re-broadcasting must never compete for a
    concurrency slot at all, it should be rejected immediately. See
    ``run_forever_pumpportal``'s own comment for the real incident this
    closes (mirrors FAST-DISCOVERY's own fix, same root cause)."""
    key = event.bonding_curve_key
    if in_flight is not None and key:
        if key in in_flight:
            stats["deduped_in_flight"] = stats.get("deduped_in_flight", 0) + 1
            return
        in_flight.add(key)
    try:
        async with semaphore:
            try:
                await _ensure_table()
                if event.bonding_curve_key:
                    async with aiosqlite.connect(_db_path()) as db:
                        if await _has_open_or_recent_signal(db, event.bonding_curve_key, chain):
                            stats["deduped"] = stats.get("deduped", 0) + 1
                            return
                row = await _track_candidate_pumpportal(
                    event, chain=chain, ws_feed=ws_feed, bonding_ws_feed=bonding_ws_feed, stats=stats,
                )
                if row is None:
                    stats["abandoned"] = stats.get("abandoned", 0) + 1
                    return
                new_id = await _insert_confirmed_row_pumpportal(row)
                stats["confirmed"] = stats.get("confirmed", 0) + 1
                asyncio.create_task(_enrich_with_rugcheck_pumpportal(
                    new_id, event.mint, bonding_ws_feed=bonding_ws_feed, ws_feed=ws_feed,
                ))
            except Exception as exc:  # noqa: BLE001 -- one candidate's failure must never break the loop
                logger.info("solana_fresh_launch_ws_exit_shadow: _track_and_maybe_insert_pumpportal failed (%s)", exc)
                stats["errors"] = stats.get("errors", 0) + 1
    finally:
        if in_flight is not None and key:
            in_flight.discard(key)


async def run_forever_pumpportal(
    feed: PumpPortalNewTokenFeed | None = None,
    *,
    chain: str = "solana",
    ws_feed=None,
    bonding_ws_feed=None,
    max_concurrent: int | None = None,
    max_events: int | None = None,
    stop_event: asyncio.Event | None = None,
) -> dict[str, int]:
    """Mirrors ``solana_fresh_launch_fast_discovery_shadow.run_forever`` --
    same drain-the-queue/bounded-concurrency shape, own stats dict. Takes
    its OWN ``feed`` instance (a second, independent PumpPortal websocket
    connection) -- ``PumpPortalNewTokenFeed``'s queue has a single consumer
    by design (``asyncio.Queue.get()``), so sharing one feed instance with
    FAST-DISCOVERY would silently split the token stream between the two
    pockets instead of both seeing the same entry universe.

    ``max_concurrent`` defaults to FAST-DISCOVERY's own constant via a LOCAL
    import -- see ``_track_candidate_pumpportal``'s own docstring for why
    (circular import at module load otherwise)."""
    if max_concurrent is None:
        from aria_core.solana_fresh_launch_fast_discovery_shadow import MAX_CONCURRENT_TRACKED_CANDIDATES

        max_concurrent = MAX_CONCURRENT_TRACKED_CANDIDATES
    feed = feed or PumpPortalNewTokenFeed()
    await feed.start()
    await _ensure_table()

    semaphore = asyncio.Semaphore(max_concurrent)
    stats: dict[str, int] = {}
    tasks: list[asyncio.Task] = []
    events_seen = 0
    # 20/08 -- see _track_and_maybe_insert_pumpportal's own docstring: a
    # candidate that never confirms has no DB row, so the DB-only dedup
    # below can never see it if PumpPortal re-broadcasts the same key.
    # Real incident: 594 wasted DexPaprika 404s against one address over
    # 2h+ (confirmed via a direct Helius getAccountInfo call: the address
    # is a plain System-Program-owned wallet, not a real bonding curve).
    in_flight: set[str] = set()

    try:
        while True:
            if stop_event is not None and stop_event.is_set():
                break
            if max_events is not None and events_seen >= max_events:
                break
            event = await feed.next_event(timeout=1.0)
            if event is None:
                continue
            events_seen += 1
            task = asyncio.create_task(
                _track_and_maybe_insert_pumpportal(
                    event, chain=chain, ws_feed=ws_feed, bonding_ws_feed=bonding_ws_feed,
                    semaphore=semaphore, stats=stats, in_flight=in_flight,
                )
            )
            tasks.append(task)
            tasks = [t for t in tasks if not t.done()]
    finally:
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    return stats


async def _stamp_last_checked_only(row_id: int) -> None:
    """Same starvation-bug fix as the original module -- marks a row as
    attempted this cycle even when no price could be obtained at all
    (neither websocket nor REST), so it drops to the back of the
    round-robin queue instead of blocking every younger row forever."""
    try:
        async with aiosqlite.connect(_db_path()) as db:
            await db.execute(
                f"UPDATE {TABLE} SET last_checked_at = ? WHERE id = ?",
                (datetime.now(timezone.utc).isoformat(), row_id),
            )
            await db.commit()
    except Exception as exc:  # noqa: BLE001 -- best-effort, never blocks the batch
        logger.info(
            "solana_fresh_launch_ws_exit_shadow: _stamp_last_checked_only failed for id=%s (%s)", row_id, exc,
        )


def evaluate_exit(
    row: dict, *,
    current_price: float,
    reserve_usd: float | None,
    dex_id: str | None,
    age_minutes: float,
    window_high: float | None = None,
    window_low: float | None = None,
) -> dict:
    """Pure exit-check: given a row and an ALREADY-KNOWN price/reserve/dex_id
    (from either a websocket push or a REST polling snapshot -- this
    function never touches the network or the DB itself), returns the next
    state to persist. This is what lets ``advance_exit_simulation`` call the
    exact same rule regardless of which mechanism supplied the price.

    ``{"skipped": True}`` means the corrupted-upstream-price sanity guard
    rejected this reading -- the caller must still stamp ``last_checked_at``
    (this function doesn't do it) so the queue advances.

    No ladder: ``remaining_qty`` only ever ends at 1.0 (still open) or 0.0
    (closed in one shot) -- priority order identical to the original module:
    liquidity_collapse > trailing_stop > max_hold."""
    entry_price = row["entry_price"]

    effective_high = max(window_high if window_high is not None else current_price, current_price)
    effective_low = min(window_low if window_low is not None else current_price, current_price)

    sanity_reference = row.get("support_range_high") or entry_price
    if sanity_reference and effective_high > sanity_reference * PEAK_PRICE_SANITY_MULTIPLE:
        logger.info(
            "solana_fresh_launch_ws_exit_shadow: implausible price for %s "
            "(effective_high=%.10g, sanity_reference=%.10g) -- skipping this cycle",
            row["pool_address"], effective_high, sanity_reference,
        )
        return {"skipped": True}

    peak_price = row.get("peak_price") or entry_price
    peak_price = max(peak_price, effective_high)

    remaining_qty = row["remaining_qty"] if row.get("remaining_qty") is not None else 1.0
    realized_proceeds = row.get("realized_proceeds") or 0.0
    realistic_entry_price = row.get("realistic_entry_price")
    realistic_realized_proceeds = row.get("realistic_realized_proceeds") or 0.0
    realistic_unreachable = realistic_entry_price is None

    def _realistic_sell(qty_fraction: float, ideal_price: float) -> None:
        nonlocal realistic_realized_proceeds, realistic_unreachable
        if realistic_unreachable:
            return
        impacted = _apply_price_impact_and_fee(
            ideal_price, trade_size_usd=qty_fraction * SIMULATED_TRADE_SIZE_USD,
            reserve_usd=reserve_usd, side="sell",
        )
        if impacted is None:
            realistic_unreachable = True
            return
        realistic_realized_proceeds += qty_fraction * impacted

    is_pumpswap = dex_id == "pumpswap"
    entry_reserve = row.get("reserve_usd")
    liquidity_collapsed = (
        not is_pumpswap
        and entry_reserve is not None and entry_reserve > 0
        and reserve_usd is not None
        and reserve_usd < entry_reserve * (1 - LIQUIDITY_COLLAPSE_EXIT_PCT / 100.0)
    )

    exit_reason: str | None = None
    if liquidity_collapsed:
        _realistic_sell(remaining_qty, current_price)
        realized_proceeds += remaining_qty * current_price
        remaining_qty = 0.0
        exit_reason = "liquidity_collapse"
    elif effective_low <= peak_price * (1 - TRAILING_STOP_PCT / 100.0):
        stop_price = peak_price * (1 - TRAILING_STOP_PCT / 100.0)
        _realistic_sell(remaining_qty, stop_price)
        realized_proceeds += remaining_qty * stop_price
        remaining_qty = 0.0
        exit_reason = "trailing_stop"
    elif age_minutes >= MAX_HOLD_MINUTES:
        _realistic_sell(remaining_qty, current_price)
        realized_proceeds += remaining_qty * current_price
        remaining_qty = 0.0
        exit_reason = "max_hold"

    final_multiplier = (realized_proceeds / entry_price) if exit_reason and entry_price else None
    realistic_final_multiplier = (
        realistic_realized_proceeds / realistic_entry_price
        if exit_reason and not realistic_unreachable and realistic_entry_price
        else None
    )

    return {
        "skipped": False,
        "peak_price": peak_price,
        "remaining_qty": remaining_qty,
        "realized_proceeds": realized_proceeds,
        "exit_reason": exit_reason,
        "final_multiplier": final_multiplier,
        "realistic_realized_proceeds": realistic_realized_proceeds,
        "realistic_final_multiplier": realistic_final_multiplier,
        "last_price": current_price,
        "last_reserve_usd": reserve_usd,
    }


async def _persist_exit_result(row_id: int, result: dict, price_source: str) -> None:
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            f"""
            UPDATE {TABLE} SET
                peak_price = ?, remaining_qty = ?, realized_proceeds = ?,
                exit_reason = ?, final_multiplier = ?, last_checked_at = ?, last_price = ?,
                realistic_realized_proceeds = ?, realistic_final_multiplier = ?, last_reserve_usd = ?,
                trailing_stop_pct_used = ?, last_price_source = ?,
                exit_price_source = CASE WHEN ? IS NOT NULL THEN ? ELSE exit_price_source END
            WHERE id = ?
            """,
            (
                result["peak_price"], result["remaining_qty"], result["realized_proceeds"],
                result["exit_reason"], result["final_multiplier"], datetime.now(timezone.utc).isoformat(),
                result["last_price"], result["realistic_realized_proceeds"], result["realistic_final_multiplier"],
                result["last_reserve_usd"], TRAILING_STOP_PCT, price_source,
                result["exit_reason"], price_source,
                row_id,
            ),
        )
        await db.commit()


async def advance_exit_simulation(
    client: GeckoTerminalClient | None = None, *, chain: str = "solana", limit: int = 30,
    ws_feed=None, max_rest_calls: int | None = None,
) -> dict[str, int]:
    """Real calibrated exit rule (trailing stop + liquidity_collapse +
    max_hold, no ladder), sourced first from ``ws_feed`` (a
    ``PumpSwapWebSocketFeed``-shaped object exposing ``get_snapshot
    (pool_address) -> object with .available/.price_usd/.reserve_usd/
    .dex_id``) when given and fresh, falling back automatically to the
    EXACT SAME REST cascade the original module uses
    (``_snapshot_with_fallback``) for any pool the feed can't currently
    price -- non-PumpSwap pools, non-WSOL-quoted pools, or simply a feed
    that hasn't started/has disconnected. ``ws_feed=None`` (the default)
    makes this module behave as pure REST polling, useful for testing or a
    degraded-mode run.

    ``max_rest_calls`` (19/08, real regression fix): ``limit`` alone used to
    double as the REST-call budget too -- raising ``limit`` to clear a real
    backlog (80 open positions, most never checked once) made a single
    cycle take 4+ minutes, because the round-robin queue
    (``COALESCE(last_checked_at, detected_at) ASC``) checks the OLDEST rows
    first, and those are structurally the ones neither websocket feed can
    price (already migrated to a non-WSOL-quoted pool) -- so a big ``limit``
    meant a big pile of serialized REST calls, all queued behind
    GeckoTerminal's own shared throttle. Now ``limit`` bounds how many rows
    are considered per cycle (cheap -- a websocket hit costs nothing), while
    ``max_rest_calls`` separately bounds real REST calls (``None`` = no cap,
    same as before). A row skipped for budget reasons is NEVER stamped
    (``_stamp_last_checked_only`` untouched), so it stays at the front of
    the queue for the next cycle instead of being pushed behind rows that
    got lucky with the websocket.

    Same round-robin queue fix as the original module
    (``COALESCE(last_checked_at, detected_at)``), ported from day one."""
    client = client or geckoterminal_client
    counts = {
        "checked": 0, "checked_via_websocket": 0, "checked_via_polling": 0,
        "closed_trailing_stop": 0, "closed_max_hold": 0, "closed_liquidity_collapse": 0,
    }
    rest_calls_used = 0
    try:
        await _ensure_table()
        async with aiosqlite.connect(_db_path()) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                f"SELECT * FROM {TABLE} WHERE chain = ? AND exit_reason IS NULL "
                f"ORDER BY COALESCE(last_checked_at, detected_at) ASC LIMIT ?",
                (chain, limit),
            )
            rows = [dict(r) for r in await cur.fetchall()]

        for row in rows:
            age_minutes = _minutes_since(row["detected_at"])
            if age_minutes is None:
                continue
            entry_price = row["entry_price"]
            if not entry_price:
                continue

            current_price: float | None = None
            reserve_usd: float | None = None
            dex_id: str | None = None
            window_high: float | None = None
            window_low: float | None = None
            price_source: str | None = None

            if ws_feed is not None:
                try:
                    live = ws_feed.get_snapshot(row["pool_address"])
                except Exception as exc:  # noqa: BLE001 -- the feed must never break the batch
                    logger.info(
                        "solana_fresh_launch_ws_exit_shadow: ws_feed.get_snapshot failed for %s (%s)",
                        row["pool_address"], exc,
                    )
                    live = None
                if live is not None and getattr(live, "available", False) and live.price_usd:
                    current_price = live.price_usd
                    reserve_usd = live.reserve_usd
                    dex_id = live.dex_id
                    price_source = "websocket"
                    counts["checked_via_websocket"] += 1
                    # 19/08 -- the feed tracks high/low across every
                    # notification since our last read (see
                    # PumpFunBondingLiveSnapshot/PumpSwapLiveSnapshot's own
                    # docstrings) -- same "spike between checks" coverage a
                    # REST OHLCV call would give, at zero extra network cost.
                    window_high = getattr(live, "price_high_since_last_read", None)
                    window_low = getattr(live, "price_low_since_last_read", None)

                    # 20/08 -- unlike the REST branch below, a websocket-priced
                    # check used to archive NOTHING to shadow_snapshot_archive,
                    # even though this is a pure local SQLite write (zero extra
                    # network cost, unlike the 19/08-removed candle archiving
                    # this module's own history note above describes) and
                    # websocket checks are the large majority of this pocket's
                    # real traffic. Real gap found investigating why
                    # `liquidity_collapse` exits get caught so late (up to
                    # ~100% reserve already gone) -- without a per-check
                    # reserve path we can't tell "checked too rarely" from
                    # "this pool collapsed in a single tick, nothing could
                    # have caught it earlier". This starts closing that gap
                    # going forward (can't backfill the past).
                    from aria_core import shadow_snapshot_archive

                    await shadow_snapshot_archive.store_snapshot(
                        module="solana_fresh_launch_ws_exit", position_id=row["id"],
                        pool_address=row["pool_address"], chain=chain,
                        price_usd=current_price, reserve_usd=reserve_usd,
                        dex_id=dex_id, price_change_pct=None,
                        transactions=None, volume_usd=None,
                    )

            if current_price is None:
                if max_rest_calls is not None and rest_calls_used >= max_rest_calls:
                    # Budget exhausted this cycle -- never stamped, stays at
                    # the front of the queue for the next cycle rather than
                    # being pushed behind rows the websocket already served.
                    continue
                rest_calls_used += 1
                try:
                    snapshot: PoolSnapshot = await _snapshot_with_fallback(
                        client, row["pool_address"], row["token_address"], chain=chain,
                    )
                except Exception as exc:  # noqa: BLE001 -- one pool's failure never blocks the batch
                    logger.info(
                        "solana_fresh_launch_ws_exit_shadow: snapshot failed for %s (%s)",
                        row["pool_address"], exc,
                    )
                    await _stamp_last_checked_only(row["id"])
                    continue
                if not snapshot.available or snapshot.price_usd is None:
                    await _stamp_last_checked_only(row["id"])
                    continue
                current_price = snapshot.price_usd
                reserve_usd = snapshot.reserve_usd
                dex_id = snapshot.dex_id
                price_source = "polling"
                counts["checked_via_polling"] += 1

                from aria_core import shadow_snapshot_archive

                await shadow_snapshot_archive.store_snapshot(
                    module="solana_fresh_launch_ws_exit", position_id=row["id"],
                    pool_address=row["pool_address"], chain=chain,
                    price_usd=snapshot.price_usd, reserve_usd=snapshot.reserve_usd,
                    dex_id=snapshot.dex_id, price_change_pct=snapshot.price_change_pct,
                    transactions=snapshot.transactions, volume_usd=snapshot.volume_usd,
                )

            # 19/08, real removal (operator call): the REST OHLCV call that
            # used to run here (fetch candles, compute window_high/low,
            # archive to shadow_candle_archive) was cut entirely -- at this
            # pocket's real transaction volume, archiving candles stopped
            # being worth its own network cost (a second GeckoTerminal call
            # per REST-priced row, serialized behind the SAME shared
            # throttle that was already the real bottleneck this session).
            # window_high/low now stay None for a REST-priced row exactly
            # like they already did for a websocket-priced one --
            # evaluate_exit() falls back to the point-sample price itself in
            # both cases.

            counts["checked"] += 1
            result = evaluate_exit(
                row, current_price=current_price, reserve_usd=reserve_usd, dex_id=dex_id,
                age_minutes=age_minutes, window_high=window_high, window_low=window_low,
            )
            if result["skipped"]:
                await _stamp_last_checked_only(row["id"])
                continue

            await _persist_exit_result(row["id"], result, price_source)

            if result["exit_reason"] is not None and ws_feed is not None:
                # 19/08 -- sheds this pool's websocket subscription(s) the
                # MOMENT a position closes -- see FAST-DISCOVERY sibling's
                # own comment at the same call site for the real incident
                # this fixes (216 stale pools accumulated on one connection,
                # correlated with recurring reconnects).
                remove_fn = getattr(ws_feed, "remove_pools", None)
                if remove_fn is not None:
                    remove_fn([row["pool_address"]])

            if result["exit_reason"] == "trailing_stop":
                counts["closed_trailing_stop"] += 1
            elif result["exit_reason"] == "max_hold":
                counts["closed_max_hold"] += 1
            elif result["exit_reason"] == "liquidity_collapse":
                counts["closed_liquidity_collapse"] += 1
    except Exception as exc:  # noqa: BLE001 -- shadow simulation must never raise into a caller
        logger.info("solana_fresh_launch_ws_exit_shadow: advance_exit_simulation failed (%s)", exc)
    return counts


async def chain_pnl_summary_realistic(chain: str = "solana") -> dict:
    """Same shape/doctrine as ``solana_fresh_launch_shadow.
    chain_pnl_summary_realistic`` (unreachable_liquidity/stranded/
    outlier_excluded never silently dropped) -- own implementation since it
    queries this module's own ``TABLE``."""
    await _ensure_table()
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            f"SELECT realistic_entry_price, remaining_qty, realistic_realized_proceeds, "
            f"realistic_final_multiplier, last_price, exit_reason, support_range_high, entry_price "
            f"FROM {TABLE} WHERE chain = ?",
            (chain,),
        )
        rows = [dict(r) for r in await cur.fetchall()]

    total_pnl_units = 0.0
    closed = 0
    open_valued = 0
    pending_price = 0
    unreachable_liquidity = 0
    stranded = 0
    outlier_excluded = 0
    for r in rows:
        entry = r["realistic_entry_price"]
        if not entry:
            unreachable_liquidity += 1
            continue
        sanity_reference = r.get("support_range_high") or r.get("entry_price")
        if r["exit_reason"] is not None:
            if r["realistic_final_multiplier"] is not None:
                implied_exit_price = r["realistic_final_multiplier"] * entry
                if sanity_reference and implied_exit_price > sanity_reference * PEAK_PRICE_SANITY_MULTIPLE:
                    outlier_excluded += 1
                    continue
                closed += 1
                total_pnl_units += r["realistic_final_multiplier"] - 1.0
            else:
                stranded += 1
                salvaged = r["realistic_realized_proceeds"] or 0.0
                total_pnl_units += salvaged / entry - 1.0
            continue
        if r["last_price"] is None:
            pending_price += 1
            continue
        if sanity_reference and r["last_price"] > sanity_reference * PEAK_PRICE_SANITY_MULTIPLE:
            outlier_excluded += 1
            continue
        open_valued += 1
        remaining = r["remaining_qty"] if r["remaining_qty"] is not None else 1.0
        realized = r["realistic_realized_proceeds"] or 0.0
        current_value = realized + remaining * r["last_price"]
        total_pnl_units += current_value / entry - 1.0

    positions_funded = closed + stranded + open_valued + pending_price
    capital_deployed_usd = positions_funded * SIMULATED_TRADE_SIZE_USD
    total_pnl_usd = total_pnl_units * SIMULATED_TRADE_SIZE_USD
    return {
        "total_pnl_units": total_pnl_units,
        "total_pnl_usd": total_pnl_usd,
        "capital_deployed_usd": capital_deployed_usd,
        "return_on_deployed_pct": (
            total_pnl_usd / capital_deployed_usd * 100.0 if capital_deployed_usd else 0.0
        ),
        "closed": closed,
        "stranded": stranded,
        "open_valued": open_valued,
        "pending_price": pending_price,
        "unreachable_liquidity": unreachable_liquidity,
        "outlier_excluded": outlier_excluded,
    }


async def summary(*, chain: str = "solana") -> dict:
    await _ensure_table()
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            f"SELECT exit_reason, final_multiplier, exit_price_source FROM {TABLE} "
            "WHERE chain = ? AND final_multiplier IS NOT NULL",
            (chain,),
        )
        rows = [dict(r) for r in await cur.fetchall()]
    wins = sum(1 for r in rows if r["final_multiplier"] > 1.0)
    by_exit_reason: dict[str, int] = {}
    by_price_source: dict[str, int] = {}
    for r in rows:
        by_exit_reason[r["exit_reason"]] = by_exit_reason.get(r["exit_reason"], 0) + 1
        if r["exit_price_source"]:
            by_price_source[r["exit_price_source"]] = by_price_source.get(r["exit_price_source"], 0) + 1
    return {
        "completed": len(rows),
        "wins": wins,
        "win_rate": (wins / len(rows)) if rows else None,
        "avg_multiplier": (sum(r["final_multiplier"] for r in rows) / len(rows)) if rows else None,
        "by_exit_reason": by_exit_reason,
        "by_exit_price_source": by_price_source,
    }
