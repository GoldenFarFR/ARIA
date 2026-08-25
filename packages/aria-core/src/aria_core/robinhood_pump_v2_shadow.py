"""Robinhood Chain pump shadow, AGGRESSIVE scale-out variant (v2, 25/08).

specs/004-shadow-robinhood T005: a live, forward-only test of a more
aggressive profit-taking schedule, built after the operator's explicit
degen-trading framing ("arrete de penser comme une IA... pense un peu comme
un degen derriere son ecran qui trade les memecoins" / "tu as qu'a simuler en
direct sur des nouveaux lancements, tu as l'infra pour le faire"). v1
(``robinhood_pump_shadow.py``) sells 25% of the remaining position at every
+25% rung above entry. A real degen trader secures a much bigger share of the
stake much sooner, before a FOMO-driven pump reverses, then treats whatever
is left as a disposable "moonbag" -- this module tests exactly that: sell
50% of remaining at every +15% rung (``SCALE_OUT_STEP_PCT``/
``SCALE_OUT_SELL_FRACTION`` below), everything else (entry filters, trailing
stop, max-hold, liquidity-collapse safety net) identical to v1.

**Why a full separate module, not a parameter added to v1's own functions**:
v1's headline PnL is CURRENTLY under active dispute (specs/004-shadow-
robinhood T001/T004 -- 2 confirmed price artifacts, verification still
incomplete). CLAUDE.md's own doctrine: never stack a second change onto a
mechanism whose effect is still being measured, the first change becomes
unattributable. Same precedent already established for exactly this
situation: ``solana_support_bounce_v2_shadow.py`` (a full separate file, own
table, same discovery feed, never touching v1's ongoing sample). This module
follows it: reads the SAME already-fetched ``pools`` list v1's own discovery
loop already pulls (``dexpaprika.get_trending_pools``, no second network
call), applies the IDENTICAL entry filters (age/liquidity/RWA-exclusion/
regime, reused verbatim from v1 -- never restated), but opens and tracks its
OWN independent position ledger (``robinhood_pump_v2_shadow_log``). The same
pool can be open in both v1 and v2 simultaneously -- two independent
hypothetical fills on the same real signal, never cross-referenced.

**Deliberately simplified vs v1's exit-tracking, an honest scope choice**: v1
additionally reads GeckoTerminal OHLCV candles every pass to refine the
scale-out/trailing-stop thresholds against the WINDOW high/low, not just a
point-sample spot (see v1's own docstring, "Honest scope limit, pass 3").
This module skips that refinement and uses the spot-price cascade alone
(``robinhood_pump_shadow._snapshot_with_fallback``: on-chain WS feed first,
DexScreener second, GeckoTerminal only as the last resort) -- a smaller
GeckoTerminal footprint, consistent with the operator's explicit correction
("je t'ai dit de pas utiliser gecko, ca fonctionne mal"), and proportionate
since the variable under test here is the SCALE-OUT SCHEDULE, not exit-price
precision. Corollary, also honest: no OHLCV candles are fetched by this
module at all, so unlike every other shadow module's standing convention this
one does NOT wire ``shadow_candle_archive`` (nothing to archive without an
extra network call this module deliberately avoids) -- revisit if this
variant survives long enough to be worth the extra cost.

Never trades, never opens a real position -- same absolute bright lines as
v1 (see its own module docstring): never calls ``paper_trader.open_position``
or anything that could move real capital, never wired into ``heartbeat.py``
by this change (a plain async function, called from ``shadow_persistent.py``
same as v1)."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import aiosqlite

from aria_core import shadow_discovery_only
from aria_core.paper_trader import _advance_high_water
from aria_core.paths import shadow_db_path
from aria_core.robinhood_pump_shadow import (
    LIQUIDITY_COLLAPSE_EXIT_PCT,
    MAX_POOL_AGE_MINUTES,
    MIN_LIQUIDITY_USD,
    M5_SURGE_THRESHOLD_PCT,
    SIMULATED_TRADE_SIZE_USD,
    _PEAK_JUMP_SUSPECT_RATIO,
    _SCALE_OUT_DUST_FRACTION,
    _apply_price_impact_and_fee,
    _minutes_since,
    _snapshot_with_fallback,
    record_regime_candidate,
    regime_state,
)
from aria_core.services.evm_swap_ws import EVMSwapWebSocketFeed
from aria_core.services.geckoterminal import (
    GeckoTerminalClient,
    PoolSnapshot,
    TrendingPool,
    geckoterminal_client,
)
from aria_core.services.robinhood_stock_tokens import is_stock_token
from aria_core.skills import chain_liquidity_regime

logger = logging.getLogger(__name__)

# Same standalone shadow.db as v1 (see shadow_db_path's own docstring) -- a
# distinct TABLE, not a distinct database.
DB_PATH = str(shadow_db_path())

TABLE = "robinhood_pump_v2_shadow_log"

# The ONE variable under test in this module -- everything else (entry
# filters, trailing stop, max-hold, liquidity floor) is reused verbatim from
# v1 above, imported rather than restated.
SCALE_OUT_STEP_PCT = 15.0  # each new rung is +15% above the PREVIOUS rung (v1: +25%)
SCALE_OUT_SELL_FRACTION = 0.5  # sell 50% of the REMAINING position at each rung (v1: 25%)
TRAILING_STOP_PCT = 20.0  # unchanged from v1 -- same safety net, not the variable under test
MAX_HOLD_MINUTES = 120  # unchanged from v1 (its own h2 horizon)

_ADDED_COLUMNS: list[tuple[str, str]] = [
    ("last_checked_at", "TEXT"),
    ("last_price", "REAL"),
    ("last_reserve_usd", "REAL"),
    ("realistic_entry_price", "REAL"),
    ("realistic_realized_proceeds", "REAL NOT NULL DEFAULT 0.0"),
    ("realistic_final_multiplier", "REAL"),
    ("dex_id", "TEXT"),
    ("pending_peak_price", "REAL"),
    ("pending_peak_since", "TEXT"),
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
                chain TEXT NOT NULL DEFAULT 'robinhood',
                symbol TEXT,
                detected_at TEXT NOT NULL,
                entry_price REAL NOT NULL,
                reserve_usd REAL,
                pool_created_at TEXT,
                closed_at TEXT,
                remaining_qty REAL NOT NULL DEFAULT 1.0,
                realized_proceeds REAL NOT NULL DEFAULT 0.0,
                peak_price REAL,
                next_scale_level REAL,
                exit_reason TEXT,
                final_multiplier REAL
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
        await db.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_detected_at ON {TABLE} (detected_at)"
        )
        await db.commit()
    _ensured_db_paths.add(path)


async def _has_open_signal(db: aiosqlite.Connection, pool_address: str, chain: str) -> bool:
    cur = await db.execute(
        f"SELECT 1 FROM {TABLE} WHERE pool_address = ? AND chain = ? AND exit_reason IS NULL LIMIT 1",
        (pool_address, chain),
    )
    return await cur.fetchone() is not None


async def record_signals(
    pools: list[TrendingPool], *, chain: str = "robinhood", entry_mode: str = "m5_surge",
) -> int:
    """Same entry filters as ``robinhood_pump_shadow.record_signals`` (age,
    liquidity floor, RWA exclusion, chain regime, discovery-only gate),
    reused verbatim -- the two modules must agree on WHAT counts as a
    signal, only WHAT THEY DO with it once opened differs. Called with the
    SAME already-fetched ``pools`` list v1's own discovery loop uses, never a
    second ``get_trending_pools``/``get_trending_pools``-equivalent call.

    ``entry_mode`` (25/08, specs/006-onchain-dayzero-entry): see
    ``robinhood_pump_shadow.record_signals``'s own docstring -- same bypass,
    same reasoning."""
    logged = 0
    try:
        await _ensure_table()
        async with aiosqlite.connect(_db_path()) as db:
            for pool in pools:
                m5 = pool.price_change_pct.get("m5")
                if entry_mode == "day_zero":
                    m5 = M5_SURGE_THRESHOLD_PCT
                if m5 is None or m5 < M5_SURGE_THRESHOLD_PCT:
                    continue
                if pool.price_usd is None:
                    continue
                if pool.pool_created_at is None:
                    continue
                pool_age_minutes = (
                    datetime.now(timezone.utc) - pool.pool_created_at
                ).total_seconds() / 60.0
                if pool_age_minutes >= MAX_POOL_AGE_MINUTES:
                    continue
                if pool.reserve_usd is None or pool.reserve_usd < MIN_LIQUIDITY_USD:
                    continue
                try:
                    if await is_stock_token(pool.token_address or "", chain):
                        continue
                except Exception as exc:  # noqa: BLE001 -- the RWA filter must never break the log pass
                    logger.info(
                        "robinhood_pump_v2_shadow: is_stock_token check failed for %s (%s)",
                        pool.token_address, exc,
                    )
                if await _has_open_signal(db, pool.pool_address, chain):
                    continue

                # Chain-level regime gate/candidate tracking is SHARED with v1
                # (same table, same chain-wide state) -- reused, never a
                # second independent regime mechanism for the same chain.
                await record_regime_candidate(
                    pool_address=pool.pool_address, mint=pool.token_address or "",
                    chain=chain, entry_price=pool.price_usd, reserve_usd=pool.reserve_usd,
                )
                if not (await regime_state())["open"]:
                    continue

                chain_regime = await chain_liquidity_regime.latest_regime(chain)
                if chain_regime and chain_regime["regime"] == chain_liquidity_regime.REGIME_TOXIC_SPIKE:
                    continue

                if shadow_discovery_only.is_discovery_only():
                    continue

                first_scale_level = pool.price_usd * (1 + SCALE_OUT_STEP_PCT / 100.0)
                realistic_entry_price = _apply_price_impact_and_fee(
                    pool.price_usd, trade_size_usd=SIMULATED_TRADE_SIZE_USD,
                    reserve_usd=pool.reserve_usd, side="buy",
                )
                await db.execute(
                    f"""
                    INSERT INTO {TABLE} (
                        pool_address, token_address, chain, symbol, detected_at, entry_price,
                        reserve_usd, pool_created_at, next_scale_level, realistic_entry_price, dex_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        pool.pool_address, pool.token_address, chain, pool.symbol,
                        datetime.now(timezone.utc).isoformat(), pool.price_usd,
                        pool.reserve_usd, pool.pool_created_at.isoformat(), first_scale_level,
                        realistic_entry_price, pool.dex_id,
                    ),
                )
                logged += 1
            await db.commit()
    except Exception as exc:  # noqa: BLE001 -- a logging pass must never raise into the caller
        logger.info("robinhood_pump_v2_shadow: record_signals failed (%s)", exc)
    return logged


async def advance_exit_simulation(
    client: GeckoTerminalClient | None = None, *, chain: str = "robinhood", limit: int = 50,
    ws_feed: EVMSwapWebSocketFeed | None = None,
) -> dict[str, int]:
    """Aggressive scale-out ladder (50%-of-remaining every +15% rung), same
    -20% trailing stop / 2h max-hold / liquidity-collapse safety net as v1,
    against a SPOT-ONLY price cascade (see module docstring's "Deliberately
    simplified" section -- no OHLCV window refinement here, unlike v1)."""
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

            if ws_feed is not None and row.get("dex_id") and row["pool_address"].lower() not in ws_feed._pools:
                try:
                    await ws_feed.add_pool(
                        row["pool_address"], dex_id=row["dex_id"], token_address=row["token_address"] or "",
                    )
                except Exception as exc:  # noqa: BLE001 -- best-effort, REST cascade still covers this pool
                    logger.info(
                        "robinhood_pump_v2_shadow: ws_feed.add_pool failed for %s (%s)",
                        row["pool_address"], exc,
                    )

            try:
                snapshot: PoolSnapshot = await _snapshot_with_fallback(
                    client, row["pool_address"], row["token_address"], chain=chain,
                    ws_feed=ws_feed, dex_id=row.get("dex_id"),
                )
            except Exception as exc:  # noqa: BLE001 -- one pool's failure never blocks the batch
                logger.info(
                    "robinhood_pump_v2_shadow: advance_exit_simulation snapshot failed for %s (%s)",
                    row["pool_address"], exc,
                )
                continue
            if not snapshot.available or snapshot.price_usd is None:
                continue
            counts["checked"] += 1
            current_price = snapshot.price_usd

            confirmed_peak_price = row["peak_price"] or entry_price
            pending_peak_price = row.get("pending_peak_price")
            pending_peak_since = row.get("pending_peak_since")
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

            # Same T003 peak-jump confirmation guardrail as v1, wired in from
            # day one rather than retrofitted after an artifact is found --
            # reused verbatim (paper_trader._advance_high_water), never
            # re-implemented.
            if confirmed_peak_price > 0 and current_price > confirmed_peak_price * _PEAK_JUMP_SUSPECT_RATIO:
                peak_price, pending_peak_price, pending_peak_since = _advance_high_water(
                    confirmed_peak_price, pending_peak_price, pending_peak_since,
                    current_price, datetime.now(timezone.utc),
                )
            else:
                peak_price = max(confirmed_peak_price, current_price)
                pending_peak_price, pending_peak_since = None, None

            entry_reserve = row.get("reserve_usd")
            liquidity_collapsed = (
                entry_reserve is not None and entry_reserve > 0
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
                elif current_price <= peak_price * (1 - TRAILING_STOP_PCT / 100.0):
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
                        peak_price = ?, pending_peak_price = ?, pending_peak_since = ?,
                        next_scale_level = ?, remaining_qty = ?,
                        realized_proceeds = ?, exit_reason = ?, final_multiplier = ?,
                        realistic_realized_proceeds = ?, realistic_final_multiplier = ?,
                        last_checked_at = ?, last_price = ?, last_reserve_usd = ?,
                        closed_at = ?
                    WHERE id = ?
                    """,
                    (
                        peak_price, pending_peak_price, pending_peak_since,
                        next_scale_level, remaining_qty,
                        realized_proceeds, exit_reason, final_multiplier,
                        realistic_realized_proceeds, realistic_final_multiplier,
                        datetime.now(timezone.utc).isoformat(), current_price,
                        snapshot.reserve_usd,
                        datetime.now(timezone.utc).isoformat() if exit_reason else None,
                        row["id"],
                    ),
                )
                await db.commit()

            if exit_reason and ws_feed is not None:
                try:
                    await ws_feed.remove_pool(row["pool_address"])
                except Exception as exc:  # noqa: BLE001 -- best-effort cleanup, never blocks a close
                    logger.info(
                        "robinhood_pump_v2_shadow: ws_feed.remove_pool failed for %s (%s)",
                        row["pool_address"], exc,
                    )

            if exit_reason == "scale_out_complete":
                counts["closed_scale_out_complete"] += 1
            elif exit_reason == "trailing_stop":
                counts["closed_trailing_stop"] += 1
            elif exit_reason == "max_hold":
                counts["closed_max_hold"] += 1
            elif exit_reason == "liquidity_collapse":
                counts["closed_liquidity_collapse"] += 1
    except Exception as exc:  # noqa: BLE001 -- shadow simulation must never raise into a caller
        logger.info("robinhood_pump_v2_shadow: advance_exit_simulation failed (%s)", exc)
    return counts


async def summary(chain: str = "robinhood") -> dict:
    """Aggregate read for session/monitoring use, outlier-resistant per
    project doctrine (never the raw average alone)."""
    await _ensure_table()
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            f"SELECT realistic_final_multiplier FROM {TABLE} "
            "WHERE chain = ? AND exit_reason IS NOT NULL AND realistic_final_multiplier IS NOT NULL",
            (chain,),
        )
        multipliers = [r["realistic_final_multiplier"] for r in await cur.fetchall()]
    n = len(multipliers)
    if n == 0:
        return {"closed": 0, "avg_pnl_pct": None, "avg_pnl_pct_no_top5": None, "winrate": None}
    pnls = sorted((m - 1.0) * 100.0 for m in multipliers)
    avg = sum(pnls) / n
    trimmed = sorted(pnls, reverse=True)[5:] if n > 5 else []
    avg_no_top5 = (sum(trimmed) / len(trimmed)) if trimmed else None
    wins = sum(1 for p in pnls if p > 0)
    return {
        "closed": n, "avg_pnl_pct": avg, "avg_pnl_pct_no_top5": avg_no_top5,
        "winrate": wins / n,
    }
