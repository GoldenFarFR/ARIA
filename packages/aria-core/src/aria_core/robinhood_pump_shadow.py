"""Robinhood Chain "take the train" shadow (16/08, explicit operator request)
-- logs, NEVER trades, NEVER opens even a real-simulated paper position.

Functional twin of ``solana_pump_shadow.py`` (read that module's docstring
first -- this one intentionally mirrors its structure, doctrine, and honest
scope limits almost verbatim, only the chain and the RWA exclusion below
differ). Same empirical basis (16/08 Dune backtest + manual DexScreener
comparison, 97.6% win rate / 1.68x average multiplier on 42 real Base
signals, calibrated on the SAME sample -- **ENCOURAGING BUT NOT VALIDATED**,
classic overfitting risk, no trading costs, no out-of-sample test yet until
shadow layers like this one accumulate real forward data).

**Why Robinhood Chain, why shadow-only**: same "genuinely unseen tokens"
framing as the Solana module -- the most honest out-of-sample validation is
prospective data on tokens NEVER seen during calibration (the 42-signal Dune
sample was Base-only), not a historical backtest where the threshold could
have been unconsciously fit. Robinhood Chain is a SECOND, independent pool of
unseen tokens (distinct venue, distinct liquidity/participant profile from
Solana) -- ``momentum_entry.DEFAULT_CHAINS`` stays ``("base",)`` only
(verified live 16/08, unchanged by this module), no other sourcing/discovery
path here reads Robinhood Chain data outside the dormant exclusion registry
(``services/robinhood_stock_tokens.py``, #309) this module reuses below.

**Mandatory RWA (tokenized-equity) exclusion, distinct from the Solana
module**: Robinhood Chain natively hosts 200+ "Stock Tokens" (NVDA, AAPL,
GOOG... ERC-8056 tokenized equities, see ``services/robinhood_stock_tokens.py``
for the full detection story, #309). The calibrated "+25%/5min, scale-out
ladder, trailing stop" rule was backtested on memecoin pump/dump behavior --
a tokenized equity's price action is driven by the underlying security's
real-world market and corporate actions, not a memecoin momentum pattern, so
it makes no sense to feed one into this shadow layer even in pure observation
mode. ``record_signals`` below calls ``robinhood_stock_tokens.is_stock_token``
(the SAME registry already wired into ``momentum_entry.evaluate_hard_gates``
for the real pipeline, #309) and skips any pool whose base token is a known
Stock Token -- reused, never duplicated. **Honest limit**: a pool whose
``token_address`` is unknown (``None``, e.g. a malformed trending-pool
response row) cannot be checked against the registry and is NOT excluded on
that basis alone (``is_stock_token`` itself returns ``False`` for an empty/
missing contract, same fail-open convention as its own module) -- this
mirrors ``record_signals``'s existing "never fabricate" doctrine rather than
inventing a block on missing data, but is stated explicitly here since it is
the one honest gap in an otherwise-enforced filter.

**Absolute bright line (never crossed by this module)**:
- Never calls ``paper_trader.open_position`` or any other position-opening
  function, real or simulated.
- Never calls ``wallet_guard``/``agent_wallet_pilot``/anything that could
  move real capital.
- Never reads from or writes to any table another pipeline treats as a
  trading signal -- ``robinhood_pump_shadow_log`` is a dedicated, standalone
  table, read by nothing else in the codebase.
- ``run_cycle()`` is a plain async function, callable manually or by a
  future test/cron -- **deliberately NOT wired into ``heartbeat.py`` by this
  change**. Wiring it in is a separate, explicit follow-up step (left to a
  future session/operator go), under a dedicated gate name reserved here by
  convention only: ``ARIA_ROBINHOOD_PUMP_SHADOW_ENABLED`` (not read anywhere
  yet, not set in any ``.env`` -- naming it here is documentation, not
  activation).

Three-pass design, identical to ``solana_pump_shadow.py``:
1. ``record_signals()`` -- called with already-fetched
   ``GeckoTerminalClient.get_trending_pools()`` results, logs one row per pool
   whose ``price_change_percentage.m5 >= M5_SURGE_THRESHOLD_PCT``, excluding
   known Robinhood Chain Stock Tokens (see above). Dedupes per
   ``(pool_address, chain)``: an already-OPEN signal for the same pool is
   never re-logged while still running.
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
from aria_core.paper_trader import _advance_high_water
from aria_core.paths import shadow_db_path
from aria_core.services import dexpaprika, dexscreener, doppler
from aria_core.services.evm_swap_ws import EVMSwapWebSocketFeed
from aria_core.skills import chain_liquidity_regime
from aria_core.services.geckoterminal import (
    GeckoTerminalClient,
    OHLCVResult,
    PoolSnapshot,
    TrendingPool,
    geckoterminal_client,
)
from aria_core.services.robinhood_stock_tokens import is_stock_token

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
TABLE = "robinhood_pump_shadow_log"

# Calibrated threshold from the 16/08 Dune/DexScreener research pass (see
# module docstring) -- the ONLY entry signal this shadow layer evaluates.
# Recalibrated same day from the 15min to the 5min window (second Dune pass,
# same exit methodology, beat 15min on both winrate and avg multiplier).
M5_SURGE_THRESHOLD_PCT = 25.0

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
# 23/08 -- TIGHTENED 25 -> 6 minutes, on the pocket's own 133 executable
# closures (17 hours, ONE day -- see the caveat at the end).
#     age < 6 min   92 trades (69% of volume)  +28.39%/trade  +21.39% w/o top5
#                   winrate 66.3%, 100% of hours positive, worst hour +7.21%,
#                   ZERO hour with the pocket idle
#     age >= 6 min                              far weaker on every measure
# 25 minutes was never calibrated -- it was a rug-protection window borrowed
# from the Solana twin (16/08), doing a different job. The freshness of the pool
# turns out to be this pocket's strongest single signal, and unlike an entry
# filter searched over many columns it was predicted BEFORE being measured (the
# operator asked for it: "la tranche a trader est peut etre differente").
# 23/08, SAME DAY, CORRECTED 6 -> 10 after the operator asked the right
# question: does 6 minutes strangle the pocket? It does not -- it keeps 65% of
# the flow -- but it was the WRONG PLACE, and for the exact reason that killed
# seven findings earlier the same day: it bought a better AVERAGE by shrinking
# the base. Full sweep, liquidity floor applied throughout:
#      4 min   28 trades  +54.63%/trade   1530 pts   winrate 96.4%  (11h only)
#      5 min   60 trades  +35.13%/trade   2108 pts   winrate 73.3%
#      6 min   93 trades  +28.08%/trade   2611 pts   winrate 65.6%
#     10 min  130 trades  +24.98%/trade   3248 pts   winrate 64.6%   <- chosen
#     20 min  137 trades  +25.68%/trade   3518 pts   winrate 62.8%
# The average falls as the window widens while the TOTAL rises, which is the
# signature of a filter cutting real gains. What 6 min threw away was measured
# and it was not noise: 44 trades at +20.62%, winrate 56.8%, liquidity fine --
# 907 points of genuine profit, above the operator's own +20% floor.
# 10 recovers 637 of those points, stays at +25% (well clear of the floor) and
# lifts throughput from ~5.6 to ~7.6 entries/hour. 4 min is tempting and is
# NOT taken: 28 trades over 11 of 17 hours is a sample to overfit, not to
# calibrate on.
# CAVEAT that must travel with this number: ONE day of data. Re-read it once a
# second day exists.
MAX_POOL_AGE_MINUTES = 10.0

# 23/08 -- THE POCKET HAD NO LIQUIDITY FLOOR AT ALL, and it was its biggest
# defect. 52 of 200 closures (26%) ran on pools whose MEAN reserve was $6.40.
# Its three "best" trades ever -- +879%, +313%, +289% -- sat on pools of $3.52,
# $20.06 and $17.15, one of them reporting a +85911% peak. That is not profit,
# it is noise on a pool you can neither enter nor exit, and it carried 38% of
# the pocket's headline PnL. Removing it is a MEASUREMENT correction, not a
# performance filter: those points never existed.
#     no floor       200 trades  +31.10%/trade  winrate 53.7%
#     >= $4000       123 trades  +25.42%/trade  winrate 61.8%  (+19.27% w/o top5)
# Set to the same 4000 as the Solana twin, but calibrated HERE and not copied:
# it is where the winrate peaks while still keeping 61% of the flow.
MIN_LIQUIDITY_USD = 4000.0

# 26/08 -- MIN_LIQUIDITY_USD above was calibrated on the DexPaprika "trending
# pools" population (already-established pools with real volume). The day-zero
# discovery feed (specs/006, live since 25/08) sees a structurally different
# population: pools at the SECOND of creation, before anyone has had time to
# deposit real liquidity. Applying the same 4000$ floor there blocked ~100% of
# the flow for 15h+ (measured: 318 real rejections in fresh_launch_pretrade_
# gate_log, reserve_usd from $0 to $3996.5, MEDIAN NEAR ZERO, p75=$134, p90=
# $2460 -- confirming the two populations cannot share one threshold).
#
# The existing 10-minute maturation window (_OBSERVATION_WINDOW_SECONDS in
# services/onchain_pool_discovery.py) already retries a candidate every cycle
# before it expires -- verified in code, not assumed -- so timing was already
# handled correctly. The defect was purely the threshold.
#
# $200 is a PROVISIONAL, conservative floor (Doctrine d'Ingestion): high enough
# to reject genuine dust/never-funded pools (the pre-23/08 defect above, mean
# reserve $6.40, must never reopen), low enough that the measured day-zero
# population can actually clear it. CAVEAT: the 318-row sample above is
# left-censored (only rejections at the OLD 4000$ floor are visible -- the
# true qualifying population's shape is still unknown). RECALIBRATE once this
# path accumulates n>=100 day-zero closures (pocket_entry_sweep, same
# statistical guardrails as everywhere else in this project) -- and note the
# spec's own closure bar is separate and higher: the +25%/trade target is only
# considered validated, and specs/010 only closeable, once the AVERAGE across
# >=1000 same-epoch closures reaches it (see specs/010-robinhood-dayzero-
# liquidity/spec.md SC-005). "Same epoch" = since the last archive/reset
# triggered by a trading-style-affecting parameter change -- this fix itself
# starts a new epoch (see docs/HANDOFF_PIPELINE_MOMENTUM.md, 2026.08.26 entry).
MIN_LIQUIDITY_USD_DAY_ZERO = 200.0

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

# 25/08 -- specs/004-shadow-robinhood T003, see the peak-ratchet comment
# below for the full rationale. A candidate high more than this many times
# the last CONFIRMED peak, in a SINGLE cycle, must hold for
# paper_trader.HIGH_WATER_CONFIRMATION_SECONDS before it's trusted -- a
# genuine pump always ratchets gradually cycle over cycle, never jumps this
# far in one leap (the 2 confirmed artifacts were 20.6x/856x in one cycle).
_PEAK_JUMP_SUSPECT_RATIO = 10.0

# 24/08, operator-directed diligence finding: MIN_LIQUIDITY_USD only guards
# the ENTRY -- nothing here protected an already-open position against the
# pool's reserve collapsing mid-life. Full-population diligence (n=116,
# 2026-08-23->24) found 45/116 closes (38.8%) got stranded under a realistic
# price-impact simulation even though their ENTRY reserve averaged $19-22k,
# well above the $4000 floor -- the pool dies AFTER entry, not at it. Same
# safety net as solana_support_bounce_shadow.py's own LIQUIDITY_COLLAPSE_
# EXIT_PCT, same value, BORROWED not independently calibrated (Doctrine
# d'Ingestion: a conservative hypothesis beats leaving a measured gap
# unguarded) -- this pocket has zero prior closes under this rule to
# calibrate its own threshold from. RECALIBRATE once this pocket accumulates
# >=100 liquidity_collapse closes of its own.
# Honest residual (never to be glossed over): unlike the Solana twin's single
# "not is_pumpswap" exclusion, this check can silently never fire for a v3/v4
# pool being priced via the EVM websocket feed (evm_swap_ws.py's own
# EVMSwapSnapshot.reserve_usd is populated for v2 only -- concentrated
# liquidity has no single "total reserve" figure, never fabricated here
# either). It still fires normally whenever the REST fallback (DexPaprika/
# GeckoTerminal, which DO report a reserve figure for v3/v4) is the live
# source for a given check -- a real but partial gap, not a fabricated fix.
LIQUIDITY_COLLAPSE_EXIT_PCT = 50.0

# 26/08 -- verified this pocket already has an equivalent guard against the
# same failure class that hit base_momentum_shadow.py the same day
# (corrupted AMM ratio-of-reserves price, "+707006.8% nominal" reading):
# `_PEAK_JUMP_SUSPECT_RATIO`/`_advance_high_water` below already routes an
# implausible jump through a multi-cycle confirmation instead of baking it
# straight into peak_price. Nothing to port here -- checked before assuming
# the gap found on the Base twin applied everywhere.
#
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
# Robinhood Chain memecoin launchpads: Uniswap's own pools.trade charges
# 0.25% (Uniswap v4 base fee), Robinpad charges 1% (Uniswap v3 LP fee) --
# 1.0% used as the conservative middle-to-higher estimate across the real
# launchpads observed on this chain. Sourced 17/08 (crypto.news/coinreporter
# pools.trade launch coverage, robinpad.app docs), never assumed from memory.
DEX_FEE_PCT = 1.0


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
    # 24/08 -- the DEX family (uniswap_v2/v3/v4/aerodrome/...) already came
    # back on the same dexpaprika.get_trending_pools() response used at
    # signal time (see TrendingPool.dex_id), just never stored. Needed to
    # call evm_swap_ws.add_pool() at exit-tracking time without a redundant
    # network call to re-discover it -- same addition as base_momentum_
    # shadow.py's own twin column, same day.
    ("dex_id", "TEXT"),
    # 24/08 -- the reserve_usd read at each exit-simulation pass, so
    # LIQUIDITY_COLLAPSE_EXIT_PCT's own trigger is auditable after the fact
    # (same pattern as solana_support_bounce_shadow.py's twin column). NULL
    # until this row's first exit-simulation pass after this column existed.
    ("last_reserve_usd", "REAL"),
    # 25/08 -- real bug confirmed live (specs/004-shadow-robinhood, T001):
    # 2 of the top-20 closed trades had a stored peak_price far above their
    # pool's real historical high (verified against GeckoTerminal OHLCV,
    # up to 856x inflated on the #1-ranked trade), each anchoring a
    # trailing-stop sell at a price that never existed. A single abnormal
    # instantaneous reading (the same class of bug paper_trader.py's
    # _advance_high_water already fixed for the real $1M portfolio, 07/19)
    # could freeze a fictitious peak here too, on a single spot/OHLCV read
    # this module never independently confirmed. Reused verbatim rather
    # than re-invented: a candidate high must hold for
    # HIGH_WATER_CONFIRMATION_SECONDS before it ratchets peak_price -- see
    # _advance_high_water's own docstring for the full mechanics.
    ("pending_peak_price", "REAL"),
    ("pending_peak_since", "TEXT"),
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
        # 26/08 -- self-healing check, see base_momentum_shadow.py's twin
        # comment: an epoch-reset rename run against a live process leaves
        # this cache stale and every write fails with "no such table" until
        # a restart. One cheap indexed lookup here makes it self-heal instead.
        async with aiosqlite.connect(path) as db:
            cur = await db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='robinhood_pump_shadow_log'"
            )
            if await cur.fetchone():
                return
        _ensured_db_paths.discard(path)
    async with aiosqlite.connect(path) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS robinhood_pump_shadow_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pool_address TEXT NOT NULL,
                token_address TEXT,
                chain TEXT NOT NULL DEFAULT 'robinhood',
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
            for row in await (await db.execute("PRAGMA table_info(robinhood_pump_shadow_log)")).fetchall()
        }
        for name, ddl in _ADDED_COLUMNS:
            if name not in existing:
                await db.execute(f"ALTER TABLE robinhood_pump_shadow_log ADD COLUMN {name} {ddl}")
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_robinhood_pump_shadow_lookup "
            "ON robinhood_pump_shadow_log (pool_address, chain, status)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_robinhood_pump_shadow_detected_at "
            "ON robinhood_pump_shadow_log (detected_at)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_robinhood_pump_shadow_exit_reason "
            "ON robinhood_pump_shadow_log (chain, exit_reason)"
        )
        await db.commit()
    _ensured_db_paths.add(path)


async def _has_open_signal(db: aiosqlite.Connection, pool_address: str, chain: str) -> bool:
    cur = await db.execute(
        "SELECT 1 FROM robinhood_pump_shadow_log WHERE pool_address = ? AND chain = ? AND status = 'open' LIMIT 1",
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
# shadow.py is itself imported BY solana_late_bonding_shadow.py (for
# ``_snapshot_with_fallback``/``_apply_price_impact_and_fee``), so a shared
# import from any of these REST pockets risks a circular dependency the
# moment one of them is reused the same way. The function has no state, so
# duplicating it costs nothing a shared import would have saved.
#
# WHY THIS POCKET CANNOT REUSE LATE_BONDING'S TRACKING MECHANISM: that sensor
# updates its peak via ``bonding_ws_feed.get_snapshot()``, a free in-memory
# read because the pool is already subscribed to a websocket for an
# unrelated reason (exit tracking). This pocket has no such subscription --
# its only view of the market is the REST ``dexpaprika.get_trending_pools()``
# fetch ``robinhood_shadow_loop`` already performs every
# ROBINHOOD_CADENCE_SECONDS=120s to look for NEW candidates, regardless of
# whether anything new is found. That fetch returns up to 25 pools with a
# live price EVERY cycle, so ``advance_regime_candidates_from_pools`` below
# re-reads THAT SAME response for pools already under tracking rather than
# issuing any REST call of its own -- zero marginal throughput.
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
REGIME_CANDIDATES_TABLE = "robinhood_pump_regime_candidates_log"
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
# replay behind the number). This pocket's own table held only 54 rows at
# the time -- still below the n>=100 bar above, so this move is BORROWED
# consistency, not an independent verdict from this pocket's own population.
# Re-verify once this table clears 100 rows.
REGIME_MIN_MEDIAN_PEAK_PCT: float | None = 25.0

_ensured_regime_candidates_db_paths: set[str] = set()


async def _ensure_regime_candidates_table(db_path: str | None = None) -> None:
    path = db_path or _db_path()
    if path in _ensured_regime_candidates_db_paths:
        # 26/08 -- self-healing check, same rationale as _ensure_table above.
        async with aiosqlite.connect(path) as db:
            cur = await db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (REGIME_CANDIDATES_TABLE,),
            )
            if await cur.fetchone():
                return
        _ensured_regime_candidates_db_paths.discard(path)
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
        logger.info("robinhood_pump_shadow: record_regime_candidate failed (%s)", exc)


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


async def record_signals(
    pools: list[TrendingPool], *, chain: str = "robinhood", client: GeckoTerminalClient | None = None,
    entry_mode: str = "m5_surge",
) -> int:
    """Logs one shadow row per pool crossing ``M5_SURGE_THRESHOLD_PCT`` on
    its 5-minute price change -- pure read+log, see the module's bright-line
    doctrine. Excludes any pool whose base token is a registered Robinhood
    Chain Stock Token (``services/robinhood_stock_tokens.is_stock_token``,
    #309) -- this shadow layer is calibrated for memecoin pump/dump behavior,
    never a tokenized equity (see module docstring). Best-effort: a DB or
    registry failure here must never break whatever fetched ``pools`` in the
    first place. Returns the number of NEW rows logged (0 on failure or when
    nothing qualifies).

    ``entry_mode`` (25/08, specs/006-onchain-dayzero-entry): default
    ``"m5_surge"`` is the ORIGINAL, unchanged behaviour (requires the 5-minute
    surge below). ``"day_zero"`` skips the m5 check entirely -- used by the
    on-chain discovery feed, whose candidates are freshly-created pools with
    no price history to surge on. Every OTHER filter (age/liquidity/RWA/
    regime/discovery-only) still applies identically regardless of mode."""
    client = client or geckoterminal_client
    logged = 0
    _rows_for_candle_archive: list[tuple[int, TrendingPool]] = []
    try:
        await _ensure_table()
        async with aiosqlite.connect(_db_path()) as db:
            for pool in pools:
                m5 = pool.price_change_pct.get("m5")
                if entry_mode == "day_zero":
                    m5 = M5_SURGE_THRESHOLD_PCT  # bypass, see docstring above
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
                                pocket="robinhood_pump", chain=chain,
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
                        logger.info("robinhood_pump_shadow: rejection log failed (%s)", exc)

                if pool_age_minutes >= MAX_POOL_AGE_MINUTES:
                    # already past the protection window at detection time
                    await _refuse(f"blocked_pool_age: {pool_age_minutes:.1f}min")
                    continue
                # 23/08 -- liquidity floor, see MIN_LIQUIDITY_USD. Placed AFTER
                # the age check so the cheaper test runs first. fail-CLOSED on an
                # unknown reserve: this pocket's whole defect was treating an
                # unmeasurable pool as a tradable one.
                # 26/08 -- day-zero candidates are a structurally different
                # population (see MIN_LIQUIDITY_USD_DAY_ZERO's own comment) --
                # never judge them against the DexPaprika-calibrated floor.
                liquidity_floor = (
                    MIN_LIQUIDITY_USD_DAY_ZERO if entry_mode == "day_zero" else MIN_LIQUIDITY_USD
                )
                if pool.reserve_usd is None or pool.reserve_usd < liquidity_floor:
                    await _refuse(f"blocked_thin_liquidity: reserve={pool.reserve_usd or 0:.0f}")
                    continue
                try:
                    if await is_stock_token(pool.token_address or "", chain):
                        continue  # tokenized equity, not a memecoin -- out of scope for this shadow
                except Exception as exc:  # noqa: BLE001 -- the RWA filter must never break the log pass
                    logger.info(
                        "robinhood_pump_shadow: is_stock_token check failed for %s (%s)",
                        pool.token_address, exc,
                    )
                if await _has_open_signal(db, pool.pool_address, chain):
                    continue  # dedupe: an ongoing pump isn't re-logged every cycle

                # 23/08 -- regime gate, see the REGIME_MIN_MEDIAN_PEAK_PCT
                # block above. This candidate cleared every OTHER filter
                # (age, liquidity, RWA exclusion, dedupe), so it is exactly
                # the population the sensor needs -- logged UNCONDITIONALLY
                # before the verdict below, a gate must never decide its own
                # sensor's input.
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
                # skills/chain_liquidity_regime.py's module docstring. Same
                # reasoning as base_momentum_shadow.py's own 25/08 addition:
                # the gate above is ENDOGENOUS and blind right after a reset,
                # this one reads DefiLlama's real chain TVL/volume instead.
                # Fail-open on anything but a CONFIRMED toxic spike.
                chain_regime = await chain_liquidity_regime.latest_regime(chain)
                if chain_regime and chain_regime["regime"] == chain_liquidity_regime.REGIME_TOXIC_SPIKE:
                    await _refuse(f"blocked_regime_defillama: {chain_regime['detail']}")
                    continue

                # 25/08 -- last link on purpose, same reasoning as the regime
                # gates above. See shadow_discovery_only.py's module docstring --
                # discovery/rejection-logging/regime-candidate tracking above
                # this line are all unaffected.
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
                    INSERT INTO robinhood_pump_shadow_log (
                        pool_address, token_address, chain, symbol, status,
                        detected_at, entry_price,
                        m5_pct, m15_pct, m30_pct, h1_pct, h6_pct, h24_pct,
                        buyers_m15, sellers_m15, volume_usd_m15, reserve_usd,
                        remaining_qty, realized_proceeds, peak_price, next_scale_level,
                        pool_created_at, realistic_entry_price, dex_id
                    ) VALUES (?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1.0, 0.0, ?, ?, ?, ?, ?)
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
                        pool.pool_created_at.isoformat(), realistic_entry_price,
                        pool.dex_id,
                    ),
                )
                new_id = cur.lastrowid
                logged += 1
                _rows_for_candle_archive.append((new_id, pool))
            await db.commit()

        # 24/08, standing convention since 18/08 (every shadow module wires
        # shadow_candle_archive) -- archives the "before" candles each entry
        # decision was actually based on. Deliberately done AFTER the
        # ``async with aiosqlite.connect`` block above has already closed
        # (not inside the loop that produced ``_rows_for_candle_archive``):
        # a network call made while that connection is still open would be
        # exactly the "connection-hold-time" anti-pattern
        # solana_support_bounce_shadow.py's own record_signals already had to
        # fix once (17/08) -- this module's entry signal needs no OHLCV call
        # for its own logic (unlike that twin), so this fetch is NOT a free
        # by-product here, only ever run once the DB connection is free.
        # Best-effort: a fetch/archive failure here never un-logs the signal
        # row already committed above.
        for new_id, pool in _rows_for_candle_archive:
            try:
                before_ohlcv: OHLCVResult = await client.get_ohlcv(
                    pool.pool_address, network=chain, mode="scalping_5m",
                )
                if before_ohlcv.available and before_ohlcv.candles:
                    from aria_core import shadow_candle_archive

                    await shadow_candle_archive.store_candles(
                        module="robinhood_pump", position_id=new_id,
                        pool_address=pool.pool_address, chain=chain, phase="before",
                        candles=before_ohlcv.candles,
                    )
            except Exception as exc:  # noqa: BLE001 -- archiving must never break the signal log
                logger.info(
                    "robinhood_pump_shadow: before-candle archive failed for %s (%s)",
                    pool.pool_address, exc,
                )
    except Exception as exc:  # noqa: BLE001 -- shadow logging must never break the caller
        logger.info("robinhood_pump_shadow: record_signals failed (%s)", exc)
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
    argument already proven on Solana via pumpswap_ws.py, and now on Base's
    twin module the same day). Returns None (never a fabricated snapshot)
    when the pool isn't tracked yet (the caller is expected to have already
    called ``add_pool`` -- see ``advance_exit_simulation``), hasn't ticked
    yet, or its USD leg can't be resolved -- the caller's cue to fall
    through to the existing REST cascade unchanged."""
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
    approximation from % variations, never real wicks). Real incident this
    whole cascade fixes: this exact pool (Robinhood SQUIRREL) 429'd on every
    GeckoTerminal attempt across many consecutive cycles, leaving its
    exit-sim permanently unchecked. Never a third silent fabrication: both
    sources failing still returns ``available=False``, same "never fabricate
    a price" doctrine as the rest of this module.

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
                "robinhood_pump_shadow: dexscreener primary lookup failed for %s (%s)", pool_address, exc,
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
                        "robinhood_pump_shadow: GeckoTerminal reserve backfill failed for %s (%s)",
                        pool_address, exc,
                    )
                if reserve_usd is None:
                    # 18/08 -- twin of solana_pump_shadow.py's own 3rd-source
                    # fallback (see its comment for the SadDog incident that
                    # prompted this). DexPaprika's real Robinhood Chain
                    # coverage isn't separately confirmed -- best-effort,
                    # degrades to None on any failure, same as every other
                    # backfill attempt here.
                    try:
                        reserve_usd = await dexpaprika.get_pool_reserve_usd(pool_address, network=chain)
                    except Exception as exc:  # noqa: BLE001 -- best-effort backfill, never blocks the primary snapshot
                        logger.info(
                            "robinhood_pump_shadow: DexPaprika reserve backfill failed for %s (%s)",
                            pool_address, exc,
                        )
            return PoolSnapshot(
                pool_address=pool_address, price_usd=pair.price_usd,
                reserve_usd=reserve_usd, available=True, dex_id=pair.dex_id,
            )
    return await client.get_pool_snapshot(pool_address, network=chain)


async def evaluate_open_signals(
    client: GeckoTerminalClient | None = None, *, chain: str = "robinhood", limit: int = 50,
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
                SELECT * FROM robinhood_pump_shadow_log WHERE chain = ? AND status = 'open'
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
                    "robinhood_pump_shadow: get_pool_snapshot failed for %s (%s)", row["pool_address"], exc,
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
                        UPDATE robinhood_pump_shadow_log SET
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
                        UPDATE robinhood_pump_shadow_log SET
                            forward_price_{due_horizon} = ?, forward_pct_{due_horizon} = ?,
                            forward_{due_horizon}_measured_at = ?
                        WHERE id = ?
                        """,
                        (snapshot.price_usd, forward_pct, now_iso, row["id"]),
                    )
                await db.commit()
            counts[f"measured_{due_horizon}"] += 1
    except Exception as exc:  # noqa: BLE001 -- shadow measurement must never raise into a caller
        logger.info("robinhood_pump_shadow: evaluate_open_signals failed (%s)", exc)
    return counts


async def advance_exit_simulation(
    client: GeckoTerminalClient | None = None, *, chain: str = "robinhood", limit: int = 50,
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
                "SELECT * FROM robinhood_pump_shadow_log WHERE chain = ? AND exit_reason IS NULL "
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
                        "robinhood_pump_shadow: ws_feed.add_pool failed for %s (%s)",
                        row["pool_address"], exc,
                    )

            try:
                snapshot: PoolSnapshot = await _snapshot_with_fallback(
                    client, row["pool_address"], row["token_address"], chain=chain,
                    ws_feed=ws_feed, dex_id=row.get("dex_id"),
                )
            except Exception as exc:  # noqa: BLE001 -- one pool's failure never blocks the batch
                logger.info(
                    "robinhood_pump_shadow: advance_exit_simulation snapshot failed for %s (%s)",
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
                    "robinhood_pump_shadow: advance_exit_simulation get_ohlcv failed for %s (%s)",
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
                    # 24/08, standing convention since 18/08 (every shadow
                    # module wires shadow_candle_archive): zero extra network
                    # cost, these candles were already fetched above for the
                    # window high/low. Lets a future session actually
                    # re-simulate TRAILING_STOP_PCT/LIQUIDITY_COLLAPSE_EXIT_PCT
                    # at alternate values against the real price path, closing
                    # the exact gap this pocket's own exit diligence (24/08)
                    # flagged as blocking any honest re-simulation.
                    try:
                        from aria_core import shadow_candle_archive

                        await shadow_candle_archive.store_candles(
                            module="robinhood_pump", position_id=row["id"],
                            pool_address=row["pool_address"], chain=chain, phase="after",
                            candles=new_candles,
                        )
                    except Exception as exc:  # noqa: BLE001 -- archiving must never break the batch
                        logger.info(
                            "robinhood_pump_shadow: after-candle archive failed for %s (%s)",
                            row["pool_address"], exc,
                        )

            # Fold the window with the literal current spot -- covers both a
            # closed candle the ladder hasn't reached yet AND a fresh tick
            # that hasn't formed a closed candle yet.
            effective_high = max(window_high, current_price)
            effective_low = min(window_low, current_price)

            confirmed_peak_price = row["peak_price"] or entry_price
            pending_peak_price = row.get("pending_peak_price")
            pending_peak_since = row.get("pending_peak_since")
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

            # 25/08 -- confirmation ratchet on SUSPECT jumps only
            # (specs/004-shadow-robinhood, T003): the 2 confirmed price
            # artifacts (20.6x/856x above the pool's real historical high,
            # each anchoring a fictitious trailing-stop sell) both jumped far
            # past the last confirmed peak in a SINGLE cycle -- a genuine
            # pump, however extreme its eventual multiple, always ratchets
            # peak_price gradually cycle over cycle, never in one leap past
            # _PEAK_JUMP_SUSPECT_RATIO. A normal cycle keeps the original
            # instant max() (unchanged behavior); only a leap this large
            # routes through paper_trader._advance_high_water's confirmation
            # gate (same mechanism already protecting the real $1M
            # portfolio from this exact bug class, never a re-invented
            # copy) before it's trusted enough to ratchet.
            if confirmed_peak_price > 0 and effective_high > confirmed_peak_price * _PEAK_JUMP_SUSPECT_RATIO:
                peak_price, pending_peak_price, pending_peak_since = _advance_high_water(
                    confirmed_peak_price, pending_peak_price, pending_peak_since,
                    effective_high, datetime.now(timezone.utc),
                )
            else:
                peak_price = max(confirmed_peak_price, effective_high)
                pending_peak_price, pending_peak_since = None, None

            # 24/08 -- liquidity_collapse, top priority, checked BEFORE even
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
                    UPDATE robinhood_pump_shadow_log SET
                        peak_price = ?, pending_peak_price = ?, pending_peak_since = ?,
                        next_scale_level = ?, remaining_qty = ?,
                        realized_proceeds = ?, exit_reason = ?, final_multiplier = ?,
                        realistic_realized_proceeds = ?, realistic_final_multiplier = ?,
                        last_checked_at = ?, last_price = ?, window_volume_usd = ?,
                        last_reserve_usd = ?
                    WHERE id = ?
                    """,
                    (
                        peak_price, pending_peak_price, pending_peak_since,
                        next_scale_level, remaining_qty,
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
                # own remove_pools and base_momentum_shadow.py's twin (216-
                # pool leak incident, 19/08). Cheap no-op if this pool was
                # never WS-tracked.
                try:
                    await ws_feed.remove_pool(row["pool_address"])
                except Exception as exc:  # noqa: BLE001 -- best-effort cleanup, never blocks a close
                    logger.info(
                        "robinhood_pump_shadow: ws_feed.remove_pool failed for %s (%s)",
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
        logger.info("robinhood_pump_shadow: advance_exit_simulation failed (%s)", exc)
    return counts


async def run_cycle(
    client: GeckoTerminalClient | None = None, *, network: str = "robinhood", duration: str = "5m",
) -> dict[str, int]:
    """One full shadow passage: fetch Robinhood Chain's currently-trending
    pools, log any new +25%/15min signal (excluding known Stock Tokens, see
    module docstring), then advance BOTH forward-measurement passes on
    already-open signals -- the m15/h1/h2 proxy (``evaluate_open_signals``)
    AND the calibrated exit-rule simulation (``advance_exit_simulation``),
    two complementary angles on the same signals, neither replacing the
    other. Self-contained (no caller needed to sequence the steps itself) --
    but, per the module's bright-line doctrine, this function is NOT called
    by ``heartbeat.py`` in this change; wiring it in (under the reserved
    ``ARIA_ROBINHOOD_PUMP_SHADOW_ENABLED`` gate name) is an explicit
    follow-up left to a future step."""
    client = client or geckoterminal_client
    result = await client.get_trending_pools(network=network, duration=duration)
    logged = 0
    if result.available:
        logged = await record_signals(result.pools, chain=network, client=client)
    else:
        logger.info("robinhood_pump_shadow: get_trending_pools unavailable (%s)", result.error)
    measured = await evaluate_open_signals(client, chain=network)
    exit_sim = await advance_exit_simulation(client, chain=network)
    return {"fetched_pools": len(result.pools), "signals_logged": logged, **measured, "exit_sim": exit_sim}


async def summary(chain: str = "robinhood") -> dict:
    """Aggregate read for session/monitoring use -- never called from a real
    trading path. ``win_rate_h2``/``avg_multiplier_h2`` are the real
    out-of-sample numbers this shadow layer exists to produce, computed only
    over CLOSED signals (a real, complete 2h forward measurement), never
    estimated from open/incomplete rows."""
    await _ensure_table()
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT status, forward_pct_h2 FROM robinhood_pump_shadow_log WHERE chain = ?", (chain,)
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


async def exit_simulation_summary(chain: str = "robinhood") -> dict:
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
            "SELECT exit_reason, final_multiplier FROM robinhood_pump_shadow_log "
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


async def chain_pnl_summary(chain: str = "robinhood") -> dict:
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
            "last_price, exit_reason FROM robinhood_pump_shadow_log WHERE chain = ?",
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


async def chain_pnl_summary_realistic(chain: str = "robinhood") -> dict:
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
            "realistic_final_multiplier, last_price, exit_reason FROM robinhood_pump_shadow_log WHERE chain = ?",
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
