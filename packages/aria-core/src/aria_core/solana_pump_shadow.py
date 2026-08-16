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

Two-pass design, same "detect now, measure later" doctrine as
``v8_rsi_reversal_shadow.py``'s open/closed state machine:
1. ``record_signals()`` -- called with already-fetched
   ``GeckoTerminalClient.get_trending_pools()`` results, logs one row per pool
   whose ``price_change_percentage.m15 >= M15_SURGE_THRESHOLD_PCT``. Dedupes
   per ``(pool_address, chain)``: an already-OPEN signal for the same pool is
   never re-logged while still running (an ongoing pump would otherwise spam
   one row per cycle) -- a deliberate design choice, not an oversight.
2. ``evaluate_open_signals()`` -- the forward-validation pass THIS research
   actually needs: re-fetches each open signal's CURRENT price
   (``GeckoTerminalClient.get_pool_snapshot``) once it has aged past 15min/1h/
   2h since detection, and records the real forward price/return at each
   horizon. Closes the row once the 2h checkpoint is captured.

**Honest scope limit (documented, not hidden)**: the 3 horizons (m15/h1/h2)
are a pragmatic proxy for "did the signal pay off", not a full simulation of
the calibrated exit rule itself (25%-of-position ladder at every +25% step,
-20% trailing stop from the running high, 2h hard timeout) -- that would
require tracking the running high-water mark and partial fills tick by tick,
which this first cut does not attempt. The 3 horizon prices are still enough
to compute a real out-of-sample win rate (forward_pct_h2 > 0) and to sanity
check the calibrated multiplier (1.68x) against genuinely unseen tokens --
the core question this shadow layer exists to answer. A full ladder/trailing-
stop simulation is a natural next step, left as an explicit TODO rather than
built now (time-boxed 16/08 build).

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
                closed_at TEXT
            )
            """
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_solana_pump_shadow_lookup "
            "ON solana_pump_shadow_log (pool_address, chain, status)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_solana_pump_shadow_detected_at "
            "ON solana_pump_shadow_log (detected_at)"
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
                await db.execute(
                    """
                    INSERT INTO solana_pump_shadow_log (
                        pool_address, token_address, chain, symbol, status,
                        detected_at, entry_price,
                        m5_pct, m15_pct, m30_pct, h1_pct, h6_pct, h24_pct,
                        buyers_m15, sellers_m15, volume_usd_m15, reserve_usd
                    ) VALUES (?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        pool.pool_address, pool.token_address, chain, pool.symbol,
                        datetime.now(timezone.utc).isoformat(), pool.price_usd,
                        pool.price_change_pct.get("m5"), m15, pool.price_change_pct.get("m30"),
                        pool.price_change_pct.get("h1"), pool.price_change_pct.get("h6"),
                        pool.price_change_pct.get("h24"),
                        transactions_m15.get("buyers"), transactions_m15.get("sellers"),
                        pool.volume_usd_m15, pool.reserve_usd,
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


async def run_cycle(
    client: GeckoTerminalClient | None = None, *, network: str = "solana", duration: str = "5m",
) -> dict[str, int]:
    """One full shadow passage: fetch Solana's currently-trending pools,
    log any new +25%/15min signal, then advance the forward-measurement pass
    on already-open signals. Self-contained (no caller needed to sequence the
    two steps itself) -- but, per the module's bright-line doctrine, this
    function is NOT called by ``heartbeat.py`` in this change; wiring it in
    (under the reserved ``ARIA_SOLANA_PUMP_SHADOW_ENABLED`` gate name) is an
    explicit follow-up left to a future step."""
    client = client or geckoterminal_client
    result = await client.get_trending_pools(network=network, duration=duration)
    logged = 0
    if result.available:
        logged = await record_signals(result.pools, chain=network)
    else:
        logger.info("solana_pump_shadow: get_trending_pools unavailable (%s)", result.error)
    measured = await evaluate_open_signals(client, chain=network)
    return {"fetched_pools": len(result.pools), "signals_logged": logged, **measured}


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
