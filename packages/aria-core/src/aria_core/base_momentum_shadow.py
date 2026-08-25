"""Base momentum shadow (23/08, explicit operator request) -- logs, NEVER
trades, NEVER opens even a real-simulated paper position.

**Why this exists, precisely**: the real Base paper-trading pipeline
(``paper_trader.py``, wallet="swing") has been PAUSED by the operator since
17/08 (``paper_pause_state.json``, "confirmee volontaire... remise apres
reprise par erreur" -- a deliberate stop, not an oversight), and the
operator does not want it resumed. But the operator DOES want the best
entry-age window on Base answered (see ``momentum_entry.evaluate_momentum_
entry``'s own ``pool_age_seconds`` field, added the same session for exactly
this question) -- unanswerable without fresh candidate data, and the real
pipeline being paused means it produces none. This module is the same
answer already given twice for other chains: build a SHADOW layer,
completely severed from the real trading pipeline, that keeps observing and
logging while the real switch stays off. Functional twin of
``solana_late_bonding_shadow.py``/``robinhood_pump_shadow.py`` (read either
module's docstring first -- this one intentionally mirrors its structure,
doctrine, and honest scope limits almost verbatim, only the chain and
sourcing endpoint differ).

**Calibration honesty, stated up front**: every threshold below
(``M5_SURGE_THRESHOLD_PCT``, ``MAX_POOL_AGE_MINUTES``, ``MIN_LIQUIDITY_USD``,
the scale-out/trailing-stop rule) is BORROWED from ``robinhood_pump_shadow.py``
verbatim, not calibrated for Base -- the same starting point that module
itself used on 16/08 before real data justified any of its own numbers. This
is deliberate, not lazy: a first shadow pass needs SOME rule to log a
signal at all, and Base is a genuinely different venue (distinct liquidity/
participant profile from both Solana and Robinhood Chain) so these numbers
should be expected to need their own recalibration once real closures
accumulate here -- same "accelerated observation on a new mechanism"
doctrine as every other freshly-armed gate in this dome.

**Sourcing**: ``dexpaprika.get_trending_pools("base", ...)`` -- verified
live 23/08 (with the real ``DEXPAPRIKA_API_KEY`` loaded, an unauthenticated
test call returns a misleading 402 that does NOT reflect the authenticated
pocket's real access) -- the SAME provider/function already used by the
Robinhood twin, chosen for the same reason: an independent throttle from
GeckoTerminal (see that module's own sourcing comment), never competing
with Solana's shared budget.

**Absolute bright line (never crossed by this module)**:
- Never calls ``paper_trader.open_position`` or any other position-opening
  function, real or simulated -- and never reads or writes
  ``paper_pause_state.json``/``paper_position``, the tables the real
  (paused) pipeline owns.
- Never calls ``wallet_guard``/``agent_wallet_pilot``/anything that could
  move real capital.
- Never reads from or writes to any table another pipeline treats as a
  trading signal -- ``base_momentum_shadow_log`` is a dedicated, standalone
  table, read by nothing else in the codebase.
- ``run_cycle()`` is a plain async function, callable manually or by a
  future test/cron -- **deliberately NOT wired into ``heartbeat.py`` by this
  change**. Wiring it in is a separate, explicit follow-up step (left to a
  future session/operator go), under a dedicated gate name reserved here by
  convention only: ``ARIA_BASE_MOMENTUM_SHADOW_ENABLED`` (not read anywhere
  yet, not set in any ``.env`` -- naming it here is documentation, not
  activation).

Three-pass design, identical to the Solana/Robinhood twins:
1. ``record_signals()`` -- called with already-fetched
   ``dexpaprika.get_trending_pools()`` results, logs one row per pool
   whose ``price_change_percentage.m5 >= M5_SURGE_THRESHOLD_PCT``. Dedupes
   per ``(pool_address, chain)``: an already-OPEN signal for the same pool
   is never re-logged while still running. Also stores ``pool_age_at_entry_
   seconds`` on every logged row -- the exact data point the operator's
   original question needs, computed the same way as ``momentum_entry.
   _pool_age_seconds`` but kept independent (this module never imports the
   real pipeline's internals beyond ``_best_pair``, a pure helper).
2. ``evaluate_open_signals()`` -- a pragmatic proxy pass: re-fetches each
   open signal's CURRENT price (``GeckoTerminalClient.get_pool_snapshot``)
   once it has aged past 15min/1h/2h since detection, and records the real
   forward price/return at each fixed horizon. Closes the row (``status``
   column) once the 2h checkpoint is captured.
3. ``advance_exit_simulation()`` -- the REAL calibrated exit rule itself, not
   a proxy: 25%-of-remaining scale-out ladder at every +25% rung above entry,
   -20% trailing stop from the running high since entry, 2h hard max-hold.
   Stateful and incremental (tracks ``remaining_qty``/``peak_price``/
   ``next_scale_level`` per row) so it can resume correctly regardless of how
   irregularly ``run_cycle`` is actually called. Uses its own
   ``exit_reason``/``final_multiplier`` columns, entirely independent of
   pass 2's ``status``/``forward_pct_h2`` -- both mechanisms coexist and are
   read separately, neither replaces the other.

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
from datetime import datetime, timedelta, timezone

import aiosqlite

from aria_core import pretrade_rejection_log, shadow_discovery_only
from aria_core.momentum_entry import _best_pair
from aria_core.paths import shadow_db_path
from aria_core.risk_guard import DEX_SWAP_FEE_PCT as _BASE_DEX_SWAP_FEE_FRACTION
from aria_core.services import dexpaprika, dexscreener, doppler
from aria_core.skills import chain_liquidity_regime
from aria_core.services.evm_swap_ws import EVMSwapWebSocketFeed
from aria_core.services.geckoterminal import (
    GeckoTerminalClient,
    OHLCVResult,
    PoolSnapshot,
    TrendingPool,
    geckoterminal_client,
)

logger = logging.getLogger(__name__)

# 17/08 -- see solana_pump_shadow.py's own DB_PATH comment (same incident,
# same fix): dedicated file, no longer shared with the prod container.
DB_PATH = str(shadow_db_path())

# 23/08 -- the table name was written out 18 times and existed as a constant
# NOWHERE, unlike every sister pocket (`solana_late_bonding_shadow.TABLE`).
# Found when wiring this pocket's missing Telegram notifications: the caller
# had nothing to import and would have had to restate the string a 19th time,
# which is exactly the defect CLAUDE.md names -- a constant redefined locally
# when it exists elsewhere. Declared here as the single source; the literals
# below are left as they are (a mechanical 18-site rewrite is a separate
# change, and this one only needs the name to be importable).
TABLE = "base_momentum_shadow_log"

# Calibrated threshold from the 16/08 Dune/DexScreener research pass (see
# module docstring) -- the ONLY entry signal this shadow layer evaluates.
# Recalibrated same day from the 15min to the 5min window (second Dune pass,
# same exit methodology, beat 15min on both winrate and avg multiplier).
#
# 24/08, LOWERED 25% -> 1%, operator-directed ("on va viser large... pour cumulee des donnees") --
# Base's own regime-candidates population showed a
# real liquidity signal (scams clustered $9-19K, real gains $22K+, see
# MIN_LIQUIDITY_USD below) but on only 4 winning trades, far short of the
# n>=100 the Doctrine d'Ingestion bar requires for a real verdict. Widening
# this gate to 1% lets far more candidates through so the pocket can
# accumulate that sample fast; liquidity (below) and pool age (see
# MAX_POOL_AGE_MINUTES) are now the operative filters, not this one.
# Explicit recalibration plan: revisit once base_momentum_shadow_log clears
# n>=100 real closures.
M5_SURGE_THRESHOLD_PCT = 1.0

# 16/08, operator-requested protection against a token whose liquidity gets
# pulled shortly after launch (real case observed live this session on the
# Solana twin module: a ~35min-old pool's LP fully removed, price down
# -38.6% in 5min). A pool older than this at DETECTION time is never logged
# as a new signal. An already-open, currently-LOSING position (current price
# <= entry) is force-closed the moment its real age crosses this line -- see
# the priority-1 check at the top of ``advance_exit_simulation``'s per-row
# loop. A still-WINNING position keeps being tracked normally instead
# (16/08, second pass, operator decision: a real 1000% run shouldn't be cut
# short just because 25min passed -- the scale-out ladder already banks
# gains progressively either way). Fail-CLOSED on missing data (unlike this
# module's usual "never fabricate, fail-open" doctrine for pure
# observations): this is a protective filter, not a reported metric, so an
# unknown age is treated as "too risky to trade", never "assume it's fine".
# 23/08 -- BORROWED verbatim from robinhood_pump_shadow.py's own MAX_POOL_AGE_
# MINUTES=10.0, itself the product of a real sweep (4/5/6/10/20-minute bands,
# 130-137 trades, winrate 62-96%) run on ROBINHOOD's own 133-closure sample --
# NOT Base's. A prior version of this comment copied that sweep's numbers
# verbatim, which read as though they described Base -- they never did; Base
# had zero closures of its own at the time this module was written. Base's own
# base_momentum_shadow_log currently has too few closures to run the same
# sweep (see pocket_entry_sweep.py, mandatory pre-trade-metric sweep tool for
# this exact question) -- re-run it here, on Base's own pool_age_at_entry_
# minutes column, once >=200 closures across >=2 distinct days exist. Until
# then this 10-minute figure is a placeholder inherited from a different
# pocket's market, not a Base-specific calibration.
#
# 24/08, RAISED 10 -> 120min, operator-directed, same "viser large... pour
# cumulee des donnees" pass as M5_SURGE_THRESHOLD_PCT/REGIME_MIN_MEDIAN_
# PEAK_PCT above -- liquidity and surge% do the real filtering now, this is
# widened rather than disarmed to keep a finite, loggable figure. Widens
# BOTH effects this constant controls, not just discovery: a losing
# position now also stays open (force-close deferred) up to 120min instead
# of 10 -- see this constant's own docstring above on the
# force-close-on-crossing behavior. Same explicit recalibration plan:
# re-run pocket_entry_sweep once Base clears its own n>=200 bar.
MAX_POOL_AGE_MINUTES = 120.0

# 24/08, LOWERED 4000 -> 1000, operator-directed ("viser large... pour
# cumulee des donnees", same pass as M5_SURGE_THRESHOLD_PCT/
# REGIME_MIN_MEDIAN_PEAK_PCT/MAX_POOL_AGE_MINUTES above). Own standalone
# value for THIS chain only -- no longer tied to any other pocket's figure
# (operator instruction: one constant per blockchain, never a shared/
# borrowed number). Matches the discovery-side floor already active in
# shadow_persistent.py's base_shadow_loop() (dexpaprika.get_trending_pools
# min_liquidity_usd=1000.0), so a candidate cleared at discovery is never
# rejected again here at trade time. Recalibrate on Base's own reserve_usd-
# at-entry column once base_momentum_shadow_log clears n>=100 real closures
# (see pocket_entry_sweep.py) -- until then this is a deliberately wide
# starting floor, not a profitability-calibrated one.
MIN_LIQUIDITY_USD = 1000.0

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

# 25/08 -- twin of robinhood_pump_shadow.py's own addition the day before
# (24/08), found missing here by a cross-pocket recheck triggered by the
# operator's question ("are rugs counted as a total loss?"). Nothing
# protected an already-open Base position against its pool's reserve
# collapsing mid-hold -- only entry-time MIN_LIQUIDITY_USD existed. Same
# value, BORROWED not independently calibrated for Base's own market
# (Doctrine d'Ingestion: a conservative hypothesis beats leaving a measured
# gap unguarded) -- this pocket has zero prior closes under this rule to
# calibrate its own threshold from. RECALIBRATE once this pocket accumulates
# >=100 liquidity_collapse closes of its own. Honest residual (never to be
# glossed over, same as the Robinhood twin): can silently never fire for a
# v3/v4 pool priced via evm_swap_ws (reserve_usd only populated for v2) --
# fires normally via the REST fallback (DexScreener/GeckoTerminal) instead.
LIQUIDITY_COLLAPSE_EXIT_PCT = 50.0

# Below this fraction of the ORIGINAL position, a scale-out rung liquidates
# whatever is left in full and closes the row -- the calibrated ladder
# (25%-of-remaining forever) is asymptotic and never reaches a literal zero;
# this is a documented modeling choice (see module docstring), never a
# fabricated price -- the dust stub is valued at the current observed spot
# price, the only real observation available for it.
_SCALE_OUT_DUST_FRACTION = 0.01

# 17/08, operator-requested realistic execution simulation (price impact +
# fees) -- mirrors solana_pump_shadow.py's own addition, same session, same
# real trigger (X17690 on Solana: reserve_usd essentially zero at detection,
# final_multiplier=341.68x on the naive calc that assumes perfect execution
# at the displayed spot price). NEVER replaces final_multiplier (kept as the
# "ideal, zero-friction" reference for comparison) -- feeds a separate
# realistic_final_multiplier column instead.
# 17/08 -- resized from 20.0$ (the old CDP-pilot-range value) to 0.1$,
# explicit operator decision after seeing the real reconstruction: at 20$,
# most Solana signals were unreachable (too large for a thin pump.fun pool's
# liquidity, price-impact function returns None), so the "ideal" PnL badly
# overstated what a real wallet could have captured. At 0.1$ far more of the
# real signal flow becomes tradeable (112/127 Solana positions vs 45/127 at
# 20$, verified by replaying every closed row through this exact function).
SIMULATED_TRADE_SIZE_USD = 0.1
# 23/08 -- reused, not restated: the real Base momentum pipeline already
# calibrated its own DEX swap fee (``risk_guard.DEX_SWAP_FEE_PCT``, Uniswap
# v3's standard 0.3% tier, see that constant's own comment) -- CLAUDE.md's
# architectural-coherence rule ("a redefined default is exactly how the
# biggest consumer ends up on the weakest endpoint") applies just as much to
# a fee assumption as to an RPC endpoint. Converted from fraction (0.003) to
# the percentage-point unit ``_apply_price_impact_and_fee`` expects.
DEX_FEE_PCT = _BASE_DEX_SWAP_FEE_FRACTION * 100.0


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

# Columns added after the table's first version -- PRAGMA-guarded ALTER
# TABLE so an already-existing prod DB migrates in place, same pattern as
# limit_orders.py/rsi_divergence_log.py/screened_pool.py/
# solana_pump_shadow.py.
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
    # ``realistic_final_multiplier`` then stays NULL too. Rows logged before
    # this column existed have NULL here -- honest gap, not a bug.
    ("realistic_entry_price", "REAL"),
    ("realistic_realized_proceeds", "REAL NOT NULL DEFAULT 0.0"),
    ("realistic_final_multiplier", "REAL"),
    # 17/08 -- same operator-requested addition as solana_pump_shadow.py's
    # twin column: RUNNING total of candle volume across the row's whole
    # life (accumulated passage over passage, never just the latest
    # window -- a per-passage value would be overwritten and lost by the
    # time the row closes). Banked for a future analysis, never used to
    # filter/gate anything yet. NULL means no candle with volume data has
    # been observed yet -- never fabricated as 0.
    ("window_volume_usd", "REAL"),
    # 23/08 -- the whole reason this module exists: the operator's original
    # question ("trouve la meilleure fenetre de trading") needs the pool's
    # age AT THE MOMENT OF ENTRY as a queryable column, not something
    # recomputed from pool_created_at/detected_at on every analysis pass.
    # Derived from the exact same pool_created_at already stored above --
    # redundant with it by design, for query convenience only.
    ("pool_age_at_entry_minutes", "REAL"),
    # 24/08 -- the DEX family (uniswap_v2/v3/v4/aerodrome/...) already came
    # back on the same dexpaprika.get_trending_pools() response used at
    # signal time (see TrendingPool.dex_id), just never stored. Needed to
    # call evm_swap_ws.add_pool() at exit-tracking time without a redundant
    # network call to re-discover it -- doctrine of ingesting a data point
    # that's already in hand rather than re-fetching or going without.
    ("dex_id", "TEXT"),
    # 25/08 -- the reserve_usd read at each exit-simulation pass, so
    # LIQUIDITY_COLLAPSE_EXIT_PCT's own trigger point stays visible on the row
    # afterward (same pattern as robinhood_pump_shadow.py's twin column).
    ("last_reserve_usd", "REAL"),
]

_ensured_db_paths: set[str] = set()


def _db_path() -> str:
    # Read the module attribute at call time (never a from-import copy) so
    # tests monkeypatching ``DB_PATH`` to a tmp path work -- same doctrine as
    # every other shadow module in this codebase (solana_pump_shadow.py,
    # wick_filter_shadow.py, v8_rsi_reversal_shadow.py, ...).
    return DB_PATH


async def _ensure_table() -> None:
    path = _db_path()
    if path in _ensured_db_paths:
        return
    async with aiosqlite.connect(path) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS base_momentum_shadow_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pool_address TEXT NOT NULL,
                token_address TEXT,
                chain TEXT NOT NULL DEFAULT 'base',
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
            for row in await (await db.execute("PRAGMA table_info(base_momentum_shadow_log)")).fetchall()
        }
        for name, ddl in _ADDED_COLUMNS:
            if name not in existing:
                await db.execute(f"ALTER TABLE base_momentum_shadow_log ADD COLUMN {name} {ddl}")
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_base_momentum_shadow_lookup "
            "ON base_momentum_shadow_log (pool_address, chain, status)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_base_momentum_shadow_detected_at "
            "ON base_momentum_shadow_log (detected_at)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_base_momentum_shadow_exit_reason "
            "ON base_momentum_shadow_log (chain, exit_reason)"
        )
        await db.commit()
    _ensured_db_paths.add(path)


async def _has_open_signal(db: aiosqlite.Connection, pool_address: str, chain: str) -> bool:
    cur = await db.execute(
        "SELECT 1 FROM base_momentum_shadow_log WHERE pool_address = ? AND chain = ? AND status = 'open' LIMIT 1",
        (pool_address, chain),
    )
    return (await cur.fetchone()) is not None


# ---------------------------------------------------------------------------
# 23/08, operator-directed ("construit ce 30 sur tout le monde") -- same
# regime gate as solana_late_bonding_shadow.py's REGIME_MIN_MEDIAN_PEAK_PCT
# (read that module for the full incident history: a first sensor that
# self-fed on TAKEN trades measured -0.18%/trade in production against
# +13.10% in a biased simulation, rebuilt as an independent, trade-blind
# sensor). ``record_regime_candidate``/``regime_state`` mirror that module's
# functions of the same name field-for-field; ``regime_median_peak`` below is
# a DUPLICATE of its pure function rather than an import -- solana_pump_
# shadow.py (this pocket's sibling) is itself imported BY solana_late_
# bonding_shadow.py, so a shared import risks a circular dependency the
# moment one of these REST pockets is reused the same way. The function has
# no state, so duplicating it costs nothing a shared import would have saved.
#
# WHY THIS POCKET CANNOT REUSE LATE_BONDING'S TRACKING MECHANISM: that sensor
# updates its peak via ``bonding_ws_feed.get_snapshot()``, a free in-memory
# read because the pool is already subscribed to a websocket for an
# unrelated reason (exit tracking). This pocket has no such subscription --
# its only view of the market is the REST ``dexpaprika.get_trending_pools()``
# fetch ``base_shadow_loop`` already performs every BASE_CADENCE_SECONDS=120s
# to look for NEW candidates, regardless of whether anything new is found.
# That fetch returns up to 25 pools with a live price EVERY cycle, so
# ``advance_regime_candidates_from_pools`` below re-reads THAT SAME response
# for pools already under tracking rather than issuing any REST call of its
# own -- zero marginal throughput.
#
# REAL BUDGET CHECKED BEFORE CHOOSING THIS (23/08): the 3 REST shadow pockets
# (robinhood/base/solana_pump) share DexPaprika's own throttle (independent
# of GeckoTerminal), 1 discovery call/cycle each at 120s cadence, plus
# ``advance_exit_simulation``'s DexScreener-first snapshot fallback (at most
# 1-2 calls per OPEN position per cycle, against a DIFFERENT provider's
# budget). A dedicated per-candidate regime poll on top of that -- up to 25
# candidates/cycle/pocket, all new REST calls -- would have added real,
# uncosted load for a signal the discovery fetch already carries for free.
# Reusing it is strictly better: no new call, no new budget risk, no new
# failure mode -- exactly the "Pense-Systeme" bar CLAUDE.md sets (never a
# linear/unbounded resource pattern when a staged, already-paid-for read
# already exists).
#
# HONEST LIMIT (documented, not hidden, same discipline as the sibling
# module's own comments): a candidate's peak is only updated on cycles where
# DexPaprika's top-25 "trending" response for this chain STILL carries its
# pool_address. A token that pumps briefly then drops out of the top 25
# (illiquid enough, or simply outranked) stops updating -- its logged peak
# understates the true market peak in that case. This is a conservative
# (never-inflated) bias: undercounting a hot market's peak only makes the
# gate MORE cautious, never less, which is the safe direction for a
# mechanism whose job is to detect when the market has gone cold.
REGIME_CANDIDATES_TABLE = "base_momentum_regime_candidates_log"
REGIME_WINDOW = 30
REGIME_TRACKING_WINDOW_MINUTES = 15.0
# Provisional, borrowed from late_bonding's own value -- NOT calibrated for
# this pocket specifically (Doctrine d'Ingestion: a conservative hypothesis
# beats leaving a promising mechanism idle for lack of fresh data).
# RECALIBRATION MANDATORY once this table holds >=100 rows (Doctrine
# d'Ingestion's own n>=100 bar) -- this pocket's screened population may
# differ from late_bonding's in either direction.
#
# 24/08, LOWERED 30% -> 25%, operator-directed, kept in lockstep with
# late_bonding's own recalibration (see that module's comment for the causal
# replay behind the number). This pocket's own table held only 49 rows at
# the time -- still below the n>=100 bar above, so this move is BORROWED
# consistency, not an independent verdict from this pocket's own population.
#
# 24/08, DISARMED (25% -> None) same day, operator-directed: this gate
# measures the market's RAW peak (never adjusted for whether it was really
# sellable, see solana_late_bonding_shadow.py's 24/08 rug-vs-regime finding
# -- Base's own scams cluster $9-19K liquidity, real gains $22K+, so a
# scam's inflated peak can still open/hold this gate open even though it was
# never capturable). Disarming it here is deliberate, not an oversight: the
# operative filters are now M5_SURGE_THRESHOLD_PCT (above) and
# MIN_LIQUIDITY_USD (below) -- "les criteres sont fixes ailleurs". None, not
# 0.0: `disarmed = REGIME_MIN_MEDIAN_PEAK_PCT is None` is the only value that
# guarantees ALWAYS open (0.0 could still block on a rare negative median).
# Re-arm once base_momentum_shadow_log clears n>=100 real closures and its
# own peak-vs-liquidity relationship can be measured directly.
REGIME_MIN_MEDIAN_PEAK_PCT: float | None = None

_ensured_regime_candidates_db_paths: set[str] = set()


async def _ensure_regime_candidates_table(db_path: str | None = None) -> None:
    path = db_path or _db_path()
    if path in _ensured_regime_candidates_db_paths:
        return
    async with aiosqlite.connect(path) as db:
        await db.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {REGIME_CANDIDATES_TABLE} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pool_address TEXT NOT NULL,
                mint TEXT NOT NULL,
                chain TEXT NOT NULL,
                decided_at TEXT NOT NULL,
                entry_price REAL NOT NULL,
                reserve_usd REAL,
                peak_price REAL NOT NULL,
                last_checked_at TEXT NOT NULL,
                tracking_status TEXT NOT NULL DEFAULT 'tracking'
            )
            """
        )
        await db.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{REGIME_CANDIDATES_TABLE}_decided "
            f"ON {REGIME_CANDIDATES_TABLE}(decided_at)"
        )
        await db.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{REGIME_CANDIDATES_TABLE}_status "
            f"ON {REGIME_CANDIDATES_TABLE}(tracking_status)"
        )
        await db.commit()
    _ensured_regime_candidates_db_paths.add(path)


async def record_regime_candidate(
    *, pool_address: str, mint: str, chain: str, entry_price: float,
    reserve_usd: float | None, db_path: str | None = None,
) -> None:
    """Logs one candidate that cleared every filter up to the point this
    pocket would log it as a signal -- REGARDLESS of what the regime gate
    itself decides below. Same discipline as solana_late_bonding_shadow.py:
    the sensor's input must never be gated by the gate's own verdict, or a
    shut gate starves its own sensor (the exact 23/08 incident that forced
    that module's rebuild). ``entry_price``/``reserve_usd`` come from the
    SAME discovery fetch already paid for -- no extra network call here.
    Never raises into the caller: a measurement must not cost a trade."""
    if not entry_price or entry_price <= 0:
        return
    try:
        await _ensure_regime_candidates_table(db_path)
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(db_path or _db_path()) as db:
            await db.execute(
                f"INSERT INTO {REGIME_CANDIDATES_TABLE} "
                f"(pool_address, mint, chain, decided_at, entry_price, reserve_usd, "
                f" peak_price, last_checked_at, tracking_status) "
                f"VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'tracking')",
                (pool_address, mint, chain, now, entry_price, reserve_usd,
                 entry_price, now),
            )
            await db.commit()
    except Exception as exc:  # noqa: BLE001 -- a sensor write never breaks a decision
        logger.info("base_momentum_shadow: record_regime_candidate failed (%s)", exc)


async def advance_regime_candidates_from_pools(
    pools: list[TrendingPool], *, max_rows: int = 200, db_path: str | None = None,
) -> dict:
    """Updates each still-tracked candidate's peak using the discovery
    fetch's OWN response -- see the module-level comment above for why this
    needs no dedicated network call. ``pools`` must be the exact same list
    the caller's loop already fetched via ``dexpaprika.get_trending_pools()``
    this cycle. A candidate whose pool is absent from this cycle's response
    simply keeps its last known peak (see the "HONEST LIMIT" comment above);
    it is still closed once REGIME_TRACKING_WINDOW_MINUTES elapses either
    way, so a candidate that fell out of the trending list cannot track
    forever. Safe to call on an arbitrarily irregular cadence, same
    discipline as ``advance_exit_simulation``: all state lives in the row
    itself."""
    path = db_path or _db_path()
    await _ensure_regime_candidates_table(path)
    stats = {"checked": 0, "updated": 0, "closed": 0}
    price_by_pool = {
        p.pool_address: p.price_usd for p in (pools or []) if p.price_usd is not None
    }
    cutoff = (
        datetime.now(timezone.utc) - timedelta(minutes=REGIME_TRACKING_WINDOW_MINUTES)
    ).isoformat()

    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            f"SELECT * FROM {REGIME_CANDIDATES_TABLE} WHERE tracking_status = 'tracking' "
            f"ORDER BY decided_at LIMIT ?",
            (max_rows,),
        )
        rows = [dict(r) for r in await cur.fetchall()]

    for row in rows:
        stats["checked"] += 1
        expired = row["decided_at"] < cutoff
        price = price_by_pool.get(row["pool_address"])
        if price is None and not expired:
            # not in this cycle's trending response and not yet expired --
            # nothing new to fold in, leave the row untouched.
            continue
        new_peak = max(price, row["peak_price"]) if price is not None else row["peak_price"]
        status = "closed" if expired else "tracking"
        async with aiosqlite.connect(path) as db:
            await db.execute(
                f"UPDATE {REGIME_CANDIDATES_TABLE} SET peak_price = ?, "
                f"last_checked_at = ?, tracking_status = ? WHERE id = ?",
                (new_peak, datetime.now(timezone.utc).isoformat(), status, row["id"]),
            )
            await db.commit()
        stats["updated"] += 1
        if status == "closed":
            stats["closed"] += 1
    return stats


def regime_median_peak(peaks: list[float]) -> float | None:
    """Median peak of the candidates handed in, or None below the window
    size. Duplicate of solana_late_bonding_shadow.regime_median_peak (see
    the module-level comment above for why this is a duplicate rather than
    an import) -- pure, no state, so the rule can be tested without a
    database. Below REGIME_WINDOW samples it returns None, and the caller
    treats that as OPEN: a pocket that has just started has no evidence the
    market is bad, and refusing on absent data would make a fresh epoch
    unable to ever collect any."""
    usable = [p for p in peaks if p is not None]
    if len(usable) < REGIME_WINDOW:
        return None
    recent = sorted(usable[-REGIME_WINDOW:])
    mid = len(recent) // 2
    if len(recent) % 2:
        return recent[mid]
    return (recent[mid - 1] + recent[mid]) / 2.0


async def regime_state(*, db_path: str | None = None) -> dict:
    """Reads the regime from candidates this pocket SCREENED, not from
    signals it logged -- same fix as solana_late_bonding_shadow.py's own
    23/08 rebuild. Ordered by ``decided_at``: what is knowable at the
    instant of a live decision is what has already been seen, regardless of
    whether it was ultimately logged as a signal."""
    await _ensure_regime_candidates_table(db_path)
    async with aiosqlite.connect(db_path or _db_path()) as db:
        cur = await db.execute(
            f"SELECT (peak_price / entry_price - 1.0) * 100.0 AS peak "
            f"FROM {REGIME_CANDIDATES_TABLE} "
            f"WHERE entry_price IS NOT NULL AND entry_price > 0 "
            f"ORDER BY decided_at DESC LIMIT ?",
            (REGIME_WINDOW,),
        )
        rows = [r[0] for r in await cur.fetchall()]
    # fetched newest-first, the pure helper expects oldest-first
    peaks = list(reversed(rows))
    median = regime_median_peak(peaks)
    disarmed = REGIME_MIN_MEDIAN_PEAK_PCT is None
    return {
        "median_peak_pct": median,
        "samples": len(peaks),
        # Disarmed reads as OPEN, never as shut: a threshold of None means
        # "no opinion on the regime", and a mechanism with no opinion must
        # not be the thing that stops the pocket trading.
        "open": disarmed or median is None or median >= REGIME_MIN_MEDIAN_PEAK_PCT,
        "threshold_pct": REGIME_MIN_MEDIAN_PEAK_PCT,
        "disarmed": disarmed,
    }


async def record_signals(pools: list[TrendingPool], *, chain: str = "base") -> int:
    """Logs one shadow row per pool crossing ``M5_SURGE_THRESHOLD_PCT`` on
    its 5-minute price change -- pure read+log, see the module's bright-line
    doctrine. Also stores ``pool_age_at_entry_minutes`` (the exact figure
    the operator's original question needs) on every logged row, computed
    from the same ``pool_created_at`` DexPaprika already returns -- no extra
    network call. Best-effort: a DB failure here must never break whatever
    fetched ``pools`` in the first place. Returns the number of NEW rows
    logged (0 on failure or when nothing qualifies)."""
    logged = 0
    _rows_for_candle_archive: list[tuple[int, TrendingPool]] = []
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
                # 23/08 -- EVERY refusal is logged from here down, where the pool
                # is priced and measurable. Without it the two filters tightened
                # today (age 25 -> 6 min, and the brand-new liquidity floor)
                # could never be recalibrated: what they reject vanishes without
                # trace, and a filter whose rejects are invisible can only be
                # trusted, never checked. Same registry as the Solana pockets.
                async def _refuse(_reason: str, _pool=pool) -> None:
                    try:
                        tx = _pool.transactions_m15 or {}
                        await pretrade_rejection_log.record_decision(
                            pretrade_rejection_log.GateDecision(
                                pocket="base_momentum", chain=chain,
                                mint=_pool.token_address or "",
                                pool_address=_pool.pool_address,
                                blocked=True, reason=_reason,
                                top_holder_pct=None, gate_latency_ms=None,
                                would_be_entry_price=_pool.price_usd,
                                would_be_reserve_usd=_pool.reserve_usd,
                                realistic_would_be_entry_price=None,
                                buys_observed=tx.get("buys"),
                                sells_observed=tx.get("sells"),
                            ),
                        )
                    except Exception as exc:  # noqa: BLE001 -- logging never blocks the pocket
                        logger.info("base_momentum_shadow: rejection log failed (%s)", exc)

                if pool_age_minutes >= MAX_POOL_AGE_MINUTES:
                    # already past the protection window at detection time
                    await _refuse(f"blocked_pool_age: {pool_age_minutes:.1f}min")
                    continue
                # 23/08 -- liquidity floor, see MIN_LIQUIDITY_USD. Placed AFTER
                # the age check so the cheaper test runs first. fail-CLOSED on an
                # unknown reserve: this pocket's whole defect was treating an
                # unmeasurable pool as a tradable one.
                if pool.reserve_usd is None or pool.reserve_usd < MIN_LIQUIDITY_USD:
                    await _refuse(f"blocked_thin_liquidity: reserve={pool.reserve_usd or 0:.0f}")
                    continue
                if await _has_open_signal(db, pool.pool_address, chain):
                    continue  # dedupe: an ongoing pump isn't re-logged every cycle

                # 23/08 -- regime gate, see the REGIME_MIN_MEDIAN_PEAK_PCT
                # block above. This candidate cleared every OTHER filter
                # (age, liquidity, dedupe), so it is exactly the population
                # the sensor needs -- logged UNCONDITIONALLY before the
                # verdict below, a gate must never decide its own sensor's
                # input.
                await record_regime_candidate(
                    pool_address=pool.pool_address, mint=pool.token_address or "",
                    chain=chain, entry_price=pool.price_usd, reserve_usd=pool.reserve_usd,
                )
                if not (await regime_state())["open"]:
                    # Last link on purpose: every cheaper filter has already
                    # had its say, so this only ever refuses candidates that
                    # were otherwise GOOD -- it is the market being refused,
                    # not the token.
                    await _refuse("blocked_regime_closed")
                    continue

                # 25/08 -- exogenous confirmation, see
                # skills/chain_liquidity_regime.py's module docstring. The
                # gate above is ENDOGENOUS (this pocket's own recent peaks) --
                # blind right after a reset until REGIME_WINDOW fresh
                # candidates re-accumulate. This reads DefiLlama's real
                # TVL/volume for the chain, independent of anything this
                # pocket has screened, so it has an opinion from day one.
                # Fail-open on anything but a CONFIRMED toxic spike (no
                # reading yet / calm / healthy inflow all read as OPEN) --
                # same doctrine as the gate above: no opinion must never be
                # the thing that stops the pocket trading.
                chain_regime = await chain_liquidity_regime.latest_regime(chain)
                if chain_regime and chain_regime["regime"] == chain_liquidity_regime.REGIME_TOXIC_SPIKE:
                    await _refuse(f"blocked_regime_defillama: {chain_regime['detail']}")
                    continue

                # 25/08 -- last link on purpose, same reasoning as the regime
                # gates above: this candidate cleared every OTHER filter, so
                # only THIS check ever refuses it. See shadow_discovery_only.py's
                # module docstring -- discovery/rejection-logging/regime-candidate
                # tracking above this line are all unaffected.
                if shadow_discovery_only.is_discovery_only():
                    await _refuse("blocked_discovery_only")
                    continue

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
                cur = await db.execute(
                    """
                    INSERT INTO base_momentum_shadow_log (
                        pool_address, token_address, chain, symbol, status,
                        detected_at, entry_price,
                        m5_pct, m15_pct, m30_pct, h1_pct, h6_pct, h24_pct,
                        buyers_m15, sellers_m15, volume_usd_m15, reserve_usd,
                        remaining_qty, realized_proceeds, peak_price, next_scale_level,
                        pool_created_at, realistic_entry_price, pool_age_at_entry_minutes, dex_id
                    ) VALUES (?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1.0, 0.0, ?, ?, ?, ?, ?, ?)
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
                        pool.pool_created_at.isoformat(), realistic_entry_price, pool_age_minutes,
                        pool.dex_id,
                    ),
                )
                new_id = cur.lastrowid
                logged += 1
                _rows_for_candle_archive.append((new_id, pool))
            await db.commit()

        # 25/08 -- this module never wired shadow_candle_archive despite the
        # 18/08 standing convention (every shadow module archives before/after
        # candles), unlike its robinhood_pump/solana_late_bonding twins. Real
        # gap: Base's own price path around entry was never stored, only the
        # entry/peak/exit snapshots -- found while diagnosing why Base's
        # realistic PnL kept worsening (-65% to -76% over three straight
        # days) with no candle history to actually inspect it against. Same
        # pattern as robinhood_pump_shadow's own fix: deliberately run AFTER
        # the ``async with aiosqlite.connect`` block above has already
        # closed, never inside that loop -- a network call while the
        # connection is held open is the anti-pattern this mirrors away from.
        # Best-effort: a fetch/archive failure here never un-logs the signal
        # row already committed above.
        for new_id, pool in _rows_for_candle_archive:
            try:
                before_ohlcv: OHLCVResult = await geckoterminal_client.get_ohlcv(
                    pool.pool_address, network=chain, mode="scalping_5m",
                )
                if before_ohlcv.available and before_ohlcv.candles:
                    from aria_core import shadow_candle_archive

                    await shadow_candle_archive.store_candles(
                        module="base_momentum", position_id=new_id,
                        pool_address=pool.pool_address, chain=chain, phase="before",
                        candles=before_ohlcv.candles,
                    )
            except Exception as exc:  # noqa: BLE001 -- archiving must never break the signal log
                logger.info(
                    "base_momentum_shadow: before-candle archive failed for %s (%s)",
                    pool.pool_address, exc,
                )
    except Exception as exc:  # noqa: BLE001 -- shadow logging must never break the caller
        logger.info("base_momentum_shadow: record_signals failed (%s)", exc)
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


async def _snapshot_from_ws(
    ws_feed: EVMSwapWebSocketFeed, pool_address: str, *, dex_id: str | None,
) -> PoolSnapshot | None:
    """24/08 -- direct on-chain Sync/Swap feed, tried BEFORE the REST
    cascade: the price the chain itself just settled, pushed the moment the
    block lands, no aggregator indexing delay in between (same latency
    argument already proven on Solana via pumpswap_ws.py). Returns None
    (never a fabricated snapshot) when the pool isn't tracked yet (the
    caller is expected to have already called ``add_pool`` -- see
    ``advance_exit_simulation``), hasn't ticked yet, or its USD leg can't be
    resolved -- the caller's cue to fall through to the existing REST
    cascade unchanged."""
    ws_snap = ws_feed.get_snapshot(pool_address)
    if not ws_snap.available:
        return None
    price_usd = ws_snap.price_usd
    if price_usd is None and ws_snap.quote_is_weth and ws_snap.price_quote is not None:
        eth_rate = await doppler.eth_usd_rate()
        if eth_rate is not None:
            price_usd = ws_snap.price_quote * eth_rate
    if price_usd is None:
        return None  # quote leg not resolvable to USD -- honest fallback to REST
    return PoolSnapshot(
        pool_address=pool_address, price_usd=price_usd,
        reserve_usd=ws_snap.reserve_usd, available=True, dex_id=dex_id,
    )


async def _snapshot_with_fallback(
    client: GeckoTerminalClient, pool_address: str, token_address: str | None, *, chain: str,
    ws_feed: EVMSwapWebSocketFeed | None = None, dex_id: str | None = None,
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
    approximation from % variations, never real wicks). Same cascade,
    verbatim, as the Robinhood twin -- built there after a real pool 429'd
    on every GeckoTerminal attempt across many consecutive cycles, leaving
    its exit-sim permanently unchecked; reused here rather than re-derived.
    Never a third silent fabrication: both sources failing still returns
    ``available=False``, same "never fabricate a price" doctrine as the
    rest of this module.

    **24/08 -- ``ws_feed`` tried FIRST, ahead of DexScreener**, when given:
    direct on-chain price, no aggregator indexing delay (see
    ``_snapshot_from_ws``). Falls through to this same DexScreener/
    GeckoTerminal cascade unchanged whenever the WS feed isn't tracking this
    pool yet, hasn't ticked, or can't resolve USD -- the WS path is a
    latency upgrade layered on top, never a replacement that could leave a
    pool unpriced."""
    if ws_feed is not None:
        ws_snapshot = await _snapshot_from_ws(ws_feed, pool_address, dex_id=dex_id)
        if ws_snapshot is not None:
            return ws_snapshot
    if token_address:
        try:
            pairs = await dexscreener.fetch_token_pairs(token_address, chain=chain)
        except Exception as exc:  # noqa: BLE001 -- the primary source must never raise into the caller
            logger.info(
                "base_momentum_shadow: dexscreener primary lookup failed for %s (%s)", pool_address, exc,
            )
            pairs = []
        pair = _best_pair(pairs, token_address)
        if pair is not None and pair.price_usd is not None:
            # 17/08, same real bug as solana_pump_shadow.py's twin function
            # (found live via a false liquidity_collapse close): DexScreener's
            # liquidity_unknown flag was ignored, so an unindexed/bonding-
            # curve pool's default 0.0 read as "genuinely drained". None is
            # the correct "unknown" sentinel -- advance_exit_simulation
            # already guards on `reserve_usd is not None`.
            reserve_usd = None if pair.liquidity_unknown else pair.liquidity_usd
            if reserve_usd is None:
                # 18/08, same real bug as solana_pump_shadow.py's twin
                # function (Krackpot, a $123K/24h-volume pump.fun pool
                # DexScreener reports as liquidity_unknown -- downstream,
                # _apply_price_impact_and_fee conflates "unknown" with
                # "genuinely dry", falsely marking an active pool as
                # unsellable/stranded). One extra lightweight GeckoTerminal
                # call, ONLY when DexScreener itself came back unknown, to
                # backfill a real reserve figure before conceding to None --
                # DexScreener's own price stays authoritative regardless.
                try:
                    gecko_snapshot = await client.get_pool_snapshot(pool_address, network=chain)
                    if gecko_snapshot.available and gecko_snapshot.reserve_usd:
                        reserve_usd = gecko_snapshot.reserve_usd
                except Exception as exc:  # noqa: BLE001 -- best-effort backfill, never blocks the primary snapshot
                    logger.info(
                        "base_momentum_shadow: GeckoTerminal reserve backfill failed for %s (%s)",
                        pool_address, exc,
                    )
                if reserve_usd is None:
                    # 18/08 -- twin of solana_pump_shadow.py's own 3rd-source
                    # fallback (see its comment for the SadDog incident that
                    # prompted this). Best-effort, degrades to None on any
                    # failure, same as every other backfill attempt here.
                    try:
                        reserve_usd = await dexpaprika.get_pool_reserve_usd(pool_address, network=chain)
                    except Exception as exc:  # noqa: BLE001 -- best-effort backfill, never blocks the primary snapshot
                        logger.info(
                            "base_momentum_shadow: DexPaprika reserve backfill failed for %s (%s)",
                            pool_address, exc,
                        )
            return PoolSnapshot(
                pool_address=pool_address, price_usd=pair.price_usd,
                reserve_usd=reserve_usd, available=True, dex_id=pair.dex_id,
            )
    return await client.get_pool_snapshot(pool_address, network=chain)


async def evaluate_open_signals(
    client: GeckoTerminalClient | None = None, *, chain: str = "base", limit: int = 50,
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
            # 17/08 -- same live-found bug as solana_pump_shadow.py's twin
            # query: selecting the `limit` OLDEST open rows unconditionally
            # let rows already measured on m15/h1 (just waiting on h2) starve
            # younger rows behind them every passage, since they never leave
            # status='open' until h2 lands. Filters to rows with an actually
            # due horizon -- thresholds from _HORIZON_MINUTES, never
            # duplicated as bare numbers so this can't drift from the
            # per-row due_horizon logic below.
            cur = await db.execute(
                """
                SELECT * FROM base_momentum_shadow_log WHERE chain = ? AND status = 'open'
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
                    "base_momentum_shadow: get_pool_snapshot failed for %s (%s)", row["pool_address"], exc,
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
                        UPDATE base_momentum_shadow_log SET
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
                        UPDATE base_momentum_shadow_log SET
                            forward_price_{due_horizon} = ?, forward_pct_{due_horizon} = ?,
                            forward_{due_horizon}_measured_at = ?
                        WHERE id = ?
                        """,
                        (snapshot.price_usd, forward_pct, now_iso, row["id"]),
                    )
                await db.commit()
            counts[f"measured_{due_horizon}"] += 1
    except Exception as exc:  # noqa: BLE001 -- shadow measurement must never raise into a caller
        logger.info("base_momentum_shadow: evaluate_open_signals failed (%s)", exc)
    return counts


async def advance_exit_simulation(
    client: GeckoTerminalClient | None = None, *, chain: str = "base", limit: int = 50,
    ws_feed: EVMSwapWebSocketFeed | None = None,
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
                "SELECT * FROM base_momentum_shadow_log WHERE chain = ? AND exit_reason IS NULL "
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
                        "base_momentum_shadow: ws_feed.add_pool failed for %s (%s)",
                        row["pool_address"], exc,
                    )

            try:
                snapshot: PoolSnapshot = await _snapshot_with_fallback(
                    client, row["pool_address"], row["token_address"], chain=chain,
                    ws_feed=ws_feed, dex_id=row.get("dex_id"),
                )
            except Exception as exc:  # noqa: BLE001 -- one pool's failure never blocks the batch
                logger.info(
                    "base_momentum_shadow: advance_exit_simulation snapshot failed for %s (%s)",
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
                    "base_momentum_shadow: advance_exit_simulation get_ohlcv failed for %s (%s)",
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
                    # 25/08, same standing convention as the "before" archive
                    # above -- zero extra network cost, these candles were
                    # already fetched for the window high/low. Closes the gap
                    # this module never had: a future recalibration can now
                    # actually re-simulate LIQUIDITY_COLLAPSE_EXIT_PCT/TRAILING
                    # constants against Base's real price path, not just the
                    # entry/peak/exit snapshots.
                    try:
                        from aria_core import shadow_candle_archive

                        await shadow_candle_archive.store_candles(
                            module="base_momentum", position_id=row["id"],
                            pool_address=row["pool_address"], chain=chain, phase="after",
                            candles=new_candles,
                        )
                    except Exception as exc:  # noqa: BLE001 -- archiving must never break the batch
                        logger.info(
                            "base_momentum_shadow: after-candle archive failed for %s (%s)",
                            row["pool_address"], exc,
                        )

            # Fold the window with the literal current spot -- covers both a
            # closed candle the ladder hasn't reached yet AND a fresh tick
            # that hasn't formed a closed candle yet.
            effective_high = max(window_high, current_price)
            effective_low = min(window_low, current_price)

            peak_price = row["peak_price"] or entry_price
            next_scale_level = row["next_scale_level"] or (entry_price * (1 + SCALE_OUT_STEP_PCT / 100.0))
            remaining_qty = row["remaining_qty"] if row["remaining_qty"] is not None else 1.0
            realized_proceeds = row["realized_proceeds"] or 0.0

            # 17/08 -- realistic execution simulation, tracked in parallel
            # through the exact same fills below (see module-level
            # _apply_price_impact_and_fee docstring). See
            # solana_pump_shadow.py's own copy for the full rationale.
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

            peak_price = max(peak_price, effective_high)

            # 25/08 -- liquidity_collapse, top priority, checked BEFORE even
            # MAX_POOL_AGE_MINUTES (see LIQUIDITY_COLLAPSE_EXIT_PCT's own
            # docstring): unrelated to price or age, this protects against an
            # unsellable pool -- a reserve that has already lost more than
            # half its entry depth is the clearest signal here that waiting
            # for the age/scale-out/trailing-stop machinery to catch up only
            # risks a worse fill later, never a better one. Fail-open on an
            # unknown/missing reserve reading (never fabricated), same
            # doctrine as every other observation in this module.
            entry_reserve = row.get("reserve_usd")
            liquidity_collapsed = (
                entry_reserve is not None and entry_reserve > 0
                and snapshot.reserve_usd is not None
                and snapshot.reserve_usd < entry_reserve * (1 - LIQUIDITY_COLLAPSE_EXIT_PCT / 100.0)
            )

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
                    UPDATE base_momentum_shadow_log SET
                        peak_price = ?, next_scale_level = ?, remaining_qty = ?,
                        realized_proceeds = ?, exit_reason = ?, final_multiplier = ?,
                        realistic_realized_proceeds = ?, realistic_final_multiplier = ?,
                        last_checked_at = ?, last_price = ?, window_volume_usd = ?,
                        last_reserve_usd = ?
                    WHERE id = ?
                    """,
                    (
                        peak_price, next_scale_level, remaining_qty,
                        realized_proceeds, exit_reason, final_multiplier,
                        realistic_realized_proceeds, realistic_final_multiplier,
                        datetime.now(timezone.utc).isoformat(), current_price,
                        window_volume_usd, snapshot.reserve_usd, row["id"],
                    ),
                )
                await db.commit()

            if exit_reason and ws_feed is not None:
                # 24/08 -- sheds the subscription the moment a position
                # closes, same doctrine as the Solana bonding/PumpSwap feed's
                # own remove_pools (see shadow_persistent.py's
                # _BondingOrPumpswapFeed docstring for the real incident:
                # 216 pools accumulated over ~1h40 with no way to ever shed
                # one). Cheap no-op if this pool was never WS-tracked.
                try:
                    await ws_feed.remove_pool(row["pool_address"])
                except Exception as exc:  # noqa: BLE001 -- best-effort cleanup, never blocks a close
                    logger.info(
                        "base_momentum_shadow: ws_feed.remove_pool failed for %s (%s)",
                        row["pool_address"], exc,
                    )

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
        logger.info("base_momentum_shadow: advance_exit_simulation failed (%s)", exc)
    return counts


async def run_cycle(
    client: GeckoTerminalClient | None = None, *, network: str = "base", limit: int = 25,
) -> dict[str, int]:
    """One full shadow passage: fetch Base's currently-trending pools (via
    DexPaprika, see module docstring's sourcing note -- NOT this parameter's
    ``client``, kept only for ``evaluate_open_signals``/
    ``advance_exit_simulation`` below, which still price already-open rows
    through the DexScreener/GeckoTerminal cascade), log any new +25%/5min
    signal, then advance BOTH forward-measurement passes on already-open
    signals -- the m15/h1/h2 proxy (``evaluate_open_signals``) AND the
    calibrated exit-rule simulation (``advance_exit_simulation``), two
    complementary angles on the same signals, neither replacing the other.
    Self-contained (no caller needed to sequence the steps itself) -- but,
    per the module's bright-line doctrine, this function is NOT called by
    ``heartbeat.py`` in this change; wiring it in (under the reserved
    ``ARIA_BASE_MOMENTUM_SHADOW_ENABLED`` gate name) is an explicit
    follow-up left to a future step."""
    result = await dexpaprika.get_trending_pools(network, limit=limit)
    logged = 0
    if result.available:
        logged = await record_signals(result.pools, chain=network)
    else:
        logger.info("base_momentum_shadow: get_trending_pools unavailable (%s)", result.error)
    measured = await evaluate_open_signals(client, chain=network)
    exit_sim = await advance_exit_simulation(client, chain=network)
    return {"fetched_pools": len(result.pools), "signals_logged": logged, **measured, "exit_sim": exit_sim}


async def summary(chain: str = "base") -> dict:
    """Aggregate read for session/monitoring use -- never called from a real
    trading path. ``win_rate_h2``/``avg_multiplier_h2`` are the real
    out-of-sample numbers this shadow layer exists to produce, computed only
    over CLOSED signals (a real, complete 2h forward measurement), never
    estimated from open/incomplete rows."""
    await _ensure_table()
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT status, forward_pct_h2 FROM base_momentum_shadow_log WHERE chain = ?", (chain,)
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


async def exit_simulation_summary(chain: str = "base") -> dict:
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
            "SELECT exit_reason, final_multiplier FROM base_momentum_shadow_log "
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


async def chain_pnl_summary(chain: str = "base") -> dict:
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
            "last_price, exit_reason FROM base_momentum_shadow_log WHERE chain = ?",
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


async def chain_pnl_summary_realistic(chain: str = "base") -> dict:
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
            "realistic_final_multiplier, last_price, exit_reason FROM base_momentum_shadow_log WHERE chain = ?",
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
                # 17/08 -- same real measurement bug fixed in
                # solana_pump_shadow.py's twin function (see its comment for
                # the full reasoning): a row reaching here was genuinely
                # BOUGHT but its exit turned unsellable (pool drained).
                # Counting it as "unreachable" dropped it from the total,
                # keeping only the positions that exited cleanly -- textbook
                # survivorship bias, which made a losing set of positions
                # report a large positive percentage. Stranded capital is a
                # LOSS: whatever was banked before the pool dried up is real,
                # the unsold remainder is worth nothing to a seller who
                # cannot sell.
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
        # 17/08, operator request: the percentage alone is a SUM across
        # positions and reads like a portfolio return without being one.
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
