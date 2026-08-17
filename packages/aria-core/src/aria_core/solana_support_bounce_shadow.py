"""Support-bounce Solana shadow (17/08, operator-directed) -- a single new
strategy, replacing the 3-variant m5-threshold experiment: buy an
ESTABLISHED pool (age >= 70min, no upper bound -- deliberately testing
older pools after the HAROLD/EYE observation that age alone doesn't
separate outcomes, but age combined with low holder concentration might)
that is net UP over the last hour (h1 > 0%) but has pulled back close to
the LOW of its own recent 10-candle (5min each, 50min lookback) range --
a mean-reversion / "buy the dip within an uptrend" entry, deliberately
the opposite of the m5-surge momentum entries used everywhere else in this
project.

**Support tolerance is a first guess, not calibrated** (operator: "20% pour
commencer mais il faut un log avec des donnees pour voir si on peut le
calibrer mieux") -- every logged row stores the REAL distance from the
10-candle low at entry (``distance_from_support_pct``), even though only
rows within ``SUPPORT_TOLERANCE_PCT`` ever get logged at all right now.
Once enough real outcomes accumulate, a future pass can look at whether
tighter/looser tolerance would have performed better, using this column.

**Exit mechanics, deliberately simple, no scale-out ladder**: a single
-10% trailing stop from the peak price since entry (looser than the -20%
used elsewhere is WRONG -- this is TIGHTER, 10 vs 20 -- operator-specified
"stop loss suiveur -10%"), full-position exit when it fires (never partial).
``liquidity_collapse`` and ``max_hold`` (2h) survive as the two safety nets,
same doctrine and same PumpSwap-aware guard as every other shadow module in
this dome (see solana_pump_shadow.py's own comment for the full root-cause
writeup on why PumpSwap pools misreport reserve).

Same bright-line doctrine as every other module here: never trades real or
paper capital, never wired to the heartbeat, pure observation. Reuses
solana_pump_shadow.py's price-impact/fee simulation and DexScreener-primary
snapshot fallback rather than re-deriving them.

Target: 50 closures before drawing any conclusion, same anti-overfitting
posture as every other shadow module here."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import aiosqlite

from aria_core.paths import shadow_db_path
from aria_core.services import dexpaprika, rugcheck
from aria_core.services.geckoterminal import GeckoTerminalClient, PoolSnapshot, TrendingPool, geckoterminal_client
from aria_core.solana_pump_shadow import (
    DEX_FEE_PCT,
    SIMULATED_TRADE_SIZE_USD,
    _apply_price_impact_and_fee,
    _minutes_since,
    _snapshot_with_fallback,
)

logger = logging.getLogger(__name__)

DB_PATH = str(shadow_db_path())

TABLE = "solana_support_bounce_shadow_log"

# Entry criteria (operator-specified 17/08)
MIN_H1_PCT = 0.0
MIN_LIQUIDITY_USD = 5000.0
MIN_POOL_AGE_MINUTES = 70.0  # no upper bound, deliberately
SUPPORT_TOLERANCE_PCT = 20.0  # first guess, see module docstring -- to recalibrate
SUPPORT_CANDLE_COUNT = 10
SUPPORT_CANDLE_INTERVAL = "5m"

# Exit mechanics (operator-specified: "stop loss suiveur -10%", "aucun palier")
TRAILING_STOP_PCT = 10.0
MAX_HOLD_MINUTES = 120.0
LIQUIDITY_COLLAPSE_EXIT_PCT = 50.0

TARGET_CLOSURES = 50

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


async def record_signals(pools: list[TrendingPool], *, chain: str = "solana") -> int:
    """Each candidate must pass, in order: h1 > 0% (should already be true --
    the caller is expected to have used ``dexpaprika.get_trending_pools``
    with ``order_by="price_change_percentage_1h"``, this is a defensive
    re-check, never trusted blindly), liquidity floor, age floor (no
    ceiling). Only candidates that clear ALL THREE get the extra OHLCV call
    to check the support condition -- keeps the real cost proportional to
    genuinely plausible candidates, not the full fetched list.

    **17/08, real bug found live** (recurring ``database is locked`` in
    ``shadow_persistent.py``'s exit-tracking loop right as this function was
    mid-batch) -- the SQLite connection used to stay open for the WHOLE
    candidate loop, including every slow network call (candle fetch under
    DexPaprika contention/retries, rugcheck lookup) in between. Under real
    DexPaprika rate-limiting each candidate's candle fetch can take many
    seconds (3 retries with backoff), so a batch of a dozen+ candidates held
    the write connection open for minutes -- long enough to collide with
    ``exit_tracking_loop``'s own writes on the same ``shadow.db`` file every
    60s. Fixed by splitting into three passes: (1) cheap synchronous filters
    + a SHORT dedup-check connection per candidate, (2) all network
    enrichment (candle fetch, rugcheck) with NO connection open at all, (3) a
    single short connection at the end for every INSERT, batched together."""
    logged = 0
    try:
        await _ensure_table()
        if (await closures_so_far()) >= TARGET_CLOSURES:
            return 0

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

        rows_to_insert: list[tuple] = []
        for pool in candidates:
            h1 = pool.price_change_pct.get("h1")
            try:
                candles = await dexpaprika._fetch_one_interval(
                    pool.pool_address, chain, SUPPORT_CANDLE_INTERVAL,
                )
            except Exception as exc:  # noqa: BLE001 -- one candidate's failure never blocks the batch
                logger.info(
                    "solana_support_bounce_shadow: candle fetch failed for %s (%s)",
                    pool.pool_address, exc,
                )
                continue
            if len(candles) < SUPPORT_CANDLE_COUNT:
                continue
            last_n = candles[-SUPPORT_CANDLE_COUNT:]
            range_low = min(c.low for c in last_n)
            range_high = max(c.high for c in last_n)
            if not range_low:
                continue
            distance_from_support_pct = (pool.price_usd / range_low - 1) * 100.0
            if distance_from_support_pct > SUPPORT_TOLERANCE_PCT:
                continue

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
                        "solana_support_bounce_shadow: rugcheck lookup failed for %s (%s)",
                        pool.token_address, exc,
                    )

            realistic_entry_price = _apply_price_impact_and_fee(
                pool.price_usd, trade_size_usd=SIMULATED_TRADE_SIZE_USD,
                reserve_usd=pool.reserve_usd, side="buy",
            )

            rows_to_insert.append((
                pool.pool_address, pool.token_address, chain, pool.symbol,
                datetime.now(timezone.utc).isoformat(), pool.price_usd,
                h1, pool.reserve_usd, range_low, range_high, distance_from_support_pct,
                pool.price_usd,
                pool.pool_created_at.isoformat(),
                rugcheck_score, rugcheck_risks, rugcheck_top_holder_pct, rugcheck_creator,
                realistic_entry_price,
            ))

        if rows_to_insert:
            async with aiosqlite.connect(_db_path()) as db:
                await db.executemany(
                    f"""
                    INSERT INTO {TABLE} (
                        pool_address, token_address, chain, symbol, detected_at, entry_price,
                        h1_pct, reserve_usd, range_low_10c, range_high_10c, distance_from_support_pct,
                        remaining_qty, realized_proceeds, peak_price,
                        pool_created_at, rugcheck_score, rugcheck_risks, rugcheck_top_holder_pct,
                        rugcheck_creator, realistic_entry_price
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1.0, 0.0, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    rows_to_insert,
                )
                await db.commit()
            logged = len(rows_to_insert)
    except Exception as exc:  # noqa: BLE001 -- shadow logging must never break the caller
        logger.info("solana_support_bounce_shadow: record_signals failed (%s)", exc)
    return logged


async def advance_exit_simulation(
    client: GeckoTerminalClient | None = None, *, chain: str = "solana", limit: int = 30,
) -> dict[str, int]:
    """No scale-out ladder -- only two possible closes: ``trailing_stop``
    (-10% from the running peak, FULL position, never partial) or the two
    safety nets (``liquidity_collapse``, ``max_hold``). Priority order:
    liquidity_collapse first (unrelated to price, protects against an
    unsellable pool), then the trailing stop, then max_hold as the final
    catch-all. Same PumpSwap dex_id guard as every other module here --
    see solana_pump_shadow.py's comment for the full root-cause writeup."""
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
                    "solana_support_bounce_shadow: snapshot failed for %s (%s)", row["pool_address"], exc,
                )
                continue
            if not snapshot.available or snapshot.price_usd is None:
                continue
            counts["checked"] += 1
            current_price = snapshot.price_usd

            peak_price = row["peak_price"] or entry_price
            peak_price = max(peak_price, current_price)
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
            trailing_stop_hit = current_price <= peak_price * (1 - TRAILING_STOP_PCT / 100.0)

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
        logger.info("solana_support_bounce_shadow: advance_exit_simulation failed (%s)", exc)
    return counts


async def chain_pnl_summary_realistic(chain: str = "solana") -> dict:
    """17/08, real gap found live by the operator ("sur bounce je ne vois
    pas le pnl") -- this pocket's notify functions mirrored the retired
    3-variant experiment's pattern (progress/winrate only, no dollar PnL),
    which was fine when a main pocket already showed the $ figure elsewhere.
    Now that support-bounce is the ONLY active pocket, that gap is real.
    Same aggregate as solana_pump_shadow.chain_pnl_summary_realistic,
    ported here since this table carries the same realistic_* columns
    (liquidity-aware: a row whose realistic_entry_price is NULL means the
    entry itself was already too shallow to fill a SIMULATED_TRADE_SIZE_USD
    trade -- counted in unreachable_liquidity, never silently dropped)."""
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
                # Bought-then-stranded capital (pool drained mid-flight) is a
                # LOSS, never an unmeasurable event -- see solana_pump_shadow's
                # own comment for the full survivorship-bias writeup this
                # guards against.
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
