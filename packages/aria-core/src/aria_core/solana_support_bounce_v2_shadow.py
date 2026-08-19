"""Support-bounce v2 Solana shadow (18/08, operator-directed) -- a parallel
variant of ``solana_support_bounce_shadow.py``, running SIDE BY SIDE on the
SAME discovery feed (never a second discovery call) into its OWN table,
never touching the original's ongoing sample.

**Reset 18/08, same day -- v2's test subject changed.** The original 3-way
bundle (MAX_RANGE_RATIO 3.0->1.5, TRAILING_STOP_PCT 10.0->5.0, a new
MAX_H1_PCT 20.0 ceiling) is RETIRED: MAX_RANGE_RATIO's own justification
(a "2.5-3.0x net negative" bucket) did not reproduce on a wider sample and
was reverted upstream on the original pocket too (see that module's own
MAX_RANGE_RATIO comment); TRAILING_STOP_PCT=5.0 is no longer v2-distinctive,
the original pocket adopted it directly. The operator asked for a clean,
ISOLATED single-variable test instead of stacking a 4th change onto an
already 3-deep bundle (the original's own docstring already names bundling
as a real cost -- it stops any result from being attributable to one cause).

v2 now tests ONE new candidate, found by filtering the original's own closed
positions retrospectively: ``reserve_usd >= 20_000`` AND
``rugcheck_top_holder_pct <= 15`` together produced +15.81% realistic PnL,
50% winrate, n=28 -- the best combined result found so far on this pocket's
data. Same honest caveat as every retrospective bin analysis in this
project: this only measured what happens WITHIN already-accepted
candidates, never whether a rejected one would have failed too, and n=28 is
thin enough that this could still be noise (exactly the class of finding
that turned out NOT to reproduce for MAX_RANGE_RATIO earlier the same
day) -- v2 exists to test it prospectively, out-of-sample, rather than
trust the retrospective read on its own.

Every other constant is now IDENTICAL to the original (including
MAX_RANGE_RATIO=5.0 and TRAILING_STOP_PCT=5.0, both no longer test
variables here) -- only ``MIN_LIQUIDITY_USD`` (5000 -> 20000) and the new
``MAX_TOP_HOLDER_PCT`` (15.0, new check) differ. The top-holder check is
FAIL-OPEN on missing data (rugcheck unavailable/lookup failed never blocks
entry, same dome-wide doctrine as every other best-effort enrichment here)
and FAIL-CLOSED only on a CONFIRMED reading above the ceiling.

See ``solana_support_bounce_shadow.py`` for the full design rationale on
everything not called out here (support-tolerance doctrine, exit mechanics,
PumpSwap dex_id guard, realistic price-impact simulation, the 18/08
liquidity-unknown-vs-genuinely-dry backfill fix -- inherited for free via
the shared ``solana_pump_shadow`` imports, never duplicated; the 18/08
OHLCV-window stop-detection fix -- own copy here, same as the original,
fill price still the theoretical threshold, never the crash extreme)."""
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
from aria_core.solana_pump_shadow import (
    DEX_FEE_PCT,
    SIMULATED_TRADE_SIZE_USD,
    _apply_price_impact_and_fee,
    _epoch_of,
    _minutes_since,
    _snapshot_with_fallback,
)

logger = logging.getLogger(__name__)

DB_PATH = str(shadow_db_path())

TABLE = "solana_support_bounce_v2_shadow_log"

# Entry criteria -- identical to the original except MIN_LIQUIDITY_USD and
# the new MAX_TOP_HOLDER_PCT check, see module docstring for the real-data
# basis of the change.
MIN_H1_PCT = -5.0
MIN_LIQUIDITY_USD = 20_000.0  # 18/08, raised from 5000 -- see module docstring
MAX_TOP_HOLDER_PCT = 15.0  # 18/08, NEW -- see module docstring; fail-open on
# missing rugcheck data, fail-closed only on a confirmed reading above this.
MIN_POOL_AGE_MINUTES = 70.0  # no upper bound, deliberately
SUPPORT_TOLERANCE_PCT = 20.0
SUPPORT_CANDLE_COUNT = 10
SUPPORT_CANDLE_INTERVAL = "5m"
MAX_RANGE_RATIO = 5.0  # 18/08 -- no longer a v2-distinctive test variable,
# aligned with the original (see that module's own comment on this line).

# Exit mechanics -- TRAILING_STOP_PCT tightened, everything else identical.
TRAILING_STOP_PCT = 5.0  # 18/08, tightened from 10.0 -- see module docstring
MAX_HOLD_MINUTES = 120.0
LIQUIDITY_COLLAPSE_EXIT_PCT = 50.0

TARGET_CLOSURES = 150

# 18/08, operator-directed exhaustive-capture pass (same as
# solana_support_bounce_shadow.py -- see its own comment for the full
# reasoning): PRAGMA-guarded ALTER TABLE so the already-existing prod DB
# migrates in place.
_ADDED_COLUMNS: list[tuple[str, str]] = [
    ("m5_pct", "REAL"),
    ("h6_pct", "REAL"),
    ("h24_pct", "REAL"),
    ("volume_usd_24h", "REAL"),
    ("transactions_24h", "INTEGER"),
    ("dex_id", "TEXT"),
    ("distance_from_support_pct_5", "REAL"),
    ("distance_from_support_pct_15", "REAL"),
    ("distance_from_support_pct_20", "REAL"),
    ("distance_from_support_pct_30", "REAL"),
]

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
                h1_pct REAL,
                reserve_usd REAL,
                range_low_10c REAL,
                range_high_10c REAL,
                distance_from_support_pct REAL,
                pool_created_at TEXT,
                rugcheck_score INTEGER,
                rugcheck_risks TEXT,
                rugcheck_top_holder_pct REAL,
                rugcheck_creator TEXT,
                remaining_qty REAL NOT NULL DEFAULT 1.0,
                realized_proceeds REAL NOT NULL DEFAULT 0.0,
                peak_price REAL,
                exit_reason TEXT,
                final_multiplier REAL,
                last_checked_at TEXT,
                last_price REAL,
                last_reserve_usd REAL,
                realistic_entry_price REAL,
                realistic_realized_proceeds REAL NOT NULL DEFAULT 0.0,
                realistic_final_multiplier REAL
            )
            """
        )
        existing = {
            row[1] for row in await (await db.execute(f"PRAGMA table_info({TABLE})")).fetchall()
        }
        for name, ddl in _ADDED_COLUMNS:
            if name not in existing:
                await db.execute(f"ALTER TABLE {TABLE} ADD COLUMN {name} {ddl}")
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
    """Same extraction as ``solana_support_bounce_shadow.get_sync_filtered_candidates``
    (19/08) -- identical filter logic (only ``MIN_LIQUIDITY_USD`` differs, see
    the module docstring), see that function's own docstring for why
    ``shadow_persistent.py`` calls this directly."""
    candidates: list[TrendingPool] = []
    async with aiosqlite.connect(_db_path()) as db:
        for pool in pools:
            h1 = pool.price_change_pct.get("h1")
            if h1 is None or h1 <= MIN_H1_PCT:
                continue
            if (pool.reserve_usd or 0.0) < MIN_LIQUIDITY_USD:
                continue
            if pool.pool_created_at is None or pool.price_usd is None:
                continue
            age_minutes = (datetime.now(timezone.utc) - pool.pool_created_at).total_seconds() / 60.0
            if age_minutes < MIN_POOL_AGE_MINUTES:
                continue
            if await _has_open_signal(db, pool.pool_address, chain):
                continue
            candidates.append(pool)
    return candidates


async def record_signals(
    pools: list[TrendingPool], *, chain: str = "solana",
    candle_cache: dict[str, list] | None = None,
) -> int:
    """Same 3-pass connection-scoping discipline as the original (see its own
    docstring for the 17/08 ``database is locked`` root-cause writeup) --
    identical logic except the raised ``MIN_LIQUIDITY_USD`` and the new
    ``MAX_TOP_HOLDER_PCT`` check (applied later, once rugcheck data is
    fetched -- see below).

    ``candle_cache`` (19/08): see ``solana_support_bounce_shadow.record_signals``'s
    own docstring for the full reasoning -- the caller pre-fetches the union
    of v1+v2 candidates in one pass BEFORE either pocket runs, so both read
    the same candle for the same pool at the same instant, no freshness gap
    between the two pockets."""
    logged = 0
    try:
        await _ensure_table()
        # 18/08 -- operator decision, same as v1: TARGET_CLOSURES was a
        # statistical sample-size target, never a real capital constraint --
        # no longer caps sourcing (kept only for progress reporting).

        candidates = await get_sync_filtered_candidates(pools, chain=chain)

        rows_to_insert: list[tuple] = []
        candles_by_row: list[list] = []
        for pool in candidates:
            h1 = pool.price_change_pct.get("h1")
            candles = candle_cache.get(pool.pool_address) if candle_cache is not None else None
            if candles is None:
                try:
                    candles = await dexpaprika._fetch_one_interval(
                        pool.pool_address, chain, SUPPORT_CANDLE_INTERVAL,
                    )
                except Exception as exc:  # noqa: BLE001 -- one candidate's failure never blocks the batch
                    logger.info(
                        "solana_support_bounce_v2_shadow: candle fetch failed for %s (%s)",
                        pool.pool_address, exc,
                    )
                    continue
                if candle_cache is not None:
                    candle_cache[pool.pool_address] = candles
            if len(candles) < SUPPORT_CANDLE_COUNT:
                continue
            last_n = candles[-SUPPORT_CANDLE_COUNT:]
            range_low = min(c.low for c in last_n)
            range_high = max(c.high for c in last_n)
            if not range_low or range_high <= range_low:
                continue
            if range_high / range_low > MAX_RANGE_RATIO:
                continue

            current_price = last_n[-1].close or pool.price_usd

            distance_from_support_pct = (current_price - range_low) / (range_high - range_low) * 100.0
            if distance_from_support_pct > SUPPORT_TOLERANCE_PCT or distance_from_support_pct < 0:
                continue

            # 18/08, operator-directed exhaustive-capture pass (see
            # solana_support_bounce_shadow.py's own comment for the full
            # reasoning) -- additional readings at other window sizes,
            # computed for FREE from the same `candles` list, never used to
            # gate entry.
            distance_from_support_pct_by_n: dict[int, float | None] = {}
            for alt_n in (5, 15, 20, 30):
                if len(candles) < alt_n:
                    distance_from_support_pct_by_n[alt_n] = None
                    continue
                alt_window = candles[-alt_n:]
                alt_low = min(c.low for c in alt_window)
                alt_high = max(c.high for c in alt_window)
                if not alt_low or alt_high <= alt_low:
                    distance_from_support_pct_by_n[alt_n] = None
                else:
                    distance_from_support_pct_by_n[alt_n] = (current_price - alt_low) / (alt_high - alt_low) * 100.0

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
                        "solana_support_bounce_v2_shadow: rugcheck lookup failed for %s (%s)",
                        pool.token_address, exc,
                    )

            # 18/08, v2's own new candidate (see module docstring): FAIL-OPEN
            # on missing data (rugcheck unavailable/lookup failed never
            # blocks entry, same dome-wide best-effort doctrine as every
            # other enrichment here) -- FAIL-CLOSED only on a CONFIRMED
            # reading above the ceiling.
            if rugcheck_top_holder_pct is not None and rugcheck_top_holder_pct > MAX_TOP_HOLDER_PCT:
                continue

            realistic_entry_price = _apply_price_impact_and_fee(
                current_price, trade_size_usd=SIMULATED_TRADE_SIZE_USD,
                reserve_usd=pool.reserve_usd, side="buy",
            )

            rows_to_insert.append((
                pool.pool_address, pool.token_address, chain, pool.symbol,
                datetime.now(timezone.utc).isoformat(), current_price,
                h1, pool.reserve_usd, range_low, range_high, distance_from_support_pct,
                current_price,
                pool.pool_created_at.isoformat(),
                rugcheck_score, rugcheck_risks, rugcheck_top_holder_pct, rugcheck_creator,
                realistic_entry_price,
                pool.price_change_pct.get("m5"), pool.price_change_pct.get("h6"), pool.price_change_pct.get("h24"),
                pool.volume_usd_24h, pool.transactions_24h, pool.dex_id,
                distance_from_support_pct_by_n[5], distance_from_support_pct_by_n[15],
                distance_from_support_pct_by_n[20], distance_from_support_pct_by_n[30],
            ))
            candles_by_row.append(candles)

        if rows_to_insert:
            # 18/08, operator-directed, same pattern as the original module:
            # archive the "before" candles this entry decision was based on
            # -- per-row INSERT (not executemany) to get a real lastrowid.
            from aria_core import shadow_candle_archive

            async with aiosqlite.connect(_db_path()) as db:
                for row, candles in zip(rows_to_insert, candles_by_row):
                    cur = await db.execute(
                        f"""
                        INSERT INTO {TABLE} (
                            pool_address, token_address, chain, symbol, detected_at, entry_price,
                            h1_pct, reserve_usd, range_low_10c, range_high_10c, distance_from_support_pct,
                            remaining_qty, realized_proceeds, peak_price,
                            pool_created_at, rugcheck_score, rugcheck_risks, rugcheck_top_holder_pct,
                            rugcheck_creator, realistic_entry_price,
                            m5_pct, h6_pct, h24_pct, volume_usd_24h, transactions_24h, dex_id,
                            distance_from_support_pct_5, distance_from_support_pct_15,
                            distance_from_support_pct_20, distance_from_support_pct_30
                        ) VALUES (
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1.0, 0.0, ?, ?, ?, ?, ?, ?, ?,
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                        )
                        """,
                        row,
                    )
                    new_id = cur.lastrowid
                    await db.commit()
                    await shadow_candle_archive.store_candles(
                        module="solana_support_bounce_v2", position_id=new_id,
                        pool_address=row[0], chain=chain, phase="before", candles=candles,
                    )
            logged = len(rows_to_insert)
    except Exception as exc:  # noqa: BLE001 -- shadow logging must never break the caller
        logger.info("solana_support_bounce_v2_shadow: record_signals failed (%s)", exc)
    return logged


async def advance_exit_simulation(
    client: GeckoTerminalClient | None = None, *, chain: str = "solana", limit: int = 30,
) -> dict[str, int]:
    """Identical logic to the original -- only ``TRAILING_STOP_PCT`` differs
    (5% here vs 10%)."""
    client = client or geckoterminal_client
    counts = {"checked": 0, "closed_trailing_stop": 0, "closed_max_hold": 0, "closed_liquidity_collapse": 0}
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
                    "solana_support_bounce_v2_shadow: snapshot failed for %s (%s)", row["pool_address"], exc,
                )
                continue
            if not snapshot.available or snapshot.price_usd is None:
                continue
            counts["checked"] += 1
            current_price = snapshot.price_usd

            # 18/08, operator-directed exhaustive-capture pass (see
            # solana_support_bounce_shadow.py's own comment for the full
            # reasoning, including the DexScreener-primary-path caveat).
            from aria_core import shadow_snapshot_archive

            await shadow_snapshot_archive.store_snapshot(
                module="solana_support_bounce_v2", position_id=row["id"],
                pool_address=row["pool_address"], chain=chain,
                price_usd=snapshot.price_usd, reserve_usd=snapshot.reserve_usd,
                dex_id=snapshot.dex_id, price_change_pct=snapshot.price_change_pct,
                transactions=snapshot.transactions, volume_usd=snapshot.volume_usd,
            )

            # 18/08 -- same window-detection fix as the original module (see
            # its own docstring for the 2 real live-bug precedents this
            # closes). Fill price stays the theoretical threshold, unchanged.
            window_high = current_price
            window_low = current_price
            try:
                ohlcv: OHLCVResult = await client.get_ohlcv(
                    row["pool_address"], network=chain, mode="scalping_5m",
                )
            except Exception as exc:  # noqa: BLE001 -- OHLCV is an enhancement, never a hard requirement
                logger.info(
                    "solana_support_bounce_v2_shadow: advance_exit_simulation get_ohlcv failed for %s (%s)",
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
                        module="solana_support_bounce_v2", position_id=row["id"],
                        pool_address=row["pool_address"], chain=chain, phase="after",
                        candles=new_candles,
                    )
            effective_high = max(window_high, current_price)
            effective_low = min(window_low, current_price)

            peak_price = row["peak_price"] or entry_price
            peak_price = max(peak_price, effective_high)
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

            is_pumpswap = snapshot.dex_id == "pumpswap"
            entry_reserve = row.get("reserve_usd")
            liquidity_collapsed = (
                not is_pumpswap
                and entry_reserve is not None and entry_reserve > 0
                and snapshot.reserve_usd is not None
                and snapshot.reserve_usd < entry_reserve * (1 - LIQUIDITY_COLLAPSE_EXIT_PCT / 100.0)
            )
            trailing_stop_hit = effective_low <= peak_price * (1 - TRAILING_STOP_PCT / 100.0)

            exit_reason: str | None = None
            if liquidity_collapsed:
                _realistic_sell(remaining_qty, current_price)
                realized_proceeds += remaining_qty * current_price
                remaining_qty = 0.0
                exit_reason = "liquidity_collapse"
            elif trailing_stop_hit:
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
                        peak_price = ?, remaining_qty = ?, realized_proceeds = ?, exit_reason = ?,
                        final_multiplier = ?, last_checked_at = ?, last_price = ?,
                        realistic_realized_proceeds = ?, realistic_final_multiplier = ?, last_reserve_usd = ?
                    WHERE id = ?
                    """,
                    (
                        peak_price, remaining_qty, realized_proceeds, exit_reason, final_multiplier,
                        datetime.now(timezone.utc).isoformat(), current_price,
                        realistic_realized_proceeds, realistic_final_multiplier, snapshot.reserve_usd,
                        row["id"],
                    ),
                )
                await db.commit()

            if exit_reason == "trailing_stop":
                counts["closed_trailing_stop"] += 1
            elif exit_reason == "max_hold":
                counts["closed_max_hold"] += 1
            elif exit_reason == "liquidity_collapse":
                counts["closed_liquidity_collapse"] += 1
    except Exception as exc:  # noqa: BLE001 -- shadow simulation must never raise into a caller
        logger.info("solana_support_bounce_v2_shadow: advance_exit_simulation failed (%s)", exc)
    return counts


async def chain_pnl_summary_realistic(chain: str = "solana") -> dict:
    """Identical to the original -- see its own docstring for the full
    liquidity-aware PnL doctrine."""
    await _ensure_table()
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            f"SELECT realistic_entry_price, remaining_qty, realistic_realized_proceeds, "
            f"realistic_final_multiplier, last_price, exit_reason FROM {TABLE} WHERE chain = ?",
            (chain,),
        )
        rows = [dict(r) for r in await cur.fetchall()]

    total_pnl_units = 0.0
    closed = 0
    open_valued = 0
    pending_price = 0
    unreachable_liquidity = 0
    stranded = 0
    for r in rows:
        entry = r["realistic_entry_price"]
        if entry is None:
            unreachable_liquidity += 1
            continue
        if r["exit_reason"] is not None:
            if r["realistic_final_multiplier"] is not None:
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
        "target": TARGET_CLOSURES,
        "wins": wins,
        "win_rate": (wins / len(rows)) if rows else None,
        "avg_multiplier": (sum(r["final_multiplier"] for r in rows) / len(rows)) if rows else None,
        "by_exit_reason": by_exit_reason,
    }
