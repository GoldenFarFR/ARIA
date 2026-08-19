"""Solana "fresh launch" shadow (19/08, operator-directed) -- replaces the
now-retired support-bounce v1/v2 pockets (``solana_support_bounce_shadow.py``/
``solana_support_bounce_v2_shadow.py``, both stopped and force-closed the same
day). Never coexists with them: this is the new sole active Solana shadow
pocket, not a third parallel variant.

**Why no entry filter beyond age+liquidity (deliberate, empirically-driven)**:
a live test the same day compared a distance-from-support entry filter
(same style as the retired v1/v2 pockets) against an unfiltered baseline on a
real 27-pool sample -- the 20 pools a distance filter would have REJECTED
outperformed the 5 it would have ACCEPTED (median x1.64 vs x0.83). Small
sample, not a rigorous backtest, but strong enough in the wrong direction to
drop the filter rather than keep tuning it. The only two gates left are
therefore structural, not predictive: a pool must be freshly launched
(``MAX_POOL_AGE_MINUTES``) and have a minimum real depth
(``MIN_LIQUIDITY_USD``) -- everything else is left to the EXIT mechanism to
sort out. ``distance_from_support_pct`` is still computed and logged on every
row (purely informational, on whatever 1-minute candles are available at
entry -- see its own comment below) so a future pass can still mine the
signal retrospectively without having thrown the data away.

**Exit mechanics -- ported, never duplicated, from ``solana_pump_shadow.py``**
(the calibrated 25%-of-remaining scale-out ladder + trailing stop + max-hold
rule, see that module's own docstring for the full empirical basis): this
module imports ``_apply_price_impact_and_fee``, ``SCALE_OUT_STEP_PCT``,
``SCALE_OUT_SELL_FRACTION``, ``_SCALE_OUT_DUST_FRACTION`` directly rather than
reimplementing them. ``TRAILING_STOP_PCT``/``LIQUIDITY_COLLAPSE_EXIT_PCT``/
``MAX_HOLD_MINUTES`` are this module's OWN constants (not shared state),
following the same convention as every other shadow pocket in this dome.

**PumpSwap/pump.fun reserve-misreport carve-out is CRITICAL here, not an
edge case** (see ``solana_pump_shadow.py``'s own comment for the full root
cause: PumpSwap pools report near-zero reserve from both DexScreener and
GeckoTerminal regardless of real depth): this module's whole discovery
window (age <= 5min) means the overwhelming majority of candidates ARE
pump.fun bonding-curve pools, so ``liquidity_collapse`` would misfire
constantly without the ``is_pumpswap`` guard ported from
``solana_support_bounce_shadow.py`` (the 18/08 SadDog incident that same
comment documents happened on exactly this pool type).

**PEAK_PRICE_SANITY_MULTIPLE -- corrupted-upstream-price guard, ported from
day one** (never bolted on after the fact, unlike the original support-bounce
pocket where this was found live via 2 real corrupted positions -- Jotchua/WW,
see that module's own comment for the full incident writeup). Since this
module doesn't always have a support-range reference at entry (a candidate
with 0 available 1-minute candles logs a NULL ``support_range_high``), the
sanity check falls back to ``entry_price`` itself as the reference when no
support range was captured -- still a real, observed value, never fabricated.

**No closure cap, ever (19/08, explicit operator correction)** -- unlike
``solana_variant_shadow.py``'s ``TARGET_CLOSURES_PER_VARIANT`` (which stops
SOURCING once a target is hit, a pattern deliberately NOT reproduced here),
this module keeps opening and closing positions indefinitely. Instead, a
CHECKPOINT is written every ``CHECKPOINT_INTERVAL`` (50) total closures into
a dedicated append-only table (``solana_fresh_launch_checkpoint_log``) -- a
snapshot of ``chain_pnl_summary_realistic()`` at that exact moment, so "how
is this pocket doing after 50/100/150 closures" can be answered by reading
back an already-computed row instead of a fresh full-table aggregate, and
without ever pausing the pocket to do it. Idempotent by construction
(``checkpoint_number`` is UNIQUE, ``INSERT OR IGNORE``) -- closing several
positions in the same ``advance_exit_simulation`` pass, or crossing several
50-boundaries in one batch, never double-writes or skips a checkpoint.

Same bright-line doctrine as every other shadow module in this dome: never
opens a real or paper-capital position, never calls
``wallet_guard``/``agent_wallet_pilot``/``paper_trader.open_position``, never
wired to the heartbeat, pure read+log+simulate. No Telegram notifications
(deliberate, same reasoning as ``solana_variant_shadow.py``'s own choice at
its scale: this pocket's discovery window is wide enough to produce a high
volume of opens/closes, and results are consulted on demand via
``chain_pnl_summary_realistic()``/``get_checkpoints()`` rather than pushed)."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import aiosqlite

from aria_core.paths import shadow_db_path
from aria_core.services import dexpaprika
from aria_core.services.geckoterminal import (
    GeckoTerminalClient,
    OHLCVResult,
    PoolSnapshot,
    TrendingPool,
    geckoterminal_client,
)
from aria_core.solana_pump_shadow import (
    SCALE_OUT_SELL_FRACTION,
    SCALE_OUT_STEP_PCT,
    SIMULATED_TRADE_SIZE_USD,
    _apply_price_impact_and_fee,
    _epoch_of,
    _minutes_since,
    _snapshot_with_fallback,
    _SCALE_OUT_DUST_FRACTION,
)

logger = logging.getLogger(__name__)

DB_PATH = str(shadow_db_path())

TABLE = "solana_fresh_launch_shadow_log"
CHECKPOINT_TABLE = "solana_fresh_launch_checkpoint_log"

# Entry criteria (operator-specified 19/08, real live test confirmed both
# server-side DexPaprika filters -- liquidity_usd_min/created_after -- work
# independently of order_by, so nothing is lost sourcing this way).
MIN_LIQUIDITY_USD = 3000.0
MAX_POOL_AGE_MINUTES = 5.0

# Informational-only support-distance reading, see module docstring -- NEVER
# gates entry. 1-minute candles, 1-5 available (never wait for the full 5 --
# the earliest window is the whole point, operator-explicit).
SUPPORT_CANDLE_INTERVAL = "1m"
SUPPORT_CANDLE_MAX_COUNT = 5

# Exit mechanics -- ladder constants imported from solana_pump_shadow.py
# (never duplicated); these three are THIS module's own tuning, operator-
# specified 19/08.
TRAILING_STOP_PCT = 15.0
MAX_HOLD_MINUTES = 60.0
LIQUIDITY_COLLAPSE_EXIT_PCT = 50.0  # same standard value as every other pocket in this dome

# Same doctrine/incident as solana_support_bounce_shadow.py's own constant
# (see that module's comment for the full Jotchua/WW writeup) -- ported here
# from day one rather than found live a second time. 50x is deliberately
# generous, catches a corrupted-upstream-read artifact without ever
# rejecting a genuine pump.
PEAK_PRICE_SANITY_MULTIPLE = 50.0

# 19/08, operator correction: no closure cap that stops sourcing (unlike
# solana_variant_shadow.py's TARGET_CLOSURES_PER_VARIANT) -- this pocket
# trades indefinitely. A checkpoint snapshot is written every N total
# closures instead, see module docstring.
CHECKPOINT_INTERVAL = 50

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
                next_scale_level REAL,
                exit_reason TEXT,
                final_multiplier REAL,
                last_checked_at TEXT,
                last_price REAL,
                last_reserve_usd REAL,
                realistic_entry_price REAL,
                realistic_realized_proceeds REAL NOT NULL DEFAULT 0.0,
                realistic_final_multiplier REAL,
                trailing_stop_pct_used REAL
            )
            """
        )
        # Scaffold kept ready, same PRAGMA-guarded-migration convention as
        # every other shadow module in this dome -- empty for now since every
        # field needed at launch is already in the CREATE TABLE above
        # (operator-directed: capture the schema in full at creation, not via
        # a migration afterward).
        added_columns: list[tuple[str, str]] = []
        existing = {
            row[1] for row in await (await db.execute(f"PRAGMA table_info({TABLE})")).fetchall()
        }
        for name, ddl in added_columns:
            if name not in existing:
                await db.execute(f"ALTER TABLE {TABLE} ADD COLUMN {name} {ddl}")
        await db.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_lookup ON {TABLE} (pool_address, chain, exit_reason)"
        )
        await db.execute(f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_detected_at ON {TABLE} (detected_at)")

        await db.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {CHECKPOINT_TABLE} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                checkpoint_number INTEGER NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                closed INTEGER NOT NULL,
                stranded INTEGER NOT NULL,
                open_valued INTEGER NOT NULL,
                pending_price INTEGER NOT NULL,
                unreachable_liquidity INTEGER NOT NULL,
                outlier_excluded INTEGER NOT NULL,
                total_pnl_units REAL NOT NULL,
                total_pnl_usd REAL NOT NULL,
                capital_deployed_usd REAL NOT NULL,
                return_on_deployed_pct REAL NOT NULL
            )
            """
        )
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
    """Cheap, no-network pre-filter: liquidity floor, age ceiling (never a
    floor -- the whole point is catching a launch as early as possible),
    dedup against an already-open position on the same pool. Defensive
    re-check even though the caller is expected to have already applied the
    same two conditions server-side via ``dexpaprika.get_trending_pools``
    (never trusted blindly, same doctrine as every other pocket here)."""
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
    """Sourcing + dedup + log -- best-effort throughout, a network/DB failure
    here must never raise into whatever fetched ``pools``. Same 3-pass
    connection-scoping discipline as every other pocket in this dome (cheap
    filter with a short connection, network enrichment with NO connection
    held open, one final batched-but-per-row insert) -- the real "database is
    locked" incident this pattern fixes is documented in
    ``solana_support_bounce_shadow.py``'s own docstring."""
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
                    "solana_fresh_launch_shadow: candle fetch failed for %s (%s)",
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
                # Freshest close available, same "never trust the stale
                # broad-scan snapshot price" fix as solana_support_bounce_
                # shadow.py's own 17/08 Niles incident.
                current_price = last_n[-1].close or current_price
                range_low = min(c.low for c in last_n)
                range_high = max(c.high for c in last_n)
                if range_low and range_high > range_low:
                    support_range_low = range_low
                    support_range_high = range_high
                    distance_from_support_pct = (current_price - range_low) / (range_high - range_low) * 100.0
                # else: degenerate/flat range (or a single candle, where
                # low==high trivially) -- NULL, never fabricated.

            if current_price is None:
                continue  # never fabricate an entry price

            realistic_entry_price = _apply_price_impact_and_fee(
                current_price, trade_size_usd=SIMULATED_TRADE_SIZE_USD,
                reserve_usd=pool.reserve_usd, side="buy",
            )
            first_scale_level = current_price * (1 + SCALE_OUT_STEP_PCT / 100.0)

            rows_to_insert.append((
                pool.pool_address, pool.token_address, chain, pool.symbol,
                datetime.now(timezone.utc).isoformat(), current_price,
                pool.reserve_usd, pool.pool_created_at.isoformat(),
                distance_from_support_pct, support_range_low, support_range_high, support_candle_count,
                current_price, first_scale_level,
                realistic_entry_price,
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
                            remaining_qty, realized_proceeds, peak_price, next_scale_level,
                            realistic_entry_price
                        ) VALUES (
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1.0, 0.0, ?, ?, ?
                        )
                        """,
                        row,
                    )
                    new_id = cur.lastrowid
                    await db.commit()
                    if candles:
                        await shadow_candle_archive.store_candles(
                            module="solana_fresh_launch", position_id=new_id,
                            pool_address=row[0], chain=chain, phase="before", candles=candles,
                        )
            result["logged"] = len(rows_to_insert)
    except Exception as exc:  # noqa: BLE001 -- shadow logging must never break the caller
        logger.info("solana_fresh_launch_shadow: record_signals failed (%s)", exc)
    return result


async def _maybe_write_checkpoint(chain: str) -> None:
    """Best-effort, never raises into the caller -- a checkpoint write
    failure must never interrupt the exit-simulation pass that triggered it.
    Idempotent via ``INSERT OR IGNORE`` on the UNIQUE ``checkpoint_number``:
    safe to call after every single closure, even several within the same
    batch crossing the same or several 50-boundaries."""
    try:
        total_closed = await closures_so_far()
        checkpoint_number = (total_closed // CHECKPOINT_INTERVAL) * CHECKPOINT_INTERVAL
        if checkpoint_number <= 0:
            return
        pnl = await chain_pnl_summary_realistic(chain)
        async with aiosqlite.connect(_db_path()) as db:
            await db.execute(
                f"""
                INSERT OR IGNORE INTO {CHECKPOINT_TABLE} (
                    checkpoint_number, created_at, closed, stranded, open_valued, pending_price,
                    unreachable_liquidity, outlier_excluded, total_pnl_units, total_pnl_usd,
                    capital_deployed_usd, return_on_deployed_pct
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    checkpoint_number, datetime.now(timezone.utc).isoformat(),
                    pnl["closed"], pnl["stranded"], pnl["open_valued"], pnl["pending_price"],
                    pnl["unreachable_liquidity"], pnl["outlier_excluded"],
                    pnl["total_pnl_units"], pnl["total_pnl_usd"],
                    pnl["capital_deployed_usd"], pnl["return_on_deployed_pct"],
                ),
            )
            await db.commit()
    except Exception as exc:  # noqa: BLE001 -- a checkpoint failure must never block exit simulation
        logger.info("solana_fresh_launch_shadow: checkpoint write failed (%s)", exc)


async def get_checkpoints() -> list[dict]:
    """Read-back accessor -- every checkpoint ever written, oldest first.
    Answers "how did this pocket look after 50/100/150 closures" without a
    fresh full-table aggregate."""
    await _ensure_table()
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(f"SELECT * FROM {CHECKPOINT_TABLE} ORDER BY checkpoint_number ASC")
        return [dict(r) for r in await cur.fetchall()]


async def advance_exit_simulation(
    client: GeckoTerminalClient | None = None, *, chain: str = "solana", limit: int = 30,
) -> dict[str, int]:
    """Real calibrated exit rule -- scale-out ladder + trailing stop, ported
    from ``solana_pump_shadow.py`` (never reimplemented), plus the two safety
    nets (``liquidity_collapse``, PumpSwap-aware; ``max_hold``) and the
    corrupted-upstream-price guard (``PEAK_PRICE_SANITY_MULTIPLE``). Priority
    order, matching the calibrated rule's own precedence: liquidity_collapse
    first (protects against an unsellable pool regardless of price), then the
    scale-out ladder (a rising-price event), then the trailing stop, then
    max_hold as the final catch-all -- identical ordering to
    solana_pump_shadow.py's own ``advance_exit_simulation``.

    Reads OHLCV candles closed since the row's own ``last_checked_at`` (5min
    granularity, same as every other pocket here) and walks the ladder/stop
    against the WINDOW high/low, not a single point-sample -- same detection
    fix already live everywhere else in this dome (see
    ``solana_support_bounce_shadow.py``'s own comment for the 2 real bugs
    this closes: a stop crossed-then-recovered between polls, a rung
    reached-then-retraced)."""
    client = client or geckoterminal_client
    counts = {
        "checked": 0, "scale_out_fills": 0, "closed_scale_out_complete": 0,
        "closed_trailing_stop": 0, "closed_max_hold": 0, "closed_liquidity_collapse": 0,
    }
    try:
        await _ensure_table()
        async with aiosqlite.connect(_db_path()) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                f"SELECT * FROM {TABLE} WHERE chain = ? AND exit_reason IS NULL ORDER BY detected_at ASC LIMIT ?",
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

            try:
                snapshot: PoolSnapshot = await _snapshot_with_fallback(
                    client, row["pool_address"], row["token_address"], chain=chain,
                )
            except Exception as exc:  # noqa: BLE001 -- one pool's failure never blocks the batch
                logger.info(
                    "solana_fresh_launch_shadow: snapshot failed for %s (%s)", row["pool_address"], exc,
                )
                continue
            if not snapshot.available or snapshot.price_usd is None:
                continue
            counts["checked"] += 1
            current_price = snapshot.price_usd

            window_high = current_price
            window_low = current_price
            try:
                ohlcv: OHLCVResult = await client.get_ohlcv(
                    row["pool_address"], network=chain, mode="scalping_5m",
                )
            except Exception as exc:  # noqa: BLE001 -- OHLCV is an enhancement, never a hard requirement
                logger.info(
                    "solana_fresh_launch_shadow: advance_exit_simulation get_ohlcv failed for %s (%s)",
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
                        module="solana_fresh_launch", position_id=row["id"],
                        pool_address=row["pool_address"], chain=chain, phase="after",
                        candles=new_candles,
                    )
            effective_high = max(window_high, current_price)
            effective_low = min(window_low, current_price)

            # Corrupted-upstream-price guard -- see PEAK_PRICE_SANITY_MULTIPLE's
            # own comment. Falls back to entry_price when no support range was
            # captured at entry (e.g. zero candles available at that moment) --
            # still a real, observed value, never fabricated.
            sanity_reference = row.get("support_range_high") or entry_price
            if sanity_reference and effective_high > sanity_reference * PEAK_PRICE_SANITY_MULTIPLE:
                logger.info(
                    "solana_fresh_launch_shadow: implausible price for %s "
                    "(effective_high=%.10g, sanity_reference=%.10g) -- "
                    "skipping this cycle, treated as unavailable",
                    row["pool_address"], effective_high, sanity_reference,
                )
                continue

            peak_price = row["peak_price"] or entry_price
            peak_price = max(peak_price, effective_high)
            next_scale_level = row["next_scale_level"] or (entry_price * (1 + SCALE_OUT_STEP_PCT / 100.0))
            remaining_qty = row["remaining_qty"] if row["remaining_qty"] is not None else 1.0
            realized_proceeds = row["realized_proceeds"] or 0.0

            realistic_entry_price = row.get("realistic_entry_price")
            realistic_realized_proceeds = row.get("realistic_realized_proceeds") or 0.0
            realistic_unreachable = realistic_entry_price is None

            def _realistic_sell(qty_fraction: float, ideal_price: float) -> None:
                nonlocal realistic_realized_proceeds, realistic_unreachable
                if realistic_unreachable:
                    return
                impacted = _apply_price_impact_and_fee(
                    ideal_price, trade_size_usd=qty_fraction * SIMULATED_TRADE_SIZE_USD,
                    reserve_usd=snapshot.reserve_usd, side="sell",
                )
                if impacted is None:
                    realistic_unreachable = True
                    return
                realistic_realized_proceeds += qty_fraction * impacted

            # PumpSwap/pump.fun reserve-misreport carve-out -- CRITICAL here,
            # see module docstring. Same guard as solana_support_bounce_
            # shadow.py/solana_pump_shadow.py, never reimplemented differently.
            is_pumpswap = snapshot.dex_id == "pumpswap"
            entry_reserve = row.get("reserve_usd")
            liquidity_collapsed = (
                not is_pumpswap
                and entry_reserve is not None and entry_reserve > 0
                and snapshot.reserve_usd is not None
                and snapshot.reserve_usd < entry_reserve * (1 - LIQUIDITY_COLLAPSE_EXIT_PCT / 100.0)
            )

            fills_this_cycle = 0
            exit_reason: str | None = None
            if liquidity_collapsed:
                _realistic_sell(remaining_qty, current_price)
                realized_proceeds += remaining_qty * current_price
                remaining_qty = 0.0
                exit_reason = "liquidity_collapse"
            else:
                while remaining_qty > _SCALE_OUT_DUST_FRACTION and effective_high >= next_scale_level:
                    sell_fraction = remaining_qty * SCALE_OUT_SELL_FRACTION
                    _realistic_sell(sell_fraction, next_scale_level)
                    realized_proceeds += sell_fraction * next_scale_level
                    remaining_qty -= sell_fraction
                    next_scale_level *= (1 + SCALE_OUT_STEP_PCT / 100.0)
                    fills_this_cycle += 1
                counts["scale_out_fills"] += fills_this_cycle

                if remaining_qty <= _SCALE_OUT_DUST_FRACTION and fills_this_cycle:
                    _realistic_sell(remaining_qty, current_price)
                    realized_proceeds += remaining_qty * current_price
                    remaining_qty = 0.0
                    exit_reason = "scale_out_complete"
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

            async with aiosqlite.connect(_db_path()) as db:
                await db.execute(
                    f"""
                    UPDATE {TABLE} SET
                        peak_price = ?, next_scale_level = ?, remaining_qty = ?, realized_proceeds = ?,
                        exit_reason = ?, final_multiplier = ?, last_checked_at = ?, last_price = ?,
                        realistic_realized_proceeds = ?, realistic_final_multiplier = ?, last_reserve_usd = ?,
                        trailing_stop_pct_used = ?
                    WHERE id = ?
                    """,
                    (
                        peak_price, next_scale_level, remaining_qty, realized_proceeds, exit_reason,
                        final_multiplier, datetime.now(timezone.utc).isoformat(), current_price,
                        realistic_realized_proceeds, realistic_final_multiplier, snapshot.reserve_usd,
                        TRAILING_STOP_PCT,
                        row["id"],
                    ),
                )
                await db.commit()

            if exit_reason == "scale_out_complete":
                counts["closed_scale_out_complete"] += 1
            elif exit_reason == "trailing_stop":
                counts["closed_trailing_stop"] += 1
            elif exit_reason == "max_hold":
                counts["closed_max_hold"] += 1
            elif exit_reason == "liquidity_collapse":
                counts["closed_liquidity_collapse"] += 1

            if exit_reason is not None:
                await _maybe_write_checkpoint(chain)
    except Exception as exc:  # noqa: BLE001 -- shadow simulation must never raise into a caller
        logger.info("solana_fresh_launch_shadow: advance_exit_simulation failed (%s)", exc)
    return counts


async def chain_pnl_summary_realistic(chain: str = "solana") -> dict:
    """Realistic (price-impact + fee aware) PnL aggregate -- same shape as
    every other pocket in this dome (``solana_support_bounce_shadow.py``,
    ``solana_pump_shadow.py``): a row whose ``realistic_entry_price`` is NULL
    means the pool was already too shallow to fill a
    ``SIMULATED_TRADE_SIZE_USD`` trade at entry (``unreachable_liquidity``,
    never silently dropped); a closed row whose implied exit price clears
    ``PEAK_PRICE_SANITY_MULTIPLE`` above its own reference is excluded into
    ``outlier_excluded`` rather than distorting the aggregate; a bought-then-
    stranded position (pool drained mid-flight, never a clean exit) counts as
    a real loss (``stranded``), never an unmeasurable non-event."""
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
                # Bought-then-stranded capital (pool drained mid-flight) is a
                # LOSS, never an unmeasurable event -- same survivorship-bias
                # guard as every other pocket here.
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
            f"SELECT exit_reason, final_multiplier, distance_from_support_pct FROM {TABLE} "
            "WHERE chain = ? AND final_multiplier IS NOT NULL",
            (chain,),
        )
        rows = [dict(r) for r in await cur.fetchall()]
    wins = sum(1 for r in rows if r["final_multiplier"] > 1.0)
    by_exit_reason: dict[str, int] = {}
    for r in rows:
        by_exit_reason[r["exit_reason"]] = by_exit_reason.get(r["exit_reason"], 0) + 1
    return {
        "completed": len(rows),
        "wins": wins,
        "win_rate": (wins / len(rows)) if rows else None,
        "avg_multiplier": (sum(r["final_multiplier"] for r in rows) / len(rows)) if rows else None,
        "by_exit_reason": by_exit_reason,
    }
