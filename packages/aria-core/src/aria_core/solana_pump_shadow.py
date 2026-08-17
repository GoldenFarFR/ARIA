"""Solana "take the train" shadow (16/08, explicit operator request) -- logs,
NEVER trades, NEVER opens even a real-simulated paper position.

Empirical basis (16/08 research session: Dune backtests + a manual visual
comparison against dozens of DexScreener charts with the operator): entering
when a token's price rises >= 25% on a rolling 5-minute window, then exiting
via a 25%-of-remaining-position ladder at every further +25% step above entry,
a -20% trailing stop from the highest price reached since entry, and a hard
2h max-hold, backtested at 97.6% win rate / 1.68x average multiplier on 42
real Base signals (Dune, historical). **ENCOURAGING BUT NOT VALIDATED**: same
sample used for calibration (classic overfitting risk), no trading costs
included, no out-of-sample test yet -- exactly the anti-overfitting doctrine
this codebase already applies everywhere else (cf. wick_filter_shadow.py's
own 05/08 precedent, backtest_robustness.py's train/validation-split rule).

**16/08, entry window recalibrated from 15min to 5min (same day, second
pass)**: a dedicated Dune re-run applying the exact same corrected exit
methodology (delay-realism, stop-check-every-candle) to the 5-minute window
found it beats 15min on both metrics -- 95.24%/1.53x vs 80.95%/1.23x at
0min delay, 85.71%/1.46x vs 78.57%/1.20x at 1min delay, same 42-signal
sample. Entering sooner captures more of the real move without hurting
reliability. The forward-measurement checkpoints below (``_HORIZON_MINUTES``)
are a SEPARATE concept (how long to wait before checking if a signal paid
off) and were deliberately left at 15/60/120min -- not tied to the entry
window's own duration.

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
   whose ``price_change_percentage.m5 >= M5_SURGE_THRESHOLD_PCT``. Dedupes
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
simulation`` used to only have whatever spot price ``get_pool_snapshot``
returned at the moment it happened to run -- an irregular, POINT-SAMPLE
cadence, never a continuous low. **16/08, second pass fixed the MAGNITUDE of
this gap (not the gap itself)**: a live position with a peak only +16% above
entry closed at a ``final_multiplier`` of 0.016 (98% loss) instead of the
~0.93 the -20% stop should have capped it at, because the real intra-cycle
low was never sampled and the stop only fired once a much later, much worse
poll happened to land. ``advance_exit_simulation`` now ALSO reads
``GeckoTerminalClient.get_ohlcv(pool_address, network=chain,
mode="scalping")`` (the existing 15min/30min sub-hour ladder, see
``services/ohlcv.py``) for every candle closed since the row's own
``last_checked_at``, and evaluates the scale-out ladder against the WINDOW
HIGH and the trailing stop against the WINDOW LOW of that window -- a rung
reached-then-retraced or a stop touched-then-crashed-further between two
polls is no longer invisible. The stop itself now fills at its OWN threshold
price (``peak_price * (1 - TRAILING_STOP_PCT/100)``, limit-order semantics,
same doctrine already used for scale-out rungs below), not wherever the spot
price happened to be sampled. **Still an honest, residual approximation, not
solved**: 15-30min candles remain far coarser than the Dune backtest's true
per-trade granularity -- a stop breached and fully recovered WITHIN a single
candle is still indistinguishable from one that never recovered, and a
severe crash that gaps straight through the stop level still fills at the
stop's threshold price (a documented, deliberately optimistic modeling
choice) rather than whatever worse price a real fill might have taken in a
genuinely illiquid flash-crash. ``get_ohlcv`` unavailable (thin pool,
network error) falls back to the OLD point-sample behavior for that pass,
never blocks the row -- see ``advance_exit_simulation``'s own docstring for
the exact mechanics and the get_pool_snapshot/get_ohlcv call-count
reasoning.

Best-effort throughout, same contract as every other shadow module in this
codebase: a logging/measurement failure must NEVER raise into whatever calls
this module, and never fabricates a value it couldn't really observe."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import aiosqlite

from aria_core.momentum_entry import _best_pair
from aria_core.paths import aria_db_path
from aria_core.services import dexscreener, rugcheck
from aria_core.services.geckoterminal import (
    GeckoTerminalClient,
    OHLCVResult,
    PoolSnapshot,
    TrendingPool,
    geckoterminal_client,
)

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())

# Calibrated threshold from the 16/08 Dune/DexScreener research pass (see
# module docstring) -- the ONLY entry signal this shadow layer evaluates.
# Recalibrated same day from the 15min to the 5min window (second Dune pass,
# same exit methodology, beat 15min on both winrate and avg multiplier).
M5_SURGE_THRESHOLD_PCT = 25.0

# 16/08, operator-requested protection against a token whose liquidity gets
# pulled shortly after launch (real case observed live this session: a
# ~35min-old pool's LP fully removed, price down -38.6% in 5min). A pool
# older than this at DETECTION time is never logged as a new signal. An
# already-open, currently-LOSING position (current price <= entry) is
# force-closed the moment its real age crosses this line -- see the
# priority-1 check at the top of ``advance_exit_simulation``'s per-row loop.
# A still-WINNING position keeps being tracked normally instead (16/08,
# second pass, operator decision: a real 1000% run shouldn't be cut short
# just because 25min passed -- the scale-out ladder already banks gains
# progressively either way). Fail-CLOSED on missing data (unlike this
# module's usual "never fabricate, fail-open" doctrine for pure
# observations): this is a protective filter, not a reported metric, so an
# unknown age is treated as "too risky to trade", never "assume it's fine".
MAX_POOL_AGE_MINUTES = 25.0

# Forward-measurement horizons, in minutes since detection -- m15/h1 give an
# early read, h2 matches the calibrated strategy's own hard max-hold (a
# position that hasn't resolved by 2h is force-closed in the real rule too).
_HORIZON_MINUTES: dict[str, int] = {"m15": 15, "h1": 60, "h2": 120}

# The CALIBRATED exit rule itself (16/08 Dune backtest, see module
# docstring) -- distinct from M5_SURGE_THRESHOLD_PCT (the ENTRY signal) and
# from _HORIZON_MINUTES (the older 3-checkpoint proxy above).
SCALE_OUT_STEP_PCT = 25.0  # each new rung is +25% above the PREVIOUS rung, cumulative from entry
SCALE_OUT_SELL_FRACTION = 0.25  # sell 25% of the REMAINING (not original) position at each rung crossed
TRAILING_STOP_PCT = 20.0  # close the rest if price falls 20% below the running high since entry
MAX_HOLD_MINUTES = _HORIZON_MINUTES["h2"]  # same 2h hard timeout as the calibrated rule's own max-hold

# --- 17/08, LIQUIDITY-FIRST REVISION (operator-directed) ------------------
# Root cause found by splitting the real 156-position sample by outcome after
# the survivorship-bias fix: positions that managed to EXIT cleanly were
# collectively PROFITABLE (+0.37$ over 64 rows), while positions that became
# unsellable mid-flight lost -7.91$ over 80 rows -- i.e. 96% of the total
# loss came from being STUCK, not from bad entries or a bad stop. The exit
# rule was never the problem; not getting out was.
#
# Two levers, in the order they matter:
#
# 1. MIN_RESERVE_USD_AT_ENTRY -- the stranded rate is strongly monotone in
#    entry liquidity, measured on the same sample: <2k$ -> 65-72% stranded,
#    2-5k$ -> 74%, 5-10k$ -> 42%, 10-25k$ -> 34%, >25k$ -> 36%. Entering
#    below ~10k$ is close to a coin flip on whether the position can ever be
#    closed at all. NOTE this is a real behavioural change to the shadow: it
#    no longer FUNDS every signal it sees. Sourcing itself stays unfiltered
#    (every candidate is still logged, per this module's own doctrine) --
#    what changes is that a too-thin pool is recorded with
#    ``realistic_entry_price`` left NULL, i.e. observed but never bought,
#    exactly like a pool already too shallow to fill the trade size.
# 2. LIQUIDITY_COLLAPSE_EXIT_PCT -- the entry filter alone is NOT enough
#    (35% of >=10k$ positions still ended stranded, still -26% overall),
#    because what traps a position is liquidity draining WHILE it is held,
#    not its depth at entry. The pool's current reserve is already fetched
#    every single cycle for the price-impact maths and was simply never
#    compared against the entry value. Now it is: a reserve that has fallen
#    past this fraction of its entry level closes the position IMMEDIATELY
#    at the current price, without waiting for the price stop -- the whole
#    point being to sell while a buyer still exists.
#
# Both are deliberately EXPRESSED AS CONSTANTS rather than hardcoded, and
# both are UNVALIDATED out-of-sample: they were derived from the very sample
# that revealed them (textbook overfitting risk, cf. this project's own
# anti-overfitting doctrine). The 17/08 shadow reset exists precisely to
# test them on independent data before anything is promoted further.
MIN_RESERVE_USD_AT_ENTRY = 10000.0
LIQUIDITY_COLLAPSE_EXIT_PCT = 50.0  # exit if reserve falls >=50% below its entry level

# 3. M5_ENTRY_CAP_PCT -- operator's own idea ("si une bougie a deja fait +25%
#    on entre pas"), measured on the same sample and confirmed, with one
#    important nuance: the cap is nearly WORTHLESS on its own (m5<60% alone
#    gives -43.3% vs -47.4% unfiltered) but strong in COMBINATION with the
#    liquidity floor, where it cuts the stranded rate by almost 3x:
#      reserve>=10k, no cap  -> 50 rows, -21.4%, 35% stranded
#      reserve>=10k, m5<60%  -> 29 rows,  -8.6%, 25% stranded
#      reserve>=10k, m5<40%  -> 12 rows,  +0.0%, 20% stranded
#    Reading: an entry that has ALREADY run hundreds of percent in 5 minutes
#    is buying the top of a launch spike, and those are precisely the pools
#    that drain. The raw sample contains m5 values up to +80917% (7 rows past
#    +1000%), i.e. tokens whose price started at essentially zero.
#    Set to 60 rather than the best-scoring 40 on purpose: 40 leaves only 12
#    rows out of 156 (~8%), far too thin to reach a 150-closure out-of-sample
#    test in reasonable time. 60 keeps ~19% of the flow while capturing most
#    of the effect -- a deliberate volume/selectivity tradeoff, not a tuned
#    optimum (tuning it on this sample is exactly the overfitting trap).
#    NOTE the operator asked for this cap on the 15-MINUTE window; that is
#    currently impossible to test: discovery runs on DexPaprika, whose search
#    endpoint has no m15/m30 field at all (documented in services/dexpaprika.py),
#    so `m15_pct` is NULL on all 278 archived rows. GeckoTerminal DOES expose
#    m15 (verified live 17/08) -- switching discovery, or enriching it, is the
#    prerequisite for ever testing the 15min variant.
M5_ENTRY_CAP_PCT = 60.0

# Below this fraction of the ORIGINAL position, a scale-out rung liquidates
# whatever is left in full and closes the row -- the calibrated ladder
# (25%-of-remaining forever) is asymptotic and never reaches a literal zero;
# this is a documented modeling choice (see module docstring), never a
# fabricated price -- the dust stub is valued at the current observed spot
# price, the only real observation available for it.
_SCALE_OUT_DUST_FRACTION = 0.01

# 17/08, operator-requested realistic execution simulation (price impact +
# fees) -- after observing X17690 live (reserve_usd=$0.0000002 at detection,
# final_multiplier=341.68x on the naive calc that assumes perfect execution
# at the displayed spot price -- fantasy on a pool with essentially zero
# depth). This parallel calculation estimates what a REAL trade of
# SIMULATED_TRADE_SIZE_USD would have actually captured, using a
# constant-product AMM approximation. NEVER replaces final_multiplier (kept
# as the "ideal, zero-friction" reference for comparison) -- this feeds a
# separate realistic_final_multiplier column instead.
# 17/08 -- resized from 20.0$ (the old CDP-pilot-range value) to 0.1$,
# explicit operator decision after seeing the real reconstruction: at 20$,
# most Solana signals were unreachable (too large for a thin pump.fun pool's
# liquidity, price-impact function returns None), so the "ideal" PnL badly
# overstated what a real wallet could have captured. At 0.1$ far more of the
# real signal flow becomes tradeable (112/127 Solana positions vs 45/127 at
# 20$, verified by replaying every closed row through this exact function).
SIMULATED_TRADE_SIZE_USD = 0.1
# Pump.fun bonding-curve fee (1.25% = 0.30% creator + 0.95% protocol), the
# conservative/higher rate applicable to the very young tokens this shadow
# overwhelmingly captures -- graduated PumpSwap pools fall to 0.25-0.30%.
# Sourced 17/08 (blocmates.com/uwuu.ai PumpSwap fee breakdown), never
# assumed from memory.
DEX_FEE_PCT = 1.25


def _apply_price_impact_and_fee(
    price: float, *, trade_size_usd: float, reserve_usd: float | None, side: str,
) -> float | None:
    """Constant-product AMM approximation of the price a REAL trade of
    ``trade_size_usd`` would achieve against a pool with ``reserve_usd``
    total liquidity (both sides combined -- ``depth = reserve_usd / 2``
    approximates one side's depth for a roughly-balanced pool, a documented
    simplification since this module has no per-token-side reserve data).
    Returns ``None`` -- never a fabricated number -- if the pool is too
    shallow to realistically absorb this trade size at all (``depth <=
    trade_size_usd``, the trade would move the price towards infinity).
    ``side="buy"`` raises the effective price paid; ``side="sell"`` lowers
    the effective price received. The DEX fee is applied on top, same
    direction in both cases (a buyer pays more, a seller receives less)."""
    if reserve_usd is None or reserve_usd <= 0:
        return None
    depth = reserve_usd / 2.0
    if depth <= trade_size_usd:
        return None
    if side == "buy":
        impacted = price * (depth + trade_size_usd) / depth
        return impacted * (1 + DEX_FEE_PCT / 100.0)
    impacted = price * depth / (depth + trade_size_usd)
    return impacted * (1 - DEX_FEE_PCT / 100.0)

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
    # 16/08, second pass -- the last moment ``advance_exit_simulation``
    # actually verified this row (see its docstring). Drives which OHLCV
    # candles count as "new since last time" -- NULL until the row's first
    # exit-simulation pass, at which point ``detected_at`` is used as the
    # implicit starting boundary instead.
    ("last_checked_at", "TEXT"),
    # 16/08, MAX_POOL_AGE_MINUTES protection -- the pool's real creation
    # timestamp (never the same as ``detected_at``, which only marks when
    # THIS shadow first saw it). NULL for any row logged before this column
    # existed (pre-migration rows, never backfilled -- honest gap, not a bug).
    ("pool_created_at", "TEXT"),
    # 16/08, RugCheck SHADOW-ONLY risk snapshot at detection time -- see
    # services/rugcheck.py's module docstring for why this is logged but
    # NEVER used to filter/block a signal (operator-requested: "let it trade
    # freely, we'll check hours later whether losses correlate with a flagged
    # score"). NULL means either the call failed/timed out or this row
    # predates the column -- never fabricated either way.
    ("rugcheck_score", "INTEGER"),
    ("rugcheck_risks", "TEXT"),
    ("rugcheck_top_holder_pct", "REAL"),
    # 16/08, operator-requested (banked for a future analysis, not built
    # yet) -- the deployer wallet, so signals can later be aggregated per
    # creator across multiple launches.
    ("rugcheck_creator", "TEXT"),
    # 17/08, operator-requested Telegram PnL notifications -- the last real
    # price observed for this row (updated every advance_exit_simulation
    # pass, same ``current_price`` already fetched for the ladder/stop
    # checks -- zero extra network cost). Lets chain_pnl_summary() compute
    # each OPEN position's unrealized PnL without a fresh network call at
    # notification time. NULL until this row's first exit-simulation pass.
    ("last_price", "REAL"),
    # 17/08, realistic execution simulation (price impact + DEX fee) -- see
    # SIMULATED_TRADE_SIZE_USD/_apply_price_impact_and_fee docstrings above.
    # NULL ``realistic_entry_price`` means the pool was already too shallow
    # to realistically buy into at detection time (never fabricated) --
    # ``realistic_final_multiplier`` then stays NULL too, for the same
    # reason, rather than a number built on a fabricated entry. Rows logged
    # before this column existed have NULL here -- honest gap, not a bug.
    ("realistic_entry_price", "REAL"),
    ("realistic_realized_proceeds", "REAL NOT NULL DEFAULT 0.0"),
    ("realistic_final_multiplier", "REAL"),
    # 17/08, operator-requested ("recupere aussi les volume de chaque
    # bougie, tous se qui pourrai servir a creer des donner") -- the
    # window-high/low candles read every passage (see
    # advance_exit_simulation's ``new_candles``) already carry a
    # ``volume`` field (Candle.volume) that was read for high/low only and
    # discarded. RUNNING total across the row's whole life (accumulated
    # passage over passage, not just the latest window) -- a per-passage
    # value would be overwritten and lost by the time the row closes,
    # useless for a post-hoc analysis. Banked for a future analysis (e.g.
    # does a trailing stop firing on real sell volume behave differently
    # from one firing on a thin/illiquid wick) -- never used to filter/gate
    # anything yet, same "log now, judge later" doctrine as rugcheck_score.
    # NULL means no candle with volume data has been observed yet -- never
    # fabricated as 0.
    ("window_volume_usd", "REAL"),
    # 17/08 -- the pool's reserve as of the LAST exit-simulation pass, next to
    # the entry-time ``reserve_usd``. Purely observational for now, but it is
    # the only way to measure how FAST a pool actually drains (the operator's
    # own question: "on peut calculer quand la liquidite s'effondre pour vite
    # cloturer ?"). LIQUIDITY_COLLAPSE_EXIT_PCT is currently a single static
    # threshold picked without any data on drain SPEED; this column is what
    # will let a future pass calibrate it on evidence instead.
    ("last_reserve_usd", "REAL"),
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
    """Logs one shadow row per pool crossing ``M5_SURGE_THRESHOLD_PCT`` on
    its 5-minute price change -- pure read+log, see the module's bright-line
    doctrine. Best-effort: a DB failure here must never break whatever
    fetched ``pools`` in the first place. Returns the number of NEW rows
    logged (0 on failure or when nothing qualifies)."""
    logged = 0
    try:
        await _ensure_table()
        async with aiosqlite.connect(_db_path()) as db:
            for pool in pools:
                m5 = pool.price_change_pct.get("m5")
                if m5 is None or m5 < M5_SURGE_THRESHOLD_PCT:
                    continue
                if pool.price_usd is None:
                    # Never fabricate an entry price -- a signal we can't
                    # price at detection time can't be forward-measured
                    # either, skip it honestly rather than log a bad row.
                    continue
                if pool.pool_created_at is None:
                    # MAX_POOL_AGE_MINUTES protection -- fail-CLOSED (see the
                    # constant's own docstring): an unknown age is never
                    # assumed safe.
                    continue
                pool_age_minutes = (
                    datetime.now(timezone.utc) - pool.pool_created_at
                ).total_seconds() / 60.0
                if pool_age_minutes >= MAX_POOL_AGE_MINUTES:
                    continue  # already past the protection window at detection time
                if await _has_open_signal(db, pool.pool_address, chain):
                    continue  # dedupe: an ongoing pump isn't re-logged every cycle

                # 16/08, RugCheck SHADOW-ONLY snapshot -- SEE services/
                # rugcheck.py's module docstring: logged for later
                # correlation analysis, NEVER used to filter/skip this
                # signal (operator-explicit: the entry stays free either
                # way). A failed/unavailable call never blocks logging the
                # signal itself -- best-effort enrichment only.
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
                    except Exception as exc:  # noqa: BLE001 -- shadow enrichment must never break the log pass
                        logger.info(
                            "solana_pump_shadow: rugcheck lookup failed for %s (%s)",
                            pool.token_address, exc,
                        )

                transactions_m15 = pool.transactions_m15 or {}
                # Exit-simulation state initialized right here at detection --
                # peak starts at entry (no higher price observed yet), first
                # rung is the calibrated ladder's own first step above entry.
                first_scale_level = pool.price_usd * (1 + SCALE_OUT_STEP_PCT / 100.0)
                # 17/08 -- realistic entry price under a real SIMULATED_TRADE_SIZE_USD
                # buy, never fabricated when the pool is too shallow to absorb it.
                realistic_entry_price = _apply_price_impact_and_fee(
                    pool.price_usd, trade_size_usd=SIMULATED_TRADE_SIZE_USD,
                    reserve_usd=pool.reserve_usd, side="buy",
                )
                # 17/08 liquidity-first revision (see MIN_RESERVE_USD_AT_ENTRY
                # above): a pool this thin is still LOGGED as an observation --
                # sourcing stays unfiltered per this module's doctrine -- but
                # it is never funded, so it can never contribute a stranded
                # loss. Leaving realistic_entry_price NULL is exactly how an
                # already-unfillable pool is represented, so every downstream
                # aggregate treats it correctly with no further change.
                # Unknown reserve is treated as too risky (fail-CLOSED), same
                # doctrine as MAX_POOL_AGE_MINUTES' unknown-age handling.
                if (pool.reserve_usd or 0.0) < MIN_RESERVE_USD_AT_ENTRY:
                    realistic_entry_price = None
                # Same "observe but never fund" treatment for an entry that
                # has already spiked past the cap -- see M5_ENTRY_CAP_PCT.
                elif m5 >= M5_ENTRY_CAP_PCT:
                    realistic_entry_price = None
                await db.execute(
                    """
                    INSERT INTO solana_pump_shadow_log (
                        pool_address, token_address, chain, symbol, status,
                        detected_at, entry_price,
                        m5_pct, m15_pct, m30_pct, h1_pct, h6_pct, h24_pct,
                        buyers_m15, sellers_m15, volume_usd_m15, reserve_usd,
                        remaining_qty, realized_proceeds, peak_price, next_scale_level,
                        pool_created_at, rugcheck_score, rugcheck_risks, rugcheck_top_holder_pct,
                        rugcheck_creator, realistic_entry_price
                    ) VALUES (?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1.0, 0.0, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        pool.pool_address, pool.token_address, chain, pool.symbol,
                        datetime.now(timezone.utc).isoformat(), pool.price_usd,
                        m5, pool.price_change_pct.get("m15"), pool.price_change_pct.get("m30"),
                        pool.price_change_pct.get("h1"), pool.price_change_pct.get("h6"),
                        pool.price_change_pct.get("h24"),
                        transactions_m15.get("buyers"), transactions_m15.get("sellers"),
                        pool.volume_usd_m15, pool.reserve_usd,
                        pool.price_usd, first_scale_level,
                        pool.pool_created_at.isoformat(),
                        rugcheck_score, rugcheck_risks, rugcheck_top_holder_pct, rugcheck_creator,
                        realistic_entry_price,
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


def _epoch_of(iso_ts: str | None) -> float | None:
    """Epoch seconds of an ISO timestamp, or ``None`` if missing/malformed --
    used to filter OHLCV candles to "closed since this row was last checked"
    in ``advance_exit_simulation``. A ``None`` boundary means "no known
    starting point", handled by the caller as "treat every candle as new"."""
    if not iso_ts:
        return None
    try:
        return datetime.fromisoformat(iso_ts).timestamp()
    except ValueError:
        return None


async def _snapshot_with_fallback(
    client: GeckoTerminalClient, pool_address: str, token_address: str | None, *, chain: str,
) -> PoolSnapshot:
    """DexScreener FIRST for the spot price, GeckoTerminal as fallback --
    16/08, operator-directed "API cascade" doctrine, inverted same day once
    both real budgets were compared (``docs/api-rate-limit-calibration.md``):
    DexScreener's confirmed real ceiling (~60/min, likely ~300/min on the
    pairs/tokens endpoint this module uses) is far above GeckoTerminal's
    demo-tier real ceiling (~15/min, repeatedly confirmed lower under load).
    DexScreener has its own INDEPENDENT rate budget (``services/
    dexscreener.py``, never shared with GeckoTerminal's adaptive throttle),
    so putting it first frees up GeckoTerminal's scarcer budget for what it
    alone can do -- OHLCV candles (DexScreener exposes NO real OHLCV
    endpoint, verified in ``services/dexscreener.py``, only a synthesized
    approximation from % variations, never real wicks). Real incident this
    whole cascade fixes: a single pool (Robinhood SQUIRREL) 429'd on every
    GeckoTerminal attempt across many consecutive cycles, leaving its
    exit-sim permanently unchecked. Never a third silent fabrication: both
    sources failing still returns ``available=False``, same "never fabricate
    a price" doctrine as the rest of this module."""
    if token_address:
        try:
            pairs = await dexscreener.fetch_token_pairs(token_address, chain=chain)
        except Exception as exc:  # noqa: BLE001 -- the primary source must never raise into the caller
            logger.info(
                "solana_pump_shadow: dexscreener primary lookup failed for %s (%s)", pool_address, exc,
            )
            pairs = []
        pair = _best_pair(pairs, token_address)
        if pair is not None and pair.price_usd is not None:
            return PoolSnapshot(
                pool_address=pool_address, price_usd=pair.price_usd,
                reserve_usd=pair.liquidity_usd, available=True,
            )
    return await client.get_pool_snapshot(pool_address, network=chain)


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
            # 17/08 -- real bug found live: the previous query selected the
            # `limit` OLDEST open rows unconditionally, even when they had
            # nothing due (e.g. already measured m15+h1, just waiting on the
            # 120min h2 checkpoint). Those rows never leave `status='open'`
            # until h2 lands, so they kept re-winning the ORDER BY every
            # single passage and starved every younger row behind them --
            # confirmed live: exactly `limit` rows older than a batch of 9
            # rows stuck with forward_pct_m15/h1 still NULL past their due
            # age, even though a direct snapshot call for one of them
            # succeeded instantly (never a snapshot failure, purely this
            # query never reaching them). Now filters to rows that actually
            # have a horizon due -- thresholds passed as params from
            # _HORIZON_MINUTES so this can never drift out of sync with the
            # per-row due_horizon logic below.
            cur = await db.execute(
                """
                SELECT * FROM solana_pump_shadow_log WHERE chain = ? AND status = 'open'
                  AND (
                    (forward_price_m15 IS NULL AND (julianday('now') - julianday(detected_at)) * 1440 >= ?)
                    OR (forward_price_h1 IS NULL AND (julianday('now') - julianday(detected_at)) * 1440 >= ?)
                    OR (forward_price_h2 IS NULL AND (julianday('now') - julianday(detected_at)) * 1440 >= ?)
                  )
                ORDER BY detected_at ASC LIMIT ?
                """,
                (chain, _HORIZON_MINUTES["m15"], _HORIZON_MINUTES["h1"], _HORIZON_MINUTES["h2"], limit),
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
                snapshot: PoolSnapshot = await _snapshot_with_fallback(
                    client, row["pool_address"], row["token_address"], chain=chain,
                )
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
    the two coexist (see module docstring). All state needed to resume lives
    in the row itself (``remaining_qty``/``peak_price``/``next_scale_level``/
    ``last_checked_at``) so this is safe to call on an arbitrarily irregular
    cadence -- nothing here assumes a fixed interval between two
    ``run_cycle`` passages.

    **16/08, second pass -- real gap fixed (see module docstring's "Honest
    scope limit, pass 3")**: the scale-out/trailing-stop THRESHOLD checks
    below no longer compare against a single point-sample spot price alone.
    Each still-simulating row now ALSO fetches
    ``GeckoTerminalClient.get_ohlcv(pool_address, network=chain,
    mode="scalping_5m")`` (5min candles, promoted from the 15min/30min
    ladder 17/08 -- explicit operator decision, ``services/ohlcv.py``) and
    reads every candle CLOSED since this row's
    ``last_checked_at`` (or ``detected_at`` on the very first pass). The
    scale-out ladder is now walked against the WINDOW HIGH of those candles
    (a rung reached then retraced between two polls is no longer invisible)
    and the trailing stop is now evaluated against the WINDOW LOW (a real
    intra-window touch of the stop level is no longer invisible either) --
    combined with the literal current spot price (``get_pool_snapshot``) so
    neither an already-closed candle nor a very fresh tick is ever missed.
    This is the exact bug a live position exposed: peak only +16% above
    entry, yet the point-sample poll landed on a much later, much worse
    price, producing a 98% loss instead of the ~-20% the stop was supposed
    to cap. Two calls per still-simulating row per call now (one
    ``get_pool_snapshot``, one ``get_ohlcv`` -- twice the previous per-row
    cost, never an unbounded multiple: a single ``get_ohlcv`` call covers
    every candle in the window, not one call per candle). This keeps the
    total throughput within the same order of magnitude already calibrated
    for this shadow, on the SAME shared GeckoTerminal throttle as the rest
    of prod -- see the 16/08 HANDOFF entry for the reasoning.
    ``get_pool_snapshot`` stays the sole source of the literal current/spot
    price for whatever genuinely needs it (dust-close/max-hold valuation),
    never replaced by a candle close, per this module's "never fabricate"
    doctrine.

    **Fill price on a trailing-stop close, deliberately changed**: it used
    to fill at the observed spot price (whatever ``get_pool_snapshot``
    happened to return at that instant, however far the price had already
    fallen by the time of that particular poll). It now fills at the STOP'S
    OWN threshold price (``peak_price * (1 - TRAILING_STOP_PCT/100)``) once
    the window low (or the current spot) confirms the level was touched --
    the same limit-order modeling doctrine already used for scale-out rungs
    below ("filled at that RUNG'S OWN price, not the observed spot"), now
    applied symmetrically to the stop. This is what actually fixes the
    observed bug's magnitude: a stop that fires now closes near
    ``peak*0.8``, never wherever a later, worse poll happened to land.

    **Still-honest residual limit (never to be glossed over)**: 15-30min
    candles are a large reduction of the blind spot versus a single instant
    spot sample, but still NOT continuous tick-by-tick data -- a stop that
    touches and fully reverses WITHIN one 15min candle is indistinguishable
    from one that never recovered (the candle's low correctly fires the
    stop either way), and a severe crash gapping straight through the stop
    level still fills at the stop's own threshold price (a documented,
    deliberately optimistic choice) rather than whatever worse price a real
    fill might take in genuine illiquidity. The Dune backtest this shadow is
    validated against uses true per-trade granularity; this remains an
    approximation, just a much tighter one.

    **Fail-open fallback, explicit choice**: if ``get_ohlcv`` raises, returns
    ``available=False`` (thin pool, network error, rate limit), or has no
    candle newer than ``last_checked_at``, this row falls back to the OLD
    point-sample behavior for this pass (window high/low both collapse to
    the current spot price) rather than being skipped entirely -- an
    imperfect measurement beats no measurement for a shadow whose whole
    purpose is accumulating forward data, same "never block, never
    fabricate" doctrine ``evaluate_open_signals`` already applies to a
    missed snapshot.

    Per row, in this fixed order (matches the calibrated rule's own
    precedence -- scale-out first since it's a rising-price event, trailing
    stop next, max-hold as the final catch-all):
    1. Update ``peak_price`` to the running high since entry (now the
       window/spot high, whichever is greater).
    2. Walk the scale-out ladder against the EFFECTIVE HIGH (window high
       folded with the current spot): while it has reached the next
       not-yet-filled rung and more than the dust fraction remains, sell 25%
       of the REMAINING position -- filled at that RUNG'S OWN price
       (limit-sell semantics, matching the calibrated rule's per-threshold
       definition), not at the possibly-higher observed high itself. A slow
       cycle that jumps past several rungs at once fills each one
       independently, never collapsed into a single fill.
    3. If what's left has dropped under 1% of the original position, close
       it out (``scale_out_complete``) -- the ladder is asymptotic and would
       otherwise never reach a literal zero; the dust stub is valued at the
       CURRENT spot price (``get_pool_snapshot``, the only real observation
       available for it), never fabricated.
    4. Otherwise, if the EFFECTIVE LOW (window low folded with the current
       spot) sits >=20% below the running peak, close the remainder
       (``trailing_stop``) at the STOP'S OWN threshold price -- see above.
    5. Otherwise, if 2h have elapsed since detection, force-close the
       remainder (``max_hold``) at the current spot price.
    ``final_multiplier`` (``realized_proceeds / entry_price``) is only ever
    written once ``remaining_qty`` reaches 0 via one of the 3 closes above --
    never estimated on a still-open row."""
    client = client or geckoterminal_client
    counts = {
        "checked": 0, "scale_out_fills": 0, "closed_scale_out_complete": 0,
        "closed_trailing_stop": 0, "closed_max_hold": 0, "closed_age_limit": 0,
        "closed_liquidity_collapse": 0,
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
                snapshot: PoolSnapshot = await _snapshot_with_fallback(
                    client, row["pool_address"], row["token_address"], chain=chain,
                )
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

            # Window high/low of every candle CLOSED since the last verified
            # moment -- see docstring's 16/08 second-pass note. Defaults to
            # the point-sample spot price (old behavior) when OHLCV is
            # unavailable, fails, or has nothing new since last time.
            window_high = current_price
            window_low = current_price
            try:
                # 17/08, later same day -- explicit operator decision: the
                # real exit decision now reads 5min candles instead of
                # 15min/30min. Made after the age_limit fix (a real crash was
                # partly missed between checks) and a shadow comparison
                # (candle_granularity_shadow.py) confirmed the finer window
                # would have caught more breaches -- promoted directly rather
                # than waiting out a longer observation period.
                ohlcv: OHLCVResult = await client.get_ohlcv(
                    row["pool_address"], network=chain, mode="scalping_5m",
                )
            except Exception as exc:  # noqa: BLE001 -- OHLCV is an enhancement, never a hard requirement
                logger.info(
                    "solana_pump_shadow: advance_exit_simulation get_ohlcv failed for %s (%s)",
                    row["pool_address"], exc,
                )
                ohlcv = None
            window_volume_usd = row.get("window_volume_usd")
            if ohlcv is not None and ohlcv.available and ohlcv.candles:
                boundary_epoch = _epoch_of(row.get("last_checked_at") or row["detected_at"])
                new_candles = [
                    c for c in ohlcv.candles if boundary_epoch is None or c.ts > boundary_epoch
                ]
                if new_candles:
                    window_high = max(c.high for c in new_candles)
                    window_low = min(c.low for c in new_candles)
                    window_volume_usd = (window_volume_usd or 0.0) + sum(c.volume for c in new_candles)

            # Fold the window with the literal current spot -- covers both a
            # closed candle the ladder hasn't reached yet AND a fresh tick
            # that hasn't formed a closed candle yet.
            effective_high = max(window_high, current_price)
            effective_low = min(window_low, current_price)

            peak_price = row["peak_price"] or entry_price
            peak_price = max(peak_price, effective_high)
            next_scale_level = row["next_scale_level"] or (entry_price * (1 + SCALE_OUT_STEP_PCT / 100.0))
            remaining_qty = row["remaining_qty"] if row["remaining_qty"] is not None else 1.0
            realized_proceeds = row["realized_proceeds"] or 0.0


            # 17/08 -- realistic execution simulation, tracked in parallel
            # through the exact same fills below (see module-level
            # _apply_price_impact_and_fee docstring). ``realistic_entry_price``
            # NULL means the pool was already too shallow to realistically
            # buy into at detection time -- ``realistic_unreachable`` starts
            # True in that case and every fill below is skipped for this
            # column, never fabricated. It also flips True mid-flight if ANY
            # later fill's pool depth turns out too shallow to absorb this
            # fill's size -- once true, it never resets for this row (a
            # partial realistic reconstruction would be worse than an honest
            # NULL).
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

            # MAX_POOL_AGE_MINUTES protection, top priority (16/08) -- checked
            # BEFORE the scale-out ladder. **16/08, second pass, operator
            # decision**: only force-closes a position that is NOT currently
            # winning (``current_price <= entry_price``) once the pool
            # crosses this age -- a still-winning position (e.g. a real
            # 1000% run that could offset several losing trades) keeps being
            # tracked normally by the scale-out ladder/trailing stop instead
            # of being cut short. The scale-out ladder itself already banks
            # 25% of whatever remains at every +25% rung, so a winning
            # position past the age line is never fully unprotected -- this
            # is deliberately NOT a second, separate profit-lock mechanism.
            # A row logged before this column existed has ``pool_created_at``
            # NULL -- never force-closed on unknown age (would silently close
            # every pre-migration open row at once); only a KNOWN age that
            # has crossed the line AND a currently-losing position triggers
            # this.
            pool_created_at_raw = row.get("pool_created_at")
            pool_age_minutes = _minutes_since(pool_created_at_raw) if pool_created_at_raw else None
            age_limit_exceeded = (
                pool_age_minutes is not None
                and pool_age_minutes >= MAX_POOL_AGE_MINUTES
                and current_price <= entry_price
            )
            # 17/08 -- real bug found live (SOLCATANA closed at -48.3% via
            # age_limit while TRAILING_STOP_PCT is a hard -20% floor):
            # age_limit was checked FIRST and unconditionally sold at
            # current_price, so a position whose period LOW had already
            # crossed the trailing-stop threshold never got to use it --
            # chronologically the stop would have fired first. This does
            # NOT add a floor to age_limit (that would fabricate a price no
            # real seller could have gotten on a genuine rug-pull collapse,
            # dishonest for a module whose whole point is measuring real
            # tradeable edge) -- it only lets the trailing stop win the race
            # when the period's REAL low proves it would have triggered.
            trailing_stop_already_crossed = effective_low <= peak_price * (1 - TRAILING_STOP_PCT / 100.0)
            if age_limit_exceeded and trailing_stop_already_crossed:
                age_limit_exceeded = False

            # 17/08 LIQUIDITY-FIRST REVISION -- the highest-priority exit, on
            # purpose. Measured on the real 156-position sample: positions
            # that exited cleanly were collectively PROFITABLE, while
            # positions that became unsellable accounted for 96% of the total
            # loss. What traps a position is the pool draining WHILE it is
            # held; ``snapshot.reserve_usd`` was already fetched every cycle
            # for the price-impact maths and simply never compared against
            # the entry level. Checked BEFORE age_limit and before the ladder
            # because the entire point is to sell while a buyer still exists
            # -- waiting for the price stop is precisely how a position ends
            # up stranded at a total loss. Fail-OPEN on unknown data (either
            # reserve missing): never force a close on an unverifiable
            # signal, same doctrine as everywhere else in this module.
            # 17/08 -- backfill the 5 signals a data audit found NEVER
            # collected (m15/m30 price change, buyers/sellers, m15 volume):
            # discovery runs on DexPaprika, whose search endpoint exposes none
            # of them, but the GeckoTerminal pool endpoint already called on
            # THIS very pass carries all of them. Zero extra network cost.
            # Written once, on the first pass that finds them missing, so the
            # value stays close to entry time rather than drifting. Purely
            # observational -- no decision reads these yet; they exist so the
            # operator's own m15-window cap idea and a buy/sell-pressure
            # filter become testable at all on the next sample.
            if row.get("m15_pct") is None and snapshot.price_change_pct:
                tx15 = (snapshot.transactions or {}).get("m15") or {}
                try:
                    async with aiosqlite.connect(_db_path()) as db:
                        await db.execute(
                            "UPDATE solana_pump_shadow_log SET m15_pct = ?, m30_pct = ?, "
                            "buyers_m15 = ?, sellers_m15 = ?, volume_usd_m15 = ? WHERE id = ?",
                            (
                                snapshot.price_change_pct.get("m15"),
                                snapshot.price_change_pct.get("m30"),
                                tx15.get("buyers"), tx15.get("sellers"),
                                (snapshot.volume_usd or {}).get("m15"),
                                row["id"],
                            ),
                        )
                        await db.commit()
                except Exception as exc:  # noqa: BLE001 -- enrichment must never break the pass
                    logger.info("solana_pump_shadow: signal backfill failed (%s)", exc)

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
            elif age_limit_exceeded:
                _realistic_sell(remaining_qty, current_price)
                realized_proceeds += remaining_qty * current_price
                remaining_qty = 0.0
                exit_reason = "age_limit"
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
                    """
                    UPDATE solana_pump_shadow_log SET
                        peak_price = ?, next_scale_level = ?, remaining_qty = ?,
                        realized_proceeds = ?, exit_reason = ?, final_multiplier = ?,
                        last_checked_at = ?, last_price = ?,
                        realistic_realized_proceeds = ?, realistic_final_multiplier = ?,
                        window_volume_usd = ?, last_reserve_usd = ?
                    WHERE id = ?
                    """,
                    (
                        peak_price, next_scale_level, remaining_qty,
                        realized_proceeds, exit_reason, final_multiplier,
                        datetime.now(timezone.utc).isoformat(), current_price,
                        realistic_realized_proceeds, realistic_final_multiplier,
                        window_volume_usd, snapshot.reserve_usd, row["id"],
                    ),
                )
                await db.commit()

            if exit_reason == "scale_out_complete":
                counts["closed_scale_out_complete"] += 1
            elif exit_reason == "trailing_stop":
                counts["closed_trailing_stop"] += 1
            elif exit_reason == "max_hold":
                counts["closed_max_hold"] += 1
            elif exit_reason == "age_limit":
                counts["closed_age_limit"] += 1
            elif exit_reason == "liquidity_collapse":
                counts["closed_liquidity_collapse"] += 1
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


async def chain_pnl_summary(chain: str = "solana") -> dict:
    """17/08, operator-requested Telegram notifications -- the single
    number that answers "is the capital growing or shrinking": cumulative
    PnL across EVERY signal ever logged on this chain, in units where 1.0 =
    one position's original stake (same normalized-unit convention used
    throughout this module's own scale-out math).

    Closed rows: fully realized, ``final_multiplier - 1.0``.
    Open rows: ``realized_proceeds`` already banked by scale-out fills PLUS
    the still-held ``remaining_qty`` valued at ``last_price`` (the last real
    price observed by ``advance_exit_simulation`` -- NEVER a fresh network
    call at notification time, and NEVER assumed flat at entry_price if
    unknown: a row with no ``last_price`` yet is simply excluded from the
    open-position component, counted in ``pending_price`` instead, since
    fabricating "flat" would understate a real move that just hasn't been
    observed yet)."""
    await _ensure_table()
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT entry_price, remaining_qty, realized_proceeds, final_multiplier, "
            "last_price, exit_reason FROM solana_pump_shadow_log WHERE chain = ?",
            (chain,),
        )
        rows = [dict(r) for r in await cur.fetchall()]

    total_pnl_units = 0.0
    closed = 0
    open_valued = 0
    pending_price = 0
    for r in rows:
        entry = r["entry_price"]
        if not entry:
            continue
        if r["exit_reason"] is not None and r["final_multiplier"] is not None:
            closed += 1
            total_pnl_units += r["final_multiplier"] - 1.0
        elif r["exit_reason"] is None:
            if r["last_price"] is None:
                pending_price += 1
                continue
            open_valued += 1
            remaining = r["remaining_qty"] if r["remaining_qty"] is not None else 1.0
            realized = r["realized_proceeds"] or 0.0
            current_value = realized + remaining * r["last_price"]
            total_pnl_units += current_value / entry - 1.0

    return {
        "total_pnl_units": total_pnl_units,
        "closed": closed,
        "open_valued": open_valued,
        "pending_price": pending_price,
    }


async def chain_pnl_summary_realistic(chain: str = "solana") -> dict:
    """17/08, answers "vérifier la liquidité minimum avant de trader" without
    adding a hard reject at sourcing time (which would silently throw away
    observations -- against this module's own shadow-only doctrine). Same
    aggregate as ``chain_pnl_summary`` above but built from the
    liquidity-aware ``realistic_*`` columns: a row whose
    ``realistic_entry_price`` is NULL means the entry itself was already too
    shallow to fill a ``SIMULATED_TRADE_SIZE_USD`` trade (see
    ``_apply_price_impact_and_fee``), and a row that only turns unreachable
    mid-exit (a later scale-out/stop sell lands on a pool too thin to absorb
    it) is caught the same way -- both counted explicitly in
    ``unreachable_liquidity``, NEVER silently dropped or treated as a 0%
    return. This is the number that reflects real tradeable edge; the
    zero-friction ``chain_pnl_summary`` above stays useful only to see how
    much of the ideal PnL is an illiquidity artifact."""
    await _ensure_table()
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT realistic_entry_price, remaining_qty, realistic_realized_proceeds, "
            "realistic_final_multiplier, last_price, exit_reason FROM solana_pump_shadow_log WHERE chain = ?",
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
                # 17/08 -- REAL MEASUREMENT BUG, found by the operator reading a
                # "+663%" notification while the position set was actually
                # LOSING money. A row that reaches here was genuinely BOUGHT
                # (realistic_entry_price is not None) but its exit turned
                # unsellable mid-flight (pool drained). The old code counted it
                # as "unreachable_liquidity" and dropped it from the total --
                # so the aggregate silently kept only the positions that
                # managed to exit cleanly, i.e. survivorship bias in its purest
                # form: every rug-pull disappeared from the P&L instead of
                # showing up as the loss it is. Bought-then-stranded capital is
                # a LOSS, never an unmeasurable event: whatever the scale-out
                # ladder banked before the pool dried up is real
                # (``realistic_realized_proceeds``, often 0.0), and the
                # unsold remainder is worth nothing to a seller who cannot
                # sell. Counted here as exactly that.
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

    # Every position that was really bought consumed real capital -- the
    # denominator that turns a sum of percentages into an honest return.
    positions_funded = closed + stranded + open_valued + pending_price
    capital_deployed_usd = positions_funded * SIMULATED_TRADE_SIZE_USD
    total_pnl_usd = total_pnl_units * SIMULATED_TRADE_SIZE_USD
    return {
        "total_pnl_units": total_pnl_units,
        # 17/08, operator request ("je veux voir les pnl en $ gagné ou perdu"):
        # the percentage alone is a SUM across positions and reads like a
        # portfolio return without being one -- a +663% sum sat on top of a
        # real loss. These two fields are what make the number honest.
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
