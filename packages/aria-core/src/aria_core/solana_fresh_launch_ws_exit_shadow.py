"""Solana "fresh launch" shadow -- WEBSOCKET-EXIT VARIANT (19/08). A SEPARATE,
PARALLEL A/B counterpart to ``solana_fresh_launch_shadow.py``, never a
replacement -- both pockets run side by side so the operator can compare
them objectively before any production change. NOT wired to the heartbeat,
NOT gated by any ``ARIA_*`` flag, silently available only.

**What is held constant vs. what changes (the whole point of this module)**:
  - ENTRY criterion: IDENTICAL, byte-for-byte, to ``solana_fresh_launch_
    shadow.py`` -- ``MIN_LIQUIDITY_USD``/``MAX_POOL_AGE_MINUTES`` are
    IMPORTED from that module (never redefined here), so a future
    recalibration of the entry criterion in the original automatically
    applies here too, keeping the A/B pairing valid forever. No entry filter
    beyond age<=5min + liquidity>=3000$, same as the original (see that
    module's own docstring for the empirical basis).
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

import logging
from datetime import datetime, timezone

import aiosqlite

from aria_core.paths import shadow_db_path
from aria_core.services import dexpaprika, rugcheck
from aria_core.services.geckoterminal import (
    GeckoTerminalClient,
    OHLCVResult,
    PoolSnapshot,
    TrendingPool,
    geckoterminal_client,
)
from aria_core.solana_fresh_launch_shadow import (
    MAX_POOL_AGE_MINUTES,
    MIN_LIQUIDITY_USD,
    PEAK_PRICE_SANITY_MULTIPLE,
    SUPPORT_CANDLE_INTERVAL,
    SUPPORT_CANDLE_MAX_COUNT,
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

TABLE = "solana_fresh_launch_ws_exit_shadow_log"

# Own constants, deliberately the SAME numeric values as
# solana_fresh_launch_shadow.py -- see module docstring for why they are
# not shared state.
TRAILING_STOP_PCT = 15.0
MAX_HOLD_MINUTES = 60.0
LIQUIDITY_COLLAPSE_EXIT_PCT = 50.0

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

    final_multiplier = (realized_proceeds / entry_price) if exit_reason else None
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
    ws_feed=None,
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

    Same round-robin queue fix as the original module
    (``COALESCE(last_checked_at, detected_at)``), ported from day one."""
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

            if current_price is None:
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

                try:
                    ohlcv: OHLCVResult = await client.get_ohlcv(
                        row["pool_address"], network=chain, mode="scalping_5m",
                    )
                except Exception as exc:  # noqa: BLE001 -- OHLCV is an enhancement, never a hard requirement
                    logger.info(
                        "solana_fresh_launch_ws_exit_shadow: get_ohlcv failed for %s (%s)",
                        row["pool_address"], exc,
                    )
                    ohlcv = None
                if ohlcv is not None and ohlcv.available and ohlcv.candles:
                    boundary_epoch = _epoch_of(row.get("last_checked_at") or row["detected_at"])
                    new_candles = [
                        c for c in ohlcv.candles if boundary_epoch is None or c.ts > boundary_epoch
                    ]
                    if new_candles:
                        window_high = max(c.high for c in new_candles)
                        window_low = min(c.low for c in new_candles)
                        from aria_core import shadow_candle_archive

                        await shadow_candle_archive.store_candles(
                            module="solana_fresh_launch_ws_exit", position_id=row["id"],
                            pool_address=row["pool_address"], chain=chain, phase="after",
                            candles=new_candles,
                        )
            # A websocket-priced check has no closed-candle window (the
            # whole point is not waiting on candles) -- window_high/low stay
            # None, evaluate_exit() falls back to the point-sample price
            # itself, appropriate since the price stream is near-continuous
            # rather than a periodic snapshot.

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
        if entry is None:
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
