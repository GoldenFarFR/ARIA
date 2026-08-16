"""Solana "take the train" shadow (16/08, explicit operator request) -- logs,
NEVER trades, NEVER opens even a real-simulated paper position.

Empirical basis (16/08 research session: Dune backtests + a manual visual
comparison against dozens of DexScreener charts with the operator): entering
when a token's price rises >= 25% on a rolling 15-minute window, then exiting
via a 25%-of-remaining-position ladder at every further +25% step above entry,
a -20% trailing stop from the highest price reached since entry, and a hard
2h max-hold, backtested at 97.6% win rate / 1.68x average multiplier on 42
real Base signals (Dune, historical). **ENCOURAGING BUT NOT VALIDATED**: same
sample used for calibration (classic overfitting risk), no trading costs
included, no out-of-sample test yet -- exactly the anti-overfitting doctrine
this codebase already applies everywhere else (cf. wick_filter_shadow.py's
own 05/08 precedent, backtest_robustness.py's train/validation-split rule).

**Why Solana, why shadow-only**: the operator's own framing -- the most
honest out-of-sample validation is prospective data on tokens NEVER seen
during calibration, not a historical backtest where the threshold could have
been unconsciously fit. Solana is the natural pool of genuinely-unseen
tokens: **this is the FIRST time Solana is connected to ANYTHING in this
project**, even read-only -- ``momentum_entry.DEFAULT_CHAINS`` stays
``("base",)`` only (verified live 16/08, unchanged by this module), no other
sourcing/discovery path here reads Solana data. Governance risk is
correspondingly low (pure read + log, see the bright line below) but this
fact is stated explicitly rather than glossed over, per the project's
"vérifier avant d'affirmer" norm.

**Absolute bright line (never crossed by this module)**:
- Never calls ``paper_trader.open_position`` or any other position-opening
  function, real or simulated.
- Never calls ``wallet_guard``/``agent_wallet_pilot``/anything that could
  move real capital.
- Never reads from or writes to any table another pipeline treats as a
  trading signal -- ``solana_pump_shadow_log`` is a dedicated, standalone
  table, read by nothing else in the codebase.
- ``run_cycle()`` is a plain async function, callable manually or by a
  future test/cron -- **deliberately NOT wired into ``heartbeat.py`` by this
  change**. Wiring it in is a separate, explicit follow-up step (left to a
  future session/operator go), under a dedicated gate name reserved here by
  convention only: ``ARIA_SOLANA_PUMP_SHADOW_ENABLED`` (not read anywhere
  yet, not set in any ``.env`` -- naming it here is documentation, not
  activation).

Three-pass design, same "detect now, measure later" doctrine as
``v8_rsi_reversal_shadow.py``'s open/closed state machine:
1. ``record_signals()`` -- called with already-fetched
   ``GeckoTerminalClient.get_trending_pools()`` results, logs one row per pool
   whose ``price_change_percentage.m15 >= M15_SURGE_THRESHOLD_PCT``. Dedupes
   per ``(pool_address, chain)``: an already-OPEN signal for the same pool is
   never re-logged while still running (an ongoing pump would otherwise spam
   one row per cycle) -- a deliberate design choice, not an oversight.
2. ``evaluate_open_signals()`` -- a pragmatic proxy pass: re-fetches each
   open signal's CURRENT price (``GeckoTerminalClient.get_pool_snapshot``)
   once it has aged past 15min/1h/2h since detection, and records the real
   forward price/return at each fixed horizon. Closes the row (``status``
   column) once the 2h checkpoint is captured.
3. ``advance_exit_simulation()`` (added in a second pass, same day) -- the
   REAL calibrated exit rule itself, not a proxy: 25%-of-remaining scale-out
   ladder at every +25% rung above entry, -20% trailing stop from the
   running high since entry, 2h hard max-hold. Stateful and incremental
   (tracks ``remaining_qty``/``peak_price``/``next_scale_level`` per row) so
   it can resume correctly regardless of how irregularly ``run_cycle`` is
   actually called. Uses its own ``exit_reason``/``final_multiplier``
   columns, entirely independent of pass 2's ``status``/``forward_pct_h2`` --
   both mechanisms coexist and are read separately, neither replaces the
   other (two complementary out-of-sample checks on the same signals).

**Honest scope limit, pass 2 (m15/h1/h2 proxy, documented, not hidden)**: the
3 horizons are a pragmatic proxy for "did the signal pay off", not the exact
calibrated exit rule -- still enough on their own to compute a real
out-of-sample win rate (forward_pct_h2 > 0) and sanity-check the calibrated
multiplier (1.68x) against genuinely unseen tokens.

**Honest scope limit, pass 3 (the real exit rule, documented, not hidden)**:
the Dune backtest reconstructs each candle's real high/low, so its -20%
trailing stop fires against the true intra-candle low. ``advance_exit_
simulation`` only has whatever spot price ``get_pool_snapshot`` returns at
the moment it happens to run -- an irregular, POINT-SAMPLE cadence (however
long the gap between two ``run_cycle`` calls turns out to be), never a
continuous low. A stop that would have touched and recovered between two
polls is invisible here, and a stop that does fire here may register at a
worse price than a true tick-by-tick simulation would have caught it at.
This is a real, structural divergence from the backtest -- never to be
glossed over when reading this shadow's numbers against the 97.6%/1.68x
calibration figures. Scale-out rung fills, by contrast, are modeled at their
OWN threshold price (limit-sell semantics), not at the observed spot price,
since the calibrated rule is defined per-threshold; a slow cycle that jumps
past several rungs at once fills each one independently, at its own price
(see ``advance_exit_simulation``'s docstring for the exact mechanics).

Best-effort throughout, same contract as every other shadow module in this
codebase: a logging/measurement failure must NEVER raise into whatever calls
this module, and never fabricates a value it couldn't really observe."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import aiosqlite

from aria_core.paths import aria_db_path
from aria_core.services.geckoterminal import (
    GeckoTerminalClient,
    PoolSnapshot,
    TrendingPool,
    geckoterminal_client,
)

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())

# Calibrated threshold from the 16/08 Dune/DexScreener research pass (see
# module docstring) -- the ONLY entry signal this shadow layer evaluates.
M15_SURGE_THRESHOLD_PCT = 25.0

# Forward-measurement horizons, in minutes since detection -- m15/h1 give an
# early read, h2 matches the calibrated strategy's own hard max-hold (a
# position that hasn't resolved by 2h is force-closed in the real rule too).
_HORIZON_MINUTES: dict[str, int] = {"m15": 15, "h1": 60, "h2": 120}

# The CALIBRATED exit rule itself (16/08 Dune backtest, see module
# docstring) -- distinct from M15_SURGE_THRESHOLD_PCT (the ENTRY signal) and
# from _HORIZON_MINUTES (the older 3-checkpoint proxy above).
SCALE_OUT_STEP_PCT = 25.0  # each new rung is +25% above the PREVIOUS rung, cumulative from entry
SCALE_OUT_SELL_FRACTION = 0.25  # sell 25% of the REMAINING (not original) position at each rung crossed
TRAILING_STOP_PCT = 20.0  # close the rest if price falls 20% below the running high since entry
MAX_HOLD_MINUTES = _HORIZON_MINUTES["h2"]  # same 2h hard timeout as the calibrated rule's own max-hold

# Below this fraction of the ORIGINAL position, a scale-out rung liquidates
# whatever is left in full and closes the row -- the calibrated ladder
# (25%-of-remaining forever) is asymptotic and never reaches a literal zero;
# this is a documented modeling choice (see module docstring), never a
# fabricated price -- the dust stub is valued at the current observed spot
# price, the only real observation available for it.
_SCALE_OUT_DUST_FRACTION = 0.01

# Columns added after the table's first version (16/08, same day) -- PRAGMA-
# guarded ALTER TABLE so an already-existing prod DB migrates in place,
# same pattern as limit_orders.py/rsi_divergence_log.py/screened_pool.py.
_ADDED_COLUMNS: list[tuple[str, str]] = [
    ("remaining_qty", "REAL NOT NULL DEFAULT 1.0"),
    ("realized_proceeds", "REAL NOT NULL DEFAULT 0.0"),
    ("peak_price", "REAL"),
    ("next_scale_level", "REAL"),
    ("exit_reason", "TEXT"),
    ("final_multiplier", "REAL"),
]

_ensured_db_paths: set[str] = set()


def _db_path() -> str:
    # Read the module attribute at call time (never a from-import copy) so
    # tests monkeypatching ``DB_PATH`` to a tmp path work -- same doctrine as
    # every other shadow module in this codebase (wick_filter_shadow.py,
    # v8_rsi_reversal_shadow.py, ...).
    return DB_PATH


async def _ensure_table() -> None:
    path = _db_path()
    if path in _ensured_db_paths:
        return
    async with aiosqlite.connect(path) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS solana_pump_shadow_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pool_address TEXT NOT NULL,
                token_address TEXT,
                chain TEXT NOT NULL DEFAULT 'solana',
                symbol TEXT,
                status TEXT NOT NULL DEFAULT 'open',
                detected_at TEXT NOT NULL,
                entry_price REAL NOT NULL,
                m5_pct REAL,
                m15_pct REAL,
                m30_pct REAL,
                h1_pct REAL,
                h6_pct REAL,
                h24_pct REAL,
                buyers_m15 INTEGER,
                sellers_m15 INTEGER,
                volume_usd_m15 REAL,
                reserve_usd REAL,
                forward_price_m15 REAL,
                forward_pct_m15 REAL,
                forward_m15_measured_at TEXT,
                forward_price_h1 REAL,
                forward_pct_h1 REAL,
                forward_h1_measured_at TEXT,
                forward_price_h2 REAL,
                forward_pct_h2 REAL,
                forward_h2_measured_at TEXT,
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
            row[1]
            for row in await (await db.execute("PRAGMA table_info(solana_pump_shadow_log)")).fetchall()
        }
        for name, ddl in _ADDED_COLUMNS:
            if name not in existing:
                await db.execute(f"ALTER TABLE solana_pump_shadow_log ADD COLUMN {name} {ddl}")
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_solana_pump_shadow_lookup "
            "ON solana_pump_shadow_log (pool_address, chain, status)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_solana_pump_shadow_detected_at "
            "ON solana_pump_shadow_log (detected_at)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_solana_pump_shadow_exit_reason "
            "ON solana_pump_shadow_log (chain, exit_reason)"
        )
        await db.commit()
    _ensured_db_paths.add(path)


async def _has_open_signal(db: aiosqlite.Connection, pool_address: str, chain: str) -> bool:
    cur = await db.execute(
        "SELECT 1 FROM solana_pump_shadow_log WHERE pool_address = ? AND chain = ? AND status = 'open' LIMIT 1",
        (pool_address, chain),
    )
    return (await cur.fetchone()) is not None


async def record_signals(pools: list[TrendingPool], *, chain: str = "solana") -> int:
    """Logs one shadow row per pool crossing ``M15_SURGE_THRESHOLD_PCT`` on
    its 15-minute price change -- pure read+log, see the module's bright-line
    doctrine. Best-effort: a DB failure here must never break whatever
    fetched ``pools`` in the first place. Returns the number of NEW rows
    logged (0 on failure or when nothing qualifies)."""
    logged = 0
    try:
        await _ensure_table()
        async with aiosqlite.connect(_db_path()) as db:
            for pool in pools:
                m15 = pool.price_change_pct.get("m15")
                if m15 is None or m15 < M15_SURGE_THRESHOLD_PCT:
                    continue
                if pool.price_usd is None:
                    # Never fabricate an entry price -- a signal we can't
                    # price at detection time can't be forward-measured
                    # either, skip it honestly rather than log a bad row.
                    continue
                if await _has_open_signal(db, pool.pool_address, chain):
                    continue  # dedupe: an ongoing pump isn't re-logged every cycle

                transactions_m15 = pool.transactions_m15 or {}
                # Exit-simulation state initialized right here at detection --
                # peak starts at entry (no higher price observed yet), first
                # rung is the calibrated ladder's own first step above entry.
                first_scale_level = pool.price_usd * (1 + SCALE_OUT_STEP_PCT / 100.0)
                await db.execute(
                    """
                    INSERT INTO solana_pump_shadow_log (
                        pool_address, token_address, chain, symbol, status,
                        detected_at, entry_price,
                        m5_pct, m15_pct, m30_pct, h1_pct, h6_pct, h24_pct,
                        buyers_m15, sellers_m15, volume_usd_m15, reserve_usd,
                        remaining_qty, realized_proceeds, peak_price, next_scale_level
                    ) VALUES (?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1.0, 0.0, ?, ?)
                    """,
                    (
                        pool.pool_address, pool.token_address, chain, pool.symbol,
                        datetime.now(timezone.utc).isoformat(), pool.price_usd,
                        pool.price_change_pct.get("m5"), m15, pool.price_change_pct.get("m30"),
                        pool.price_change_pct.get("h1"), pool.price_change_pct.get("h6"),
                        pool.price_change_pct.get("h24"),
                        transactions_m15.get("buyers"), transactions_m15.get("sellers"),
                        pool.volume_usd_m15, pool.reserve_usd,
                        pool.price_usd, first_scale_level,
                    ),
                )
                logged += 1
            await db.commit()
    except Exception as exc:  # noqa: BLE001 -- shadow logging must never break the caller
        logger.info("solana_pump_shadow: record_signals failed (%s)", exc)
    return logged


def _minutes_since(iso_ts: str) -> float | None:
    try:
        then = datetime.fromisoformat(iso_ts)
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - then).total_seconds() / 60.0


async def evaluate_open_signals(
    client: GeckoTerminalClient | None = None, *, chain: str = "solana", limit: int = 50,
) -> dict[str, int]:
    """The real out-of-sample forward-measurement pass -- for each OPEN
    signal old enough to have crossed a not-yet-measured horizon (15min/1h/
    2h since detection), fetches the pool's CURRENT price
    (``get_pool_snapshot``) and records the real forward return. Closes the
    row once the 2h checkpoint lands. Best-effort per row: one pool's lookup
    failing (delisted, no liquidity left, network error) never blocks the
    others -- ``PoolSnapshot.available=False`` is simply skipped, left for
    the next passage to retry, never a fabricated 0%."""
    client = client or geckoterminal_client
    counts = {"measured_m15": 0, "measured_h1": 0, "measured_h2": 0, "closed": 0}
    try:
        await _ensure_table()
        async with aiosqlite.connect(_db_path()) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM solana_pump_shadow_log WHERE chain = ? AND status = 'open' "
                "ORDER BY detected_at ASC LIMIT ?",
                (chain, limit),
            )
            rows = [dict(r) for r in await cur.fetchall()]

        for row in rows:
            age_minutes = _minutes_since(row["detected_at"])
            if age_minutes is None:
                continue

            due_horizon: str | None = None
            for horizon, minutes in _HORIZON_MINUTES.items():
                if age_minutes >= minutes and row.get(f"forward_price_{horizon}") is None:
                    due_horizon = horizon
                    break  # earliest not-yet-measured horizon this passage handles
            if due_horizon is None:
                continue

            try:
                snapshot: PoolSnapshot = await client.get_pool_snapshot(row["pool_address"], network=chain)
            except Exception as exc:  # noqa: BLE001 -- one pool's failure never blocks the batch
                logger.info(
                    "solana_pump_shadow: get_pool_snapshot failed for %s (%s)", row["pool_address"], exc,
                )
                continue
            if not snapshot.available or snapshot.price_usd is None:
                continue

            entry_price = row["entry_price"]
            forward_pct = (snapshot.price_usd / entry_price - 1.0) * 100.0 if entry_price else None
            now_iso = datetime.now(timezone.utc).isoformat()

            async with aiosqlite.connect(_db_path()) as db:
                if due_horizon == "h2":
                    await db.execute(
                        """
                        UPDATE solana_pump_shadow_log SET
                            forward_price_h2 = ?, forward_pct_h2 = ?, forward_h2_measured_at = ?,
                            status = 'closed', closed_at = ?
                        WHERE id = ?
                        """,
                        (snapshot.price_usd, forward_pct, now_iso, now_iso, row["id"]),
                    )
                    counts["closed"] += 1
                else:
                    await db.execute(
                        f"""
                        UPDATE solana_pump_shadow_log SET
                            forward_price_{due_horizon} = ?, forward_pct_{due_horizon} = ?,
                            forward_{due_horizon}_measured_at = ?
                        WHERE id = ?
                        """,
                        (snapshot.price_usd, forward_pct, now_iso, row["id"]),
                    )
                await db.commit()
            counts[f"measured_{due_horizon}"] += 1
    except Exception as exc:  # noqa: BLE001 -- shadow measurement must never raise into a caller
        logger.info("solana_pump_shadow: evaluate_open_signals failed (%s)", exc)
    return counts


async def advance_exit_simulation(
    client: GeckoTerminalClient | None = None, *, chain: str = "solana", limit: int = 50,
) -> dict[str, int]:
    """Stateful, incremental simulation of the CALIBRATED exit rule itself
    (25%-of-remaining scale-out ladder every +25% rung above entry, -20%
    trailing stop from the running high since entry, 2h hard max-hold) --
    distinct from ``evaluate_open_signals``'s 3-fixed-horizon proxy above,
    the two coexist (see module docstring). One ``get_pool_snapshot`` call
    per still-simulating row per call (never more), all state needed to
    resume lives in the row itself (``remaining_qty``/``peak_price``/
    ``next_scale_level``) so this is safe to call on an arbitrarily
    irregular cadence -- nothing here assumes a fixed interval between two
    ``run_cycle`` passages.

    Per row, in this fixed order (matches the calibrated rule's own
    precedence -- scale-out first since it's a rising-price event, trailing
    stop next, max-hold as the final catch-all):
    1. Update ``peak_price`` to the running high since entry.
    2. Walk the scale-out ladder: while the CURRENT price has reached the
       next not-yet-filled rung and more than the dust fraction remains,
       sell 25% of the REMAINING position -- filled at that RUNG'S OWN price
       (limit-sell semantics, matching the calibrated rule's per-threshold
       definition), not at the possibly-higher observed price. A slow cycle
       that jumps past several rungs at once fills each one independently,
       never collapsed into a single fill.
    3. If what's left has dropped under 1% of the original position, close
       it out (``scale_out_complete``) -- the ladder is asymptotic and would
       otherwise never reach a literal zero; the dust stub is valued at the
       CURRENT observed price (the only real observation available for it),
       never fabricated.
    4. Otherwise, if the current price sits >=20% below the running peak,
       close the remainder (``trailing_stop``) at the current price. **Point-
       sample limitation, documented not hidden**: this can only see the
       spot price observed AT this call, never a true intra-cycle low --
       see the module docstring's "Honest scope limit, pass 3".
    5. Otherwise, if 2h have elapsed since detection, force-close the
       remainder (``max_hold``) at the current price.
    ``final_multiplier`` (``realized_proceeds / entry_price``) is only ever
    written once ``remaining_qty`` reaches 0 via one of the 3 closes above --
    never estimated on a still-open row."""
    client = client or geckoterminal_client
    counts = {
        "checked": 0, "scale_out_fills": 0, "closed_scale_out_complete": 0,
        "closed_trailing_stop": 0, "closed_max_hold": 0,
    }
    try:
        await _ensure_table()
        async with aiosqlite.connect(_db_path()) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM solana_pump_shadow_log WHERE chain = ? AND exit_reason IS NULL "
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
                snapshot: PoolSnapshot = await client.get_pool_snapshot(row["pool_address"], network=chain)
            except Exception as exc:  # noqa: BLE001 -- one pool's failure never blocks the batch
                logger.info(
                    "solana_pump_shadow: advance_exit_simulation snapshot failed for %s (%s)",
                    row["pool_address"], exc,
                )
                continue
            if not snapshot.available or snapshot.price_usd is None:
                continue
            counts["checked"] += 1
            current_price = snapshot.price_usd

            peak_price = row["peak_price"] or entry_price
            next_scale_level = row["next_scale_level"] or (entry_price * (1 + SCALE_OUT_STEP_PCT / 100.0))
            remaining_qty = row["remaining_qty"] if row["remaining_qty"] is not None else 1.0
            realized_proceeds = row["realized_proceeds"] or 0.0

            peak_price = max(peak_price, current_price)

            fills_this_cycle = 0
            while remaining_qty > _SCALE_OUT_DUST_FRACTION and current_price >= next_scale_level:
                sell_fraction = remaining_qty * SCALE_OUT_SELL_FRACTION
                realized_proceeds += sell_fraction * next_scale_level
                remaining_qty -= sell_fraction
                next_scale_level *= (1 + SCALE_OUT_STEP_PCT / 100.0)
                fills_this_cycle += 1
            counts["scale_out_fills"] += fills_this_cycle

            exit_reason: str | None = None
            if remaining_qty <= _SCALE_OUT_DUST_FRACTION and fills_this_cycle:
                realized_proceeds += remaining_qty * current_price
                remaining_qty = 0.0
                exit_reason = "scale_out_complete"
            elif current_price <= peak_price * (1 - TRAILING_STOP_PCT / 100.0):
                realized_proceeds += remaining_qty * current_price
                remaining_qty = 0.0
                exit_reason = "trailing_stop"
            elif age_minutes >= MAX_HOLD_MINUTES:
                realized_proceeds += remaining_qty * current_price
                remaining_qty = 0.0
                exit_reason = "max_hold"

            final_multiplier = (realized_proceeds / entry_price) if exit_reason else None

            async with aiosqlite.connect(_db_path()) as db:
                await db.execute(
                    """
                    UPDATE solana_pump_shadow_log SET
                        peak_price = ?, next_scale_level = ?, remaining_qty = ?,
                        realized_proceeds = ?, exit_reason = ?, final_multiplier = ?
                    WHERE id = ?
                    """,
                    (
                        peak_price, next_scale_level, remaining_qty,
                        realized_proceeds, exit_reason, final_multiplier, row["id"],
                    ),
                )
                await db.commit()

            if exit_reason == "scale_out_complete":
                counts["closed_scale_out_complete"] += 1
            elif exit_reason == "trailing_stop":
                counts["closed_trailing_stop"] += 1
            elif exit_reason == "max_hold":
                counts["closed_max_hold"] += 1
    except Exception as exc:  # noqa: BLE001 -- shadow simulation must never raise into a caller
        logger.info("solana_pump_shadow: advance_exit_simulation failed (%s)", exc)
    return counts


async def run_cycle(
    client: GeckoTerminalClient | None = None, *, network: str = "solana", duration: str = "5m",
) -> dict[str, int]:
    """One full shadow passage: fetch Solana's currently-trending pools,
    log any new +25%/15min signal, then advance BOTH forward-measurement
    passes on already-open signals -- the m15/h1/h2 proxy
    (``evaluate_open_signals``) AND the calibrated exit-rule simulation
    (``advance_exit_simulation``), two complementary angles on the same
    signals, neither replacing the other. Self-contained (no caller needed
    to sequence the steps itself) -- but, per the module's bright-line
    doctrine, this function is NOT called by ``heartbeat.py`` in this
    change; wiring it in (under the reserved
    ``ARIA_SOLANA_PUMP_SHADOW_ENABLED`` gate name) is an explicit follow-up
    left to a future step."""
    client = client or geckoterminal_client
    result = await client.get_trending_pools(network=network, duration=duration)
    logged = 0
    if result.available:
        logged = await record_signals(result.pools, chain=network)
    else:
        logger.info("solana_pump_shadow: get_trending_pools unavailable (%s)", result.error)
    measured = await evaluate_open_signals(client, chain=network)
    exit_sim = await advance_exit_simulation(client, chain=network)
    return {"fetched_pools": len(result.pools), "signals_logged": logged, **measured, "exit_sim": exit_sim}


async def summary(chain: str = "solana") -> dict:
    """Aggregate read for session/monitoring use -- never called from a real
    trading path. ``win_rate_h2``/``avg_multiplier_h2`` are the real
    out-of-sample numbers this shadow layer exists to produce, computed only
    over CLOSED signals (a real, complete 2h forward measurement), never
    estimated from open/incomplete rows."""
    await _ensure_table()
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT status, forward_pct_h2 FROM solana_pump_shadow_log WHERE chain = ?", (chain,)
        )
        rows = [dict(r) for r in await cur.fetchall()]
    closed = [r for r in rows if r["status"] == "closed" and r["forward_pct_h2"] is not None]
    wins = sum(1 for r in closed if r["forward_pct_h2"] > 0)
    return {
        "open": sum(1 for r in rows if r["status"] == "open"),
        "closed": len(closed),
        "wins_h2": wins,
        "win_rate_h2": (wins / len(closed)) if closed else None,
        "avg_multiplier_h2": (
            sum(1.0 + r["forward_pct_h2"] / 100.0 for r in closed) / len(closed)
        ) if closed else None,
    }


async def exit_simulation_summary(chain: str = "solana") -> dict:
    """The real out-of-sample winrate/multiplier for the CALIBRATED exit
    rule itself (``advance_exit_simulation``), the number this whole
    second pass exists to produce -- to compare against the 16/08 Dune
    backtest calibration (97.6% win rate / 1.68x average multiplier).
    Computed ONLY over rows whose exit simulation actually completed
    (``final_multiplier`` populated), never estimated from a still-open
    position. ``win`` = ``final_multiplier > 1.0`` (the position finished
    above its entry-normalized value), same convention as ``summary()``'s
    ``forward_pct_h2 > 0``."""
    await _ensure_table()
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT exit_reason, final_multiplier FROM solana_pump_shadow_log "
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
