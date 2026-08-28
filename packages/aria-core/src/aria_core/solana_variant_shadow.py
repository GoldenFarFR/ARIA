"""Multi-variant Solana shadow (17/08, operator-directed) -- three parallel
entry-threshold experiments (m5 >= 5% / 10% / 15%, all sharing the same
5000$ liquidity floor at entry), each variant independently deciding
whether a candidate qualifies. Logs to its own table, distinct from
``solana_pump_shadow.py``'s ongoing 20-240min age-window test -- the two run
fully independently, never sharing state, so neither test contaminates the
other's sample.

**Needs its OWN discovery call, cannot reuse solana_pump_shadow.py's**
(17/08, caught before wiring): that module's DexPaprika call passes
``min_price_change_5m=M5_SURGE_THRESHOLD_PCT`` (25.0) straight to the API,
so its ``result.pools`` never contains anything below 25% m5 -- reusing it
here would silently starve the 5%/10%/15% variants of exactly the candidates
between their own thresholds and 25% that the experiment exists to test.
The caller (``shadow_persistent.py``) makes a second, separate
``dexpaprika.get_trending_pools`` call with ``min_price_change_5m=5.0`` (this
module's lowest variant threshold) and passes its pools to
``record_signals`` here.

**Exit mechanics deliberately DROP the trailing stop** (operator-directed):
only the scale-out ladder, ``liquidity_collapse``, and ``max_hold`` survive
as exits. Goal: isolate whether ``TRAILING_STOP_PCT`` itself was cutting
winners short, independent of the entry-threshold question tested here.
Without a price-based floor, a position that never triggers a scale-out
rung and never crosses the liquidity/age exits could in principle sit at a
large unrealized loss until ``max_hold`` -- an explicit, operator-approved
tradeoff for this experiment, not an oversight (the two remaining exits are
NOT stop-losses: one guards against an unsellable pool, the other is a pure
time limit).

Same bright-line doctrine as ``solana_pump_shadow.py``: never trades real or
paper capital, never wired to the heartbeat, pure observation. Reuses that
module's price-impact/fee simulation and DexScreener-primary snapshot
fallback rather than re-deriving them -- same math, same doctrine, one
source of truth.

Target: 50 closures PER VARIANT (150 total) before drawing any conclusion --
same anti-overfitting posture as every other shadow module here."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import aiosqlite

from aria_core import shadow_pocket_cap
from aria_core.paths import shadow_db_path
from aria_core.services import rugcheck
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

TABLE = "solana_variant_shadow_log"

# Each variant is independently evaluated against the SAME discovered pool
# list every cycle -- a token can qualify for more than one variant at once
# (e.g. a +18% m5 move qualifies m5_5pct AND m5_10pct but not m5_15pct),
# each logging its OWN row. This is deliberate: it lets the three variants
# be compared on overlapping/identical real candidates rather than on
# whatever the market happened to produce for each independently.
VARIANTS: dict[str, dict[str, float]] = {
    "m5_5pct": {"m5_threshold_pct": 5.0, "min_liquidity_usd": 5000.0},
    "m5_10pct": {"m5_threshold_pct": 10.0, "min_liquidity_usd": 5000.0},
    "m5_15pct": {"m5_threshold_pct": 15.0, "min_liquidity_usd": 5000.0},
}
TARGET_CLOSURES_PER_VARIANT = 50

# Age window -- deliberately the ORIGINAL, already-validated 20-120min band
# (not the 20-240min currently under test in solana_pump_shadow.py). This
# experiment already varies entry threshold, liquidity floor, and exit
# mechanics at once (operator-directed) -- holding age constant at a known
# value avoids piling on a FOURTH simultaneous variable.
MIN_POOL_AGE_MINUTES = 20.0
MAX_POOL_AGE_MINUTES = 120.0

# Exit mechanics -- scale-out ladder identical to solana_pump_shadow.py.
# TRAILING_STOP_PCT deliberately does not exist in this module.
SCALE_OUT_STEP_PCT = 25.0
SCALE_OUT_SELL_FRACTION = 0.25
MAX_HOLD_MINUTES = 120.0
LIQUIDITY_COLLAPSE_EXIT_PCT = 50.0
_SCALE_OUT_DUST_FRACTION = 0.01

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
                variant TEXT NOT NULL,
                pool_address TEXT NOT NULL,
                token_address TEXT,
                chain TEXT NOT NULL DEFAULT 'solana',
                symbol TEXT,
                detected_at TEXT NOT NULL,
                entry_price REAL NOT NULL,
                m5_pct REAL,
                reserve_usd REAL,
                pool_created_at TEXT,
                rugcheck_score INTEGER,
                rugcheck_risks TEXT,
                rugcheck_top_holder_pct REAL,
                rugcheck_creator TEXT,
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
                realistic_final_multiplier REAL
            )
            """
        )
        await db.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_lookup ON {TABLE} (variant, pool_address, chain, exit_reason)"
        )
        await db.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_detected_at ON {TABLE} (detected_at)"
        )
        await db.commit()
    _ensured_db_paths.add(path)


async def _has_open_signal(db: aiosqlite.Connection, variant: str, pool_address: str, chain: str) -> bool:
    cur = await db.execute(
        f"SELECT 1 FROM {TABLE} WHERE variant = ? AND pool_address = ? AND chain = ? "
        "AND exit_reason IS NULL LIMIT 1",
        (variant, pool_address, chain),
    )
    return (await cur.fetchone()) is not None


async def closures_so_far(variant: str) -> int:
    await _ensure_table()
    async with aiosqlite.connect(_db_path()) as db:
        cur = await db.execute(
            f"SELECT COUNT(*) FROM {TABLE} WHERE variant = ? AND exit_reason IS NOT NULL", (variant,),
        )
        (count,) = await cur.fetchone()
    return count


async def record_signals(pools: list[TrendingPool], *, chain: str = "solana") -> int:
    """One discovery pass shared by all three variants -- each pool is
    checked against EVERY variant's own threshold/liquidity pair, logging
    one independent row per variant that qualifies (never a single shared
    row: the three variants must be free to diverge in exit outcome even
    when they open on the exact same pool). A variant that has already
    reached its ``TARGET_CLOSURES_PER_VARIANT`` stops opening new positions
    (still allowed to log the observation? No -- unlike
    ``solana_pump_shadow.py``'s sourcing-stays-unfiltered doctrine, this
    module's whole purpose is a bounded 50-closure comparison per variant;
    once a variant hits its target, further signals for it are skipped
    entirely, never logged, to keep the three samples exactly comparable in
    size)."""
    logged = 0
    try:
        await _ensure_table()
        variant_done = {v: (await closures_so_far(v)) >= TARGET_CLOSURES_PER_VARIANT for v in VARIANTS}
        if all(variant_done.values()):
            return 0
        async with aiosqlite.connect(_db_path()) as db:
            for pool in pools:
                m5 = pool.price_change_pct.get("m5")
                if m5 is None or pool.price_usd is None or pool.pool_created_at is None:
                    continue
                pool_age_minutes = (datetime.now(timezone.utc) - pool.pool_created_at).total_seconds() / 60.0
                if not (MIN_POOL_AGE_MINUTES <= pool_age_minutes < MAX_POOL_AGE_MINUTES):
                    continue

                qualifying_variants = [
                    name for name, cfg in VARIANTS.items()
                    if not variant_done[name]
                    and m5 >= cfg["m5_threshold_pct"]
                    and (pool.reserve_usd or 0.0) >= cfg["min_liquidity_usd"]
                ]
                if not qualifying_variants:
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
                        logger.info("solana_variant_shadow: rugcheck lookup failed for %s (%s)", pool.token_address, exc)

                realistic_entry_price = _apply_price_impact_and_fee(
                    pool.price_usd, trade_size_usd=SIMULATED_TRADE_SIZE_USD,
                    reserve_usd=pool.reserve_usd, side="buy",
                )
                first_scale_level = pool.price_usd * (1 + SCALE_OUT_STEP_PCT / 100.0)

                for variant in qualifying_variants:
                    if await _has_open_signal(db, variant, pool.pool_address, chain):
                        continue
                    # 28/08 -- shadow-wide resource cap, see shadow_pocket_cap.py's
                    # module docstring. Never a trading gate: purely a ceiling on
                    # how many concurrent open positions this pocket may hold.
                    # Scoped per VARIANT (like this module's own
                    # closures_so_far/TARGET_CLOSURES_PER_VARIANT), never the
                    # whole table -- each variant is its own independent
                    # comparison arm with its own subscription cost.
                    if await shadow_pocket_cap.at_capacity(
                        db, TABLE, open_clause="variant = ? AND exit_reason IS NULL", params=(variant,)
                    ):
                        continue
                    await db.execute(
                        f"""
                        INSERT INTO {TABLE} (
                            variant, pool_address, token_address, chain, symbol,
                            detected_at, entry_price, m5_pct, reserve_usd,
                            remaining_qty, realized_proceeds, peak_price, next_scale_level,
                            pool_created_at, rugcheck_score, rugcheck_risks, rugcheck_top_holder_pct,
                            rugcheck_creator, realistic_entry_price
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1.0, 0.0, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            variant, pool.pool_address, pool.token_address, chain, pool.symbol,
                            datetime.now(timezone.utc).isoformat(), pool.price_usd, m5, pool.reserve_usd,
                            pool.price_usd, first_scale_level,
                            pool.pool_created_at.isoformat(),
                            rugcheck_score, rugcheck_risks, rugcheck_top_holder_pct, rugcheck_creator,
                            realistic_entry_price,
                        ),
                    )
                    logged += 1
            await db.commit()
    except Exception as exc:  # noqa: BLE001 -- shadow logging must never break the caller
        logger.info("solana_variant_shadow: record_signals failed (%s)", exc)
    return logged


async def advance_exit_simulation(
    client: GeckoTerminalClient | None = None, *, chain: str = "solana", limit: int = 30,
) -> dict[str, int]:
    """Same calibrated-exit doctrine as ``solana_pump_shadow.py``'s own
    ``advance_exit_simulation`` (scale-out ladder, limit-order fill
    semantics, fail-open on unknown data) MINUS the trailing stop, per this
    module's whole point. Priority order: liquidity_collapse first (an
    already-validated, highest-priority exit, unrelated to price), then the
    scale-out ladder, then max_hold as the final catch-all. No age_limit
    exit here -- ``MAX_POOL_AGE_MINUTES`` in this module only gates
    DISCOVERY (a pool already past 120min at detection is never logged),
    it never force-closes an already-open row the way
    ``solana_pump_shadow.py``'s DIFFERENT 20-240min measurement window
    needs to."""
    client = client or geckoterminal_client
    counts = {
        "checked": 0, "scale_out_fills": 0, "closed_scale_out_complete": 0,
        "closed_max_hold": 0, "closed_liquidity_collapse": 0,
    }
    try:
        await _ensure_table()
        async with aiosqlite.connect(_db_path()) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                f"SELECT * FROM {TABLE} WHERE chain = ? AND exit_reason IS NULL "
                "ORDER BY detected_at ASC LIMIT ?",
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
                    "solana_variant_shadow: snapshot failed for %s/%s (%s)",
                    row["variant"], row["pool_address"], exc,
                )
                continue
            if not snapshot.available or snapshot.price_usd is None:
                continue
            counts["checked"] += 1
            current_price = snapshot.price_usd

            peak_price = row["peak_price"] or entry_price
            peak_price = max(peak_price, current_price)
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

            entry_reserve = row.get("reserve_usd")
            # 17/08, real bug found live (EYE): PumpSwap pools report
            # near-zero reserve from both DexScreener and GeckoTerminal
            # regardless of real liquidity -- see solana_pump_shadow.py's
            # identical guard for the full root-cause writeup. Disabled here
            # too, not just an edge case for this pool type.
            is_pumpswap = snapshot.dex_id == "pumpswap"
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
                while remaining_qty > _SCALE_OUT_DUST_FRACTION and current_price >= next_scale_level:
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
                        peak_price = ?, next_scale_level = ?, remaining_qty = ?,
                        realized_proceeds = ?, exit_reason = ?, final_multiplier = ?,
                        last_checked_at = ?, last_price = ?,
                        realistic_realized_proceeds = ?, realistic_final_multiplier = ?,
                        last_reserve_usd = ?
                    WHERE id = ?
                    """,
                    (
                        peak_price, next_scale_level, remaining_qty,
                        realized_proceeds, exit_reason, final_multiplier,
                        datetime.now(timezone.utc).isoformat(), current_price,
                        realistic_realized_proceeds, realistic_final_multiplier,
                        snapshot.reserve_usd, row["id"],
                    ),
                )
                await db.commit()

            if exit_reason == "scale_out_complete":
                counts["closed_scale_out_complete"] += 1
            elif exit_reason == "max_hold":
                counts["closed_max_hold"] += 1
            elif exit_reason == "liquidity_collapse":
                counts["closed_liquidity_collapse"] += 1
    except Exception as exc:  # noqa: BLE001 -- shadow simulation must never raise into a caller
        logger.info("solana_variant_shadow: advance_exit_simulation failed (%s)", exc)
    return counts


async def variant_summary(variant: str, *, chain: str = "solana") -> dict:
    """Per-variant win rate/avg multiplier, computed only over rows whose
    exit simulation actually completed -- never estimated from an open
    position. Mirrors ``solana_pump_shadow.exit_simulation_summary``'s
    convention (win = final_multiplier > 1.0)."""
    await _ensure_table()
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            f"SELECT exit_reason, final_multiplier FROM {TABLE} "
            "WHERE variant = ? AND chain = ? AND final_multiplier IS NOT NULL",
            (variant, chain),
        )
        rows = [dict(r) for r in await cur.fetchall()]
    wins = sum(1 for r in rows if r["final_multiplier"] > 1.0)
    by_exit_reason: dict[str, int] = {}
    for r in rows:
        by_exit_reason[r["exit_reason"]] = by_exit_reason.get(r["exit_reason"], 0) + 1
    return {
        "variant": variant,
        "completed": len(rows),
        "wins": wins,
        "win_rate": (wins / len(rows)) if rows else None,
        "avg_multiplier": (sum(r["final_multiplier"] for r in rows) / len(rows)) if rows else None,
        "by_exit_reason": by_exit_reason,
    }
