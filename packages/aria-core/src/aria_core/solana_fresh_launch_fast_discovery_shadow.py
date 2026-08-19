"""Solana "fresh launch" shadow -- FAST-DISCOVERY VARIANT (19/08). A SEPARATE,
PARALLEL A/B counterpart to ``solana_fresh_launch_shadow.py``/
``solana_fresh_launch_ws_exit_shadow.py``, never a replacement -- the
operator wants an objective comparison before any production change. NOT
wired to the heartbeat, NOT gated by any ``ARIA_*`` flag, silently available
only (no orchestration script calls this yet -- see module docstring's own
"how to run this" note near ``run_forever`` below).

**What this module tests**: whether entering within the first 20-60 REAL
seconds of a pump.fun token's life (instead of the 1-5 minutes the 60s
REST-polling sibling achieves) changes outcomes. Real diligence done the
same day (12-minute live measurement, see
``docs/aria-learning-inbox/`` -- not yet promoted, findings summarized here):
DexPaprika's OWN indexing lag (median ~97s from real on-chain creation to
first DexPaprika visibility) is almost entirely what the 60s-REST-poll
sibling is bottlenecked on, not its poll cadence -- so a REST-only
discovery mechanism, however tightly polled, cannot beat this. The fix has
to bypass DexPaprika for DISCOVERY entirely: this module subscribes to a
REAL-TIME token-creation feed (``services/pumpportal_ws.py``, verified live
19/08 -- free, no API key, ~40 events/min, zero parse failures on a real
45s connection) and only falls back to DexPaprika for the thing it's
actually good at once a candidate is already known: reading its LIQUIDITY.

**What is held constant vs. what changes (the whole point of this A/B/C
comparison)**:
  - ENTRY THRESHOLD values: ``MIN_LIQUIDITY_USD``/``MAX_POOL_AGE_MINUTES`` are
    IMPORTED from ``solana_fresh_launch_shadow.py`` (never redefined here),
    exactly like ``solana_fresh_launch_ws_exit_shadow.py`` already does --
    keeps every sibling's entry bar numerically identical, isolating
    DISCOVERY SPEED as the one real variable under test.
  - EXIT mechanism: IMPORTED, never reimplemented. ``evaluate_exit`` (the
    pure liquidity_collapse > trailing_stop > max_hold rule, closed over
    ws-exit's own ``TRAILING_STOP_PCT``/``MAX_HOLD_MINUTES``/
    ``LIQUIDITY_COLLAPSE_EXIT_PCT``) is imported straight from
    ``solana_fresh_launch_ws_exit_shadow.py`` and called as-is -- this module
    never reimplements the decision, nor redefines those three thresholds
    (``TRAILING_STOP_PCT`` is additionally imported here on its own, purely
    to stamp ``trailing_stop_pct_used`` on each row for a future audit).
    ``PEAK_PRICE_SANITY_MULTIPLE`` is imported from
    ``solana_fresh_launch_shadow.py``, the same single source of truth the
    ws-exit sibling already uses. What IS
    necessarily duplicated (same doctrine as the ws-exit sibling's own
    docstring: "necessary duplication since each pocket owns its own
    persistence") is the thin orchestration shell around that shared rule --
    this module's own ``advance_exit_simulation`` fetches a fresh
    price/reserve snapshot each cycle and persists the result into ITS OWN
    table, structurally mirroring the ws-exit sibling's own function almost
    line for line, but never redefining the RULE itself.
  - DISCOVERY mechanism: the actual novelty here. A real-time
    ``PumpPortalNewTokenFeed`` (see ``services/pumpportal_ws.py``) replaces
    DexPaprika's own 60s-polled ``get_trending_pools``/``pools/search``
    entirely for the "is there a new token" question -- DexPaprika is only
    ever consulted afterward, per-candidate, to answer "has ITS liquidity
    crossed the bar yet" (``_fetch_pool_snapshot_rest``, one REST call per
    poll, reusing ``services/dexpaprika.py``'s own module-level throttle/
    circuit-breaker choke point -- never a new, uncoordinated throttle).
    ``services/pumpswap_ws.py`` is tried FIRST for this same liquidity
    question (per the mission's explicit instruction) via an optional
    ``ws_feed`` -- honestly expected to rarely fire for tokens this young
    (a pump.fun bonding-curve token only becomes a PumpSwap AMM pool after
    migration, which essentially never happens within this module's own
    ``MAX_POOL_AGE_MINUTES`` window), but wired for architectural
    consistency and any pool that happens to already be past migration.

**No support-distance columns** (unlike both siblings) -- deliberate
simplification, not an oversight: a candidate confirmed at 20-60s old has
no meaningful 1-minute-candle history to compute a support range from, and
this module's mission is discovery speed, not that informational signal.
``sanity_reference`` in ``evaluate_exit`` therefore always falls back to
``entry_price`` itself (the documented fallback path already built into
that shared function), never a fabricated range.

**Candidate age accounting -- two real proxies, honestly distinguished**:
``first_seen_at`` is this module's OWN wall-clock reading at the moment
PumpPortal's creation event was received (no on-chain timestamp in that
payload, see ``services/pumpportal_ws.py``'s own docstring). Once a
candidate's liquidity is confirmed, this module makes ONE extra REST call
that also carries DexPaprika's own ``created_at`` (real on-chain-adjacent
value, verified live 19/08 to be present on the exact same call already
needed for liquidity) -- ``age_at_entry_seconds`` prefers this REST-
confirmed value when available, falling back to
``entry_confirmed_at - first_seen_at`` only when it isn't (e.g. the
PumpSwap-websocket tier confirmed the candidate without ever making that
REST call). Both raw timestamps (``first_seen_at``/``pool_created_at``) are
kept on the row so a future pass can audit which proxy was actually used,
never silently blended.

Same bright-line doctrine as every other shadow module in this dome: never
opens a real or paper-capital position, never calls
``wallet_guard``/``agent_wallet_pilot``/``paper_trader.open_position``, pure
read+log+simulate. Own dedicated SQLite table
(``solana_fresh_launch_fast_discovery_shadow_log``) -- never shares either
sibling's table (would corrupt independent A/B/C comparability and dedup
logic, same reasoning as the ws-exit sibling's own docstring)."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

import aiosqlite

from aria_core.paths import shadow_db_path
from aria_core.services import dexpaprika, rugcheck
from aria_core.services.geckoterminal import GeckoTerminalClient, OHLCVResult, PoolSnapshot, geckoterminal_client
from aria_core.services.pumpportal_ws import PumpPortalNewTokenEvent, PumpPortalNewTokenFeed
from aria_core.solana_fresh_launch_shadow import (
    MAX_POOL_AGE_MINUTES,
    MIN_LIQUIDITY_USD,
    PEAK_PRICE_SANITY_MULTIPLE,
)
from aria_core.solana_fresh_launch_ws_exit_shadow import (
    TRAILING_STOP_PCT,
    evaluate_exit,
)
from aria_core.solana_pump_shadow import (
    SIMULATED_TRADE_SIZE_USD,
    _apply_price_impact_and_fee,
    _epoch_of,
    _minutes_since,
    _snapshot_with_fallback,
)

logger = logging.getLogger(__name__)

DB_PATH = str(shadow_db_path())

TABLE = "solana_fresh_launch_fast_discovery_shadow_log"

# How often a still-unconfirmed candidate's liquidity is re-checked. Deliberately
# NOT a rate limiter itself -- dexpaprika.py's own module-level throttle/lock
# (_get_json's choke point, shared process-wide) is what actually caps real
# request throughput regardless of how many candidates are tracked
# concurrently; this constant only controls how promptly a single candidate
# is re-polled, matching the cadence already used in the originating live
# probe (pumpfun_creation_gap_probe.py's own DEXPAPRIKA_POLL_INTERVAL_S).
FAST_DISCOVERY_POLL_INTERVAL_SECONDS = 5.0

# Sanity bound on concurrently-tracked candidates -- protects against an
# unbounded task explosion during a real creation burst (measured ~40/min
# live 19/08; MAX_POOL_AGE_MINUTES=5 means, worst case, ~200 concurrent
# trackers if literally none ever confirmed or got abandoned early). NOT a
# throughput control (see FAST_DISCOVERY_POLL_INTERVAL_SECONDS above) --
# dexpaprika's shared throttle already serializes the actual network calls
# regardless of this number; a real creation burst just means each tracked
# candidate gets checked less often, an honest degradation, never a burst
# that risks a 429.
MAX_CONCURRENT_TRACKED_CANDIDATES = 10

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
                name TEXT,
                discovery_source TEXT NOT NULL DEFAULT 'pumpportal',
                first_seen_at TEXT NOT NULL,
                detected_at TEXT NOT NULL,
                pool_created_at TEXT,
                age_at_entry_seconds REAL,
                liquidity_confirmed_via TEXT,
                entry_price REAL NOT NULL,
                reserve_usd REAL,
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
                market_cap_sol_at_creation REAL,
                v_sol_in_bonding_curve_at_creation REAL,
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
        await db.commit()
    _ensured_db_paths.add(path)


async def _has_open_or_recent_signal(db: aiosqlite.Connection, pool_address: str, chain: str) -> bool:
    """Dedup against an already-tracked-or-logged candidate for the same
    pool -- checked before spawning a new tracking task, never after (a
    duplicate tracker for the same pool would double-count in the PnL
    aggregate)."""
    cur = await db.execute(
        f"SELECT 1 FROM {TABLE} WHERE pool_address = ? AND chain = ? LIMIT 1",
        (pool_address, chain),
    )
    return (await cur.fetchone()) is not None


async def closures_so_far() -> int:
    await _ensure_table()
    async with aiosqlite.connect(_db_path()) as db:
        cur = await db.execute(f"SELECT COUNT(*) FROM {TABLE} WHERE exit_reason IS NOT NULL")
        (count,) = await cur.fetchone()
    return count


async def _fetch_pool_snapshot_rest(pool_address: str, chain: str) -> tuple[float | None, float | None, datetime | None]:
    """One REST call to DexPaprika's single-pool detail endpoint (the exact
    same endpoint ``dexpaprika.get_pool_reserve_usd``/``_resolve_base_token``
    already use), read directly via the module's own ``_get_json`` choke
    point rather than calling both of those separately (would cost 2 calls
    for data already present in 1). Returns
    ``(price_usd, liquidity_usd, created_at)`` -- any field the response
    doesn't carry, or a failed call, comes back ``None`` rather than
    fabricated. Verified live 19/08 against a real pump.fun bonding-curve
    pool (see module docstring): the same call surfaces
    ``last_price_usd``/``liquidity_usd``/``created_at`` together."""
    data, error = await dexpaprika._get_json(f"/networks/{chain}/pools/{pool_address}", params={})
    if error is not None or not isinstance(data, dict):
        return None, None, None
    price_raw = data.get("last_price_usd")
    price_usd = float(price_raw) if isinstance(price_raw, (int, float)) else None
    liq_raw = data.get("liquidity_usd")
    reserve_usd = float(liq_raw) if isinstance(liq_raw, (int, float)) else None
    created_at: datetime | None = None
    raw_created = data.get("created_at")
    if isinstance(raw_created, str) and raw_created:
        try:
            created_at = datetime.fromisoformat(raw_created.replace("Z", "+00:00"))
        except ValueError:
            created_at = None  # never fabricate -- an unparseable date stays None
    return price_usd, reserve_usd, created_at


async def _resolve_liquidity_snapshot(
    pool_address: str, *, chain: str, ws_feed=None,
) -> tuple[float | None, float | None, datetime | None, str | None]:
    """Returns ``(price_usd, reserve_usd, pool_created_at, source)``.
    Tries ``ws_feed`` (a ``PumpSwapWebSocketFeed``, see
    ``services/pumpswap_ws.py``) FIRST when given -- per the mission's
    explicit instruction -- honestly expected to rarely have data this early
    (see module docstring), falling back to the REST DexPaprika detail call
    for any pool it can't currently price. ``pool_created_at`` is only ever
    known via the REST tier (the websocket snapshot carries no such field);
    ``source=None`` and every value ``None`` means BOTH tiers came back
    empty this cycle -- the caller must keep waiting, never treat this as an
    abandonment on its own."""
    if ws_feed is not None:
        try:
            live = ws_feed.get_snapshot(pool_address)
        except Exception as exc:  # noqa: BLE001 -- the feed must never break the batch
            logger.info(
                "solana_fresh_launch_fast_discovery_shadow: ws_feed.get_snapshot failed for %s (%s)",
                pool_address, exc,
            )
            live = None
        if live is not None and getattr(live, "available", False) and live.price_usd:
            return live.price_usd, live.reserve_usd, None, "pumpswap_ws"

    try:
        price_usd, reserve_usd, created_at = await _fetch_pool_snapshot_rest(pool_address, chain)
    except Exception as exc:  # noqa: BLE001 -- one candidate's failure never blocks the batch
        logger.info(
            "solana_fresh_launch_fast_discovery_shadow: REST snapshot failed for %s (%s)",
            pool_address, exc,
        )
        return None, None, None, None
    if price_usd is not None:
        return price_usd, reserve_usd, created_at, "rest_dexpaprika"
    return None, None, None, None


async def _track_candidate(
    event: PumpPortalNewTokenEvent,
    *,
    chain: str = "solana",
    ws_feed=None,
    min_liquidity_usd: float = MIN_LIQUIDITY_USD,
    max_pool_age_minutes: float = MAX_POOL_AGE_MINUTES,
    poll_interval_seconds: float = FAST_DISCOVERY_POLL_INTERVAL_SECONDS,
    resolve_fn=None,
    sleep_fn=asyncio.sleep,
    time_fn=time.time,
) -> dict | None:
    """Polls a single candidate's liquidity (``resolve_fn``, defaults to
    ``_resolve_liquidity_snapshot`` -- overridable in tests, never touches
    the network there) until it crosses ``min_liquidity_usd`` or the
    candidate's age exceeds ``max_pool_age_minutes``, whichever comes
    first. Returns a ready-to-insert row dict on confirmation, or ``None``
    on abandonment -- an abandoned candidate is NEVER logged (mission
    requirement: no phantom row for a candidate that never qualified).

    Best-effort throughout past the polling loop itself (a rugcheck lookup
    failure never blocks a confirmed candidate from being returned)."""
    resolve_fn = resolve_fn or _resolve_liquidity_snapshot
    pool_address = event.bonding_curve_key
    if not pool_address:
        return None  # no pool to track at all -- never guessed

    while True:
        now = time_fn()
        age_seconds = now - event.detected_at
        if age_seconds >= max_pool_age_minutes * 60.0:
            return None  # abandoned -- age ceiling reached before liquidity confirmed

        price_usd, reserve_usd, pool_created_at, source = await resolve_fn(
            pool_address, chain=chain, ws_feed=ws_feed,
        )

        if price_usd is not None and reserve_usd is not None and reserve_usd >= min_liquidity_usd:
            entry_time = time_fn()
            age_at_entry_seconds = (
                (entry_time - pool_created_at.timestamp())
                if pool_created_at is not None
                else (entry_time - event.detected_at)
            )
            realistic_entry_price = _apply_price_impact_and_fee(
                price_usd, trade_size_usd=SIMULATED_TRADE_SIZE_USD, reserve_usd=reserve_usd, side="buy",
            )

            rugcheck_score: int | None = None
            rugcheck_risks: str | None = None
            rugcheck_top_holder_pct: float | None = None
            rugcheck_creator: str | None = None
            try:
                report = await rugcheck.get_token_report(event.mint)
                if report.available:
                    rugcheck_score = report.score_normalised
                    rugcheck_risks = ",".join(report.risks) if report.risks else None
                    rugcheck_top_holder_pct = report.top_holder_pct
                    rugcheck_creator = report.creator
            except Exception as exc:  # noqa: BLE001 -- enrichment must never break a confirmed candidate
                logger.info(
                    "solana_fresh_launch_fast_discovery_shadow: rugcheck lookup failed for %s (%s)",
                    event.mint, exc,
                )

            return {
                "pool_address": pool_address,
                "token_address": event.mint,
                "chain": chain,
                "symbol": event.symbol,
                "name": event.name,
                "discovery_source": "pumpportal",
                "first_seen_at": datetime.fromtimestamp(event.detected_at, tz=timezone.utc).isoformat(),
                "detected_at": datetime.fromtimestamp(entry_time, tz=timezone.utc).isoformat(),
                "pool_created_at": pool_created_at.isoformat() if pool_created_at is not None else None,
                "age_at_entry_seconds": age_at_entry_seconds,
                "liquidity_confirmed_via": source,
                "entry_price": price_usd,
                "reserve_usd": reserve_usd,
                "peak_price": price_usd,
                "realistic_entry_price": realistic_entry_price,
                "market_cap_sol_at_creation": event.market_cap_sol,
                "v_sol_in_bonding_curve_at_creation": event.v_sol_in_bonding_curve,
                "rugcheck_score": rugcheck_score,
                "rugcheck_risks": rugcheck_risks,
                "rugcheck_top_holder_pct": rugcheck_top_holder_pct,
                "rugcheck_creator": rugcheck_creator,
            }

        await sleep_fn(poll_interval_seconds)


async def _insert_confirmed_row(row: dict) -> int:
    await _ensure_table()
    async with aiosqlite.connect(_db_path()) as db:
        cur = await db.execute(
            f"""
            INSERT INTO {TABLE} (
                pool_address, token_address, chain, symbol, name, discovery_source,
                first_seen_at, detected_at, pool_created_at, age_at_entry_seconds,
                liquidity_confirmed_via, entry_price, reserve_usd, remaining_qty, realized_proceeds,
                peak_price, realistic_entry_price, market_cap_sol_at_creation,
                v_sol_in_bonding_curve_at_creation,
                rugcheck_score, rugcheck_risks, rugcheck_top_holder_pct, rugcheck_creator
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1.0, 0.0, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                row["pool_address"], row["token_address"], row["chain"], row["symbol"], row["name"],
                row["discovery_source"], row["first_seen_at"], row["detected_at"], row["pool_created_at"],
                row["age_at_entry_seconds"], row["liquidity_confirmed_via"], row["entry_price"],
                row["reserve_usd"], row["peak_price"], row["realistic_entry_price"],
                row["market_cap_sol_at_creation"], row["v_sol_in_bonding_curve_at_creation"],
                row["rugcheck_score"], row["rugcheck_risks"], row["rugcheck_top_holder_pct"],
                row["rugcheck_creator"],
            ),
        )
        new_id = cur.lastrowid
        await db.commit()
    return new_id


async def _track_and_maybe_insert(
    event: PumpPortalNewTokenEvent, *, chain: str, ws_feed, semaphore: asyncio.Semaphore, stats: dict,
) -> None:
    """Fire-and-forget task body: acquire a concurrency slot, track the
    candidate to confirmation or abandonment, insert if confirmed. Never
    raises into the caller (a single candidate's failure must never break
    the discovery loop)."""
    async with semaphore:
        try:
            await _ensure_table()
            if event.bonding_curve_key:
                async with aiosqlite.connect(_db_path()) as db:
                    if await _has_open_or_recent_signal(db, event.bonding_curve_key, chain):
                        stats["deduped"] = stats.get("deduped", 0) + 1
                        return
            row = await _track_candidate(event, chain=chain, ws_feed=ws_feed)
            if row is None:
                stats["abandoned"] = stats.get("abandoned", 0) + 1
                return
            await _insert_confirmed_row(row)
            stats["confirmed"] = stats.get("confirmed", 0) + 1
        except Exception as exc:  # noqa: BLE001 -- one candidate's failure must never break the loop
            logger.info(
                "solana_fresh_launch_fast_discovery_shadow: tracking failed for mint=%s (%s)",
                event.mint, exc,
            )
            stats["errors"] = stats.get("errors", 0) + 1


async def run_forever(
    feed: PumpPortalNewTokenFeed | None = None,
    *,
    chain: str = "solana",
    ws_feed=None,
    max_concurrent: int = MAX_CONCURRENT_TRACKED_CANDIDATES,
    max_events: int | None = None,
    stop_event: asyncio.Event | None = None,
) -> dict[str, int]:
    """The real entry point a future orchestration script would call (not
    wired anywhere yet -- see module docstring). Starts ``feed`` if not
    already running, then drains its queue indefinitely, spawning a bounded
    number of concurrent ``_track_candidate`` tasks. ``max_events``/
    ``stop_event`` exist purely to make this loop testable/stoppable --
    production use passes neither and lets it run for the process lifetime
    (mirroring ``services/pumpswap_ws.py``'s own ``start``/``stop`` pair for
    graceful shutdown).

    Returns a stats dict (``confirmed``/``abandoned``/``deduped``/``errors``)
    once stopped -- tracking tasks still in flight when ``stop_event`` fires
    or ``max_events`` is reached are awaited to completion before returning
    (never abandoned mid-flight, which would silently lose a candidate that
    was about to confirm)."""
    feed = feed or PumpPortalNewTokenFeed()
    await feed.start()
    await _ensure_table()

    semaphore = asyncio.Semaphore(max_concurrent)
    stats: dict[str, int] = {}
    tasks: list[asyncio.Task] = []
    events_seen = 0

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
                _track_and_maybe_insert(event, chain=chain, ws_feed=ws_feed, semaphore=semaphore, stats=stats)
            )
            tasks.append(task)
            tasks = [t for t in tasks if not t.done()]
    finally:
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    return stats


async def advance_exit_simulation(
    client: GeckoTerminalClient | None = None, *, chain: str = "solana", limit: int = 30, ws_feed=None,
) -> dict[str, int]:
    """Structurally mirrors ``solana_fresh_launch_ws_exit_shadow.
    advance_exit_simulation`` almost line for line (own table, own queue),
    but calls the SAME imported ``evaluate_exit`` rather than a local
    reimplementation -- see module docstring for why this necessary
    duplication is scoped to the orchestration shell only, never the rule."""
    client = client or geckoterminal_client
    counts = {
        "checked": 0, "checked_via_websocket": 0, "checked_via_polling": 0,
        "closed_trailing_stop": 0, "closed_max_hold": 0, "closed_liquidity_collapse": 0,
    }
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
                        "solana_fresh_launch_fast_discovery_shadow: ws_feed.get_snapshot failed for %s (%s)",
                        row["pool_address"], exc,
                    )
                    live = None
                if live is not None and getattr(live, "available", False) and live.price_usd:
                    current_price = live.price_usd
                    reserve_usd = live.reserve_usd
                    dex_id = live.dex_id
                    price_source = "websocket"
                    counts["checked_via_websocket"] += 1

            if current_price is None:
                try:
                    snapshot: PoolSnapshot = await _snapshot_with_fallback(
                        client, row["pool_address"], row["token_address"], chain=chain,
                    )
                except Exception as exc:  # noqa: BLE001 -- one pool's failure never blocks the batch
                    logger.info(
                        "solana_fresh_launch_fast_discovery_shadow: snapshot failed for %s (%s)",
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

                try:
                    ohlcv: OHLCVResult = await client.get_ohlcv(row["pool_address"], network=chain, mode="scalping_5m")
                except Exception as exc:  # noqa: BLE001 -- OHLCV is an enhancement, never a hard requirement
                    logger.info(
                        "solana_fresh_launch_fast_discovery_shadow: get_ohlcv failed for %s (%s)",
                        row["pool_address"], exc,
                    )
                    ohlcv = None
                if ohlcv is not None and ohlcv.available and ohlcv.candles:
                    boundary_epoch = _epoch_of(row.get("last_checked_at") or row["detected_at"])
                    new_candles = [c for c in ohlcv.candles if boundary_epoch is None or c.ts > boundary_epoch]
                    if new_candles:
                        window_high = max(c.high for c in new_candles)
                        window_low = min(c.low for c in new_candles)

            counts["checked"] += 1
            result = evaluate_exit(
                row, current_price=current_price, reserve_usd=reserve_usd, dex_id=dex_id,
                age_minutes=age_minutes, window_high=window_high, window_low=window_low,
            )
            if result["skipped"]:
                await _stamp_last_checked_only(row["id"])
                continue

            await _persist_exit_result(row["id"], result, price_source)

            if result["exit_reason"] == "trailing_stop":
                counts["closed_trailing_stop"] += 1
            elif result["exit_reason"] == "max_hold":
                counts["closed_max_hold"] += 1
            elif result["exit_reason"] == "liquidity_collapse":
                counts["closed_liquidity_collapse"] += 1
    except Exception as exc:  # noqa: BLE001 -- shadow simulation must never raise into a caller
        logger.info("solana_fresh_launch_fast_discovery_shadow: advance_exit_simulation failed (%s)", exc)
    return counts


async def _stamp_last_checked_only(row_id: int) -> None:
    """Same starvation-bug fix already fixed live 19/08 in both siblings --
    marks a row as attempted this cycle even when no price could be
    obtained at all, so it drops to the back of the round-robin queue
    instead of blocking every younger row forever."""
    try:
        async with aiosqlite.connect(_db_path()) as db:
            await db.execute(
                f"UPDATE {TABLE} SET last_checked_at = ? WHERE id = ?",
                (datetime.now(timezone.utc).isoformat(), row_id),
            )
            await db.commit()
    except Exception as exc:  # noqa: BLE001 -- best-effort, never blocks the batch
        logger.info(
            "solana_fresh_launch_fast_discovery_shadow: _stamp_last_checked_only failed for id=%s (%s)", row_id, exc,
        )


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


async def chain_pnl_summary_realistic(chain: str = "solana") -> dict:
    """Same shape/doctrine as both siblings' own
    ``chain_pnl_summary_realistic`` (unreachable_liquidity/stranded/
    outlier_excluded never silently dropped) -- own implementation since it
    queries this module's own ``TABLE``. ``sanity_reference`` always falls
    back to ``entry_price`` (no ``support_range_high`` column here, see
    module docstring)."""
    await _ensure_table()
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            f"SELECT realistic_entry_price, remaining_qty, realistic_realized_proceeds, "
            f"realistic_final_multiplier, last_price, exit_reason, entry_price "
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
        if entry is None:
            unreachable_liquidity += 1
            continue
        sanity_reference = r.get("entry_price")
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
            f"SELECT exit_reason, final_multiplier, age_at_entry_seconds, liquidity_confirmed_via FROM {TABLE} "
            "WHERE chain = ? AND final_multiplier IS NOT NULL",
            (chain,),
        )
        rows = [dict(r) for r in await cur.fetchall()]
    wins = sum(1 for r in rows if r["final_multiplier"] > 1.0)
    by_exit_reason: dict[str, int] = {}
    by_liquidity_source: dict[str, int] = {}
    for r in rows:
        by_exit_reason[r["exit_reason"]] = by_exit_reason.get(r["exit_reason"], 0) + 1
        src = r["liquidity_confirmed_via"]
        if src:
            by_liquidity_source[src] = by_liquidity_source.get(src, 0) + 1
    ages = [r["age_at_entry_seconds"] for r in rows if r["age_at_entry_seconds"] is not None]
    return {
        "completed": len(rows),
        "wins": wins,
        "win_rate": (wins / len(rows)) if rows else None,
        "avg_multiplier": (sum(r["final_multiplier"] for r in rows) / len(rows)) if rows else None,
        "avg_age_at_entry_seconds": (sum(ages) / len(ages)) if ages else None,
        "by_exit_reason": by_exit_reason,
        "by_liquidity_confirmed_via": by_liquidity_source,
    }
