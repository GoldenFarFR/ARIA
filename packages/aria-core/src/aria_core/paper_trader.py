"""$1M paper portfolio (TRADING mode) — the proof-of-concept test bench.

ARIA applies her REAL reports to a FICTITIOUS $1,000,000 portfolio: she opens and
closes imaginary positions at the REAL market price, issues CLEARLY FICTITIOUS buy
and sell alerts, and measures her performance over time. Goal: prove performance
over ~20 days BEFORE any real money (pact docs/protocole-argent-reel.md).

TRADING mode (not VC): short horizon, levels derived from real analysis. Position
management via TRAILING STOP (tightens with the highest price reached, never
relaxes below the original invalidation) + STAGED PROFIT-TAKING (sells in thirds
at +50%, +100%, +200% gain rather than all-or-nothing at the target) — protects
gains already made without cutting off remaining potential. NO on-chain execution,
NO signing, NO real money — simulation persisted locally (aria.db). The market
price is real; the orders are fictitious.
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
from datetime import datetime, timezone

import aiosqlite

from aria_core import momentum_funnel_log
from aria_core.paths import aria_db_path
from aria_core.services.dexscreener import token_url

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())

STARTING_CAPITAL_USD = 1_000_000.0
ALLOC_PCT = 0.05          # 5% of starting capital per position (~$50,000) — trading mode
# 25/07 -- raised 15 -> 30 for the operator's one-off 24h aggressive test
# ("trader un maximum de tokens"): with the daily-floor sizing now dynamic
# (risk/ATR, see compute_entry_alloc) instead of a fixed 1%, real cash
# availability becomes the natural brake before this count-based cap is ever
# hit -- this just removes an artificial ceiling below that real brake.
MAX_POSITIONS = 30        # cash cushion + diversification -- still read by the single-
# pocket gate-OFF path below AND by limit_orders.py (its own defense-in-depth check,
# a separate later chantier -- kept defined and unused-here rather than removed).

# 27/07 -- 3-pocket architecture plan, Phase 2 (scalping/swing/VC, each an
# independent $1M paper wallet, gated by ``multi_pocket_sourcing_enabled()``
# below): per-POCKET position caps, replacing the single portfolio-wide
# ``MAX_POSITIONS`` above for the new concurrent-sourcing loop. Natural caps
# per pocket (operator-approved plan, not numerically enforced elsewhere):
# VC is a low-conviction-count, high-conviction-per-position thesis (1-5
# positions); swing (momentum, standard mode) sits between the two; scalping
# stays UNCAPPED -- same doctrine as today's existing "trading_mode ==
# scalping" MAX_POSITIONS bypass (Item #101, 26/07: "laisse libre, voyons
# comment ARIA trade sans la force" -- real cash availability remains the
# natural brake).
MAX_POSITIONS_VC = 5
MAX_POSITIONS_SWING = 15
MAX_POSITIONS_SCALPING = None  # unlimited, same doctrine as the scalping bypass above
MODE = "trading"

# 07/18 -- explicit operator decision: replaces the 30d/7d/14d protocol. ARIA restarts at
# $1M EVERY week, target +10% ($1.1M) VALIDATED every week -- a repeated TRAINING loop
# (never a one-time exit gate to cross once). The reset happens whether the week was
# validated or not -- same diagnostic philosophy as #194 (push ARIA to make mistakes/
# learn rather than over-filter out of excess caution).
WEEKLY_CYCLE_DAYS = 7
WEEKLY_TARGET_MULTIPLIER = 1.10

# 07/22 -- Task 2 (option 3, explicitly confirmed by the operator after a 3-option
# proposal): SATELLITE POCKET. A position that still has real potential intact at
# the moment of the weekly reset (solid remaining R/R, ATR stop not touched, ratchet
# regime still Euphoria) is no longer force-closed like the rest -- it's set aside
# in a SEPARATE and CAPPED pocket, excluded from the MAIN week's +10% verdict
# calculation (never a way to artificially postpone a weekly failure: the satellite
# pocket counts NEITHER for NOR against `validated`/`return_pct`) and never wiped by
# the weekly archiving -- it continues its life under normal management (ATR trailing
# stop + staged TP) until its own close, on its own schedule, independent of the 7-day
# calendar. Thresholds deliberately conservative for a first round (v1) -- to adjust
# once observed under real conditions.
SATELLITE_POCKET_MIN_RR = 1.5
SATELLITE_POCKET_MAX_PCT_OF_CAPITAL = 0.05  # 5% of fixed starting capital ($1M) -- hard cap, never silently exceeded

# 07/23 -- daily trade FLOOR (explicit operator decision): "for now, force ARIA to
# make at least 5 trades/day so we can judge the tokens she picks, even if she loses."
# A separate additive cycle (``run_daily_trade_floor_cycle``) that NEVER touches the
# normal ``run_paper_cycle`` decision path -- it only tops up small, tagged trades when
# ARIA is behind the daily pace. Forced trades waive the QUALITY bars (relaxed momentum
# eval) but NEVER the SAFETY guardrails (honeypot/blacklist/etc.) -- losing on a weak
# momentum bet is diagnostic, buying a scam is not. Respects the risk circuit breaker
# (operator decision 07/23): stops forcing if the drawdown/consecutive-loss hard stop is
# armed (observing her risk management is itself diagnostic). Gate OFF by default.
# 25/07 -- raised for the operator's one-off 24h aggressive test ("trader un
# maximum de tokens... meme si elle ne respecte pas tous les signaux"): 5/day
# was calibrated for a slow diagnostic trickle, not a volume-maximizing
# 24h window. 30/day paced across 24 hourly heartbeat passes (see
# daily_trade_floor_cycle, interval_minutes=60 in heartbeat.py) needs a higher
# per-pass burst (FLOOR_MAX_OPENS_PER_CYCLE) to actually reach the target
# instead of trickling in at 2/hour. FLOOR_TRADE_ALLOC_PCT removed entirely --
# sizing is now the same dynamic risk/ATR formula as a normal conviction pick
# (compute_entry_alloc), never a fixed 1% ceiling on the upside.
DAILY_TRADE_FLOOR = 30
FLOOR_MAX_OPENS_PER_CYCLE = 5  # was 2 -- raised to actually reach a 30/day target

# 25/07, operator request ("je veux qu'elle sache combien elle a dans son
# portfolio et combien de benefice ou perte elle a realise pour s'auto
# mettre un stress"): midpoint of the operator's own $50k-$100k target for
# this 24h window -- reused as the SAME weekly-pacing context mechanism
# already wired to the LLM prompt (_weekly_pacing_line, "Contexte de rythme"),
# just with this cycle's real numbers (day 1/1, not day X/7) instead of None.
DAILY_FLOOR_TARGET_PROFIT_USD = 75_000.0

# #196 -- SHARED lock, regardless of the caller (heartbeat paper_trade_cycle OR the
# momentum #196 websocket service): without it, two concurrent executions of
# run_paper_cycle() would read the available capital/number of open positions BEFORE
# either one writes -- real risk of double-allocation or exceeding MAX_POSITIONS. Only
# one cycle at a time, never two in parallel.
_run_cycle_lock = asyncio.Lock()

# Position management (trailing stop + staged profit-taking) — replaces the binary
# exit (100% at target OR at invalidation) with management that protects gains
# ALREADY MADE without cutting off remaining potential.
TRAIL_STOP_PCT = 0.15         # DEFAULT trailing stop: 15% below the highest price reached
# since entry -- fallback for any position WITHOUT a known entry_atr_pct (positions
# opened before 07/19, or any analyzer that doesn't provide it, e.g. the old VC-thesis
# pilot). See ATR_TRAIL_MULTIPLIER below for the default adaptive computation.
TP_STAGES = (0.5, 1.0, 2.0)   # gain thresholds vs entry (+50%, +100%, +200%)
TP_STAGE_FRACTION = 1.0 / 3.0  # fraction of the INITIAL quantity sold at each stage
TP_QTY_EPSILON = 1e-9         # negligible remainder after the last stage -> full close

# 07/19 -- volatility-adaptive trailing stop (Gemini cross-review, confirmed "100%
# yes" by the operator): replaces the fixed percentage (TRAIL_STOP_PCT) with a width
# calibrated on each token's REAL volatility (ATR, ``entry_atr_pct`` computed once at
# entry by momentum_entry.py -- never recomputed during the holding period, preserves
# the ratchet effect and avoids any timeframe desync). 2.5x multiplier -- middle of
# the standard 2-3x range cited by Gemini ("2xATR to 3xATR: the industry standard").
# Defensive bounds: a token with near-zero volatility (ATR close to 0) must never
# produce a stop so tight it triggers on the slightest noise (5% floor); an extremely
# volatile token must never produce a stop so wide it protects nothing anymore (40%
# cap, same value as the #187 concentration cap -- coincidence, not a functional
# link).
ATR_TRAIL_MULTIPLIER = 2.5
MIN_ATR_TRAIL_PCT = 0.05
MAX_ATR_TRAIL_PCT = 0.40

# 07/20 -- price freshness at execution (Gemini cross-review, replaces an initial
# blind %-threshold design -- fixed the SAME evening after a 2nd review pass).
# ``sig["price"]`` is captured at the very start of ``evaluate_momentum_entry``
# (before honeypot/holder concentration/OHLCV cascade/up to 2 sequential LLM calls)
# -- on a volatile token, several seconds can pass before this price is actually
# used to open the position.
#
# Root cause of the 1st design (rejected): a blind %-move threshold (3%) treats ANY
# movement as bad, whereas the real question is never "has the price moved" but "is
# the trade still good". A token that pumps even harder while the LLM is thinking
# (exactly the profile step 3 is looking for) would get rejected by a % threshold --
# adverse selection that would filter out the BEST setups, letting through only the
# "soft" configurations that don't move.
#
# Fix: recomputes R/R at the FRESH price using the SAME structural levels (target/
# invalidation, Fibonacci -- fixed, never recomputed) as the entry decision, and
# checks that it still clears the bar THIS signal had originally cleared (2.0 for a
# direct buy, 1.0 for an ambiguous one confirmed by LLM). If the price has risen but
# the target is still far, R/R stays good -> execution. If the price has slightly
# dropped without touching invalidation, R/R mechanically improves (a "discount" on
# the thesis) -> execution. Only rejects a setup that has REALLY degraded (price too
# close to the target or invalidation), never a movement that's simply present.
def _fresh_rr(fresh_price: float | None, target: float | None, invalidation: float | None) -> float | None:
    """R/R recomputed at the fresh price. ``None`` if the config doesn't allow a
    valid computation (missing data, or the setup is already resolved -- price
    beyond the target or already below invalidation, no more R/R to measure at
    this stage)."""
    if not fresh_price or fresh_price <= 0 or not target or not invalidation:
        return None
    if fresh_price <= invalidation or fresh_price >= target:
        return None
    return (target - fresh_price) / (fresh_price - invalidation)


def _execution_rr_still_valid(signal_rr: float | None, fresh_rr: float | None) -> bool:
    """``True`` if ``fresh_rr`` still clears the bar the ORIGINAL signal had
    cleared -- 2.0 (direct buy) if ``signal_rr`` had already reached it, otherwise
    1.0 (the ambiguous floor, cleared via LLM confirmation). ``fresh_rr is None``
    -> fail-closed (never an execution without data to judge from)."""
    if fresh_rr is None:
        return False
    from aria_core.momentum_entry import _RR_AMBIGUOUS_FLOOR, _RR_MIN_FOR_DIRECT_BUY

    bar = _RR_MIN_FOR_DIRECT_BUY if (signal_rr and signal_rr >= _RR_MIN_FOR_DIRECT_BUY) else _RR_AMBIGUOUS_FLOOR
    return fresh_rr >= bar


# 07/20 -- Formula B, VC exit discipline (``strategy="vc_thesis"``, Gemini cross-review,
# explicit operator decision "starting now"): distinct from the momentum discipline
# above (ATR trailing stop + staged TP), reserved for positions that would one day
# come from the 85% VC pocket (``safety_screen``/``vc_analysis``, NOT the momentum
# pipeline active on the current $1M test -- ``strategy`` defaults to "momentum" for
# any existing position/caller, unchanged behavior as long as nothing explicitly
# sources "vc_thesis"). Points refined over 3 back-and-forths with Gemini (relayed by
# the operator):
#   1. Entry/exit paradox resolved STRUCTURALLY: ``strategy`` is derived from the real
#      ENTRY pipeline (momentum_entry.py -> "momentum"; the old _default_analyzer,
#      which comes from safety_screen/vc_analysis -- already fundamentals + safety,
#      NEVER Fibonacci/RSI -- -> "vc_thesis"), never an independent flag that could be
#      mismatched to a purely speculative token.
#   2. FUNDAMENTAL invalidation rather than technical: a chart support level on a
#      young, illiquid pair can be crossed by a simple overnight volatility wick.
#      Pool liquidity (data already on hand every cycle, no extra network call) is a
#      more robust signal -- a pool doesn't lose 50% of its liquidity on a single
#      isolated trade, only on a real withdrawal/rug. $30,000 = same absolute floor
#      as safety_screen.py (85% VC pocket), not a number invented for the occasion.
#   3. "Take Seed" (no mechanical staged TP): a SINGLE partial exit, as soon as the
#      position doubles (2x), that recovers EXACTLY the initial stake -- secures
#      capital for redeployment, lets the rest (moonbag) run WITHOUT a stop toward
#      the thesis's full target (VC Power Law: one x50 pays for all the zeros).
VC_MIN_LIQUIDITY_FLOOR_USD = 30_000.0
VC_LIQUIDITY_DROP_INVALIDATION_PCT = 0.5
VC_TAKE_SEED_MULTIPLE = 2.0

# 07/22 -- task #4, explicit operator decision: POST-ENTRY monitoring of a vc_thesis
# position (until now only liquidity was re-checked during the holding period, see
# VC_LIQUIDITY_DROP_INVALIDATION_PCT above -- nothing monitored the deployer wallet's
# behavior AFTER opening). Two emergency signals, independent of each other, added
# BEFORE the existing checks:
#   1. RECENT deployer sale: delta of sold_pct_of_received (dev_wallet.py) between the
#      entry snapshot and a fresh re-scan -- 10 percentage points is enough (unlike
#      dev_wallet.py's HEAVY_SELL_PCT=50% threshold, meant for a ONE-TIME judgment at
#      entry -- here it's a DEGRADATION during the holding period that matters, so a
#      much lower threshold is justified).
#   2. SUDDEN liquidity drop between two consecutive cycles (30%) -- complements,
#      never replaces, the cumulative check since entry (50%) already in place: an LP
#      withdrawal spread over several weeks in small tranches (never >50% at once
#      since entry at any point) can still represent a real withdrawal in progress,
#      detected here cycle by cycle rather than cumulatively.
VC_DEV_SOLD_DELTA_ALERT_PCT = 10.0
VC_LIQUIDITY_SUDDEN_DROP_PCT = 0.3


def _effective_trail_pct(entry_atr_pct: float | None) -> float:
    """Trailing stop width for ONE position: fixed ``TRAIL_STOP_PCT`` if
    ``entry_atr_pct`` is missing/invalid (unchanged historical behavior), otherwise
    ``ATR_TRAIL_MULTIPLIER * entry_atr_pct`` bounded to ``[MIN_ATR_TRAIL_PCT,
    MAX_ATR_TRAIL_PCT]``."""
    if entry_atr_pct is None or entry_atr_pct <= 0:
        return TRAIL_STOP_PCT
    return max(MIN_ATR_TRAIL_PCT, min(MAX_ATR_TRAIL_PCT, ATR_TRAIL_MULTIPLIER * entry_atr_pct))


def _compute_active_stop(
    *, entry_price: float, entry_atr_pct: float | None, high_water_price: float | None,
    invalidation_price: float | None, breakeven_locked: bool,
) -> tuple[float, str]:
    """ACTIVE stop for a position -- the highest of the ATR trailing stop, the
    original invalidation, and the locked breakeven (extracted from the
    management loop, 07/22, Task 2 satellite pocket, to be reused WITHOUT
    duplicating logic that could diverge -- same philosophy as reusing the
    wash-trading detector).

    READ-ONLY, no side effects: uses ``high_water_price`` AS-IS (the last
    CONFIRMED high reached by the normal management cycle), does no ratcheting
    or DB writing here -- the caller managing an ONGOING position
    (``_run_paper_cycle_locked``) ratchets the high itself BEFORE calling this
    function; a READ-ONLY caller (e.g. satellite pocket eligibility at the
    weekly reset) deliberately uses it as-is, without advancing the ratchet."""
    trail_pct = _effective_trail_pct(entry_atr_pct)
    high_water = high_water_price or entry_price
    trailing_stop = high_water * (1 - trail_pct)
    active_stop = trailing_stop
    stop_source = "stop suiveur"
    if invalidation_price and invalidation_price > active_stop:
        active_stop = invalidation_price
        stop_source = "invalidation"
    if breakeven_locked and entry_price and entry_price > active_stop:
        active_stop = entry_price
        stop_source = "point mort verrouillé"
    return active_stop, stop_source


def _remaining_reward_risk(
    *, price: float, target_price: float | None, active_stop: float,
) -> float | None:
    """REMAINING R/R from the current price: (target - price) / (price - active
    stop). ``None`` if the target is unknown/already exceeded, or if the stop is
    already touched (risk <= 0) -- never an infinite/negative ratio returned
    silently."""
    if not target_price or target_price <= price:
        return None
    risk = price - active_stop
    if risk <= 0:
        return None
    return (target_price - price) / risk


def _satellite_pocket_eligible(
    pos: dict, price: float | None, current_regime: str,
) -> tuple[bool, float | None]:
    """07/22 -- Task 2 (option 3, explicitly confirmed by the operator). A position
    has real potential still intact if, ALL together:
      1. strategy 'momentum' -- Formula B (vc_thesis, dormant) has neither an ATR
         trailing stop nor a regime notion for now, a separate extension would be
         needed if this path becomes active one day (never assumed identical);
      2. the ATR stop is NOT already touched (``price`` above the active stop,
         see ``_compute_active_stop``);
      3. the REMAINING R/R (not the entry one -- what's left to gain/risk NOW) is
         still >= ``SATELLITE_POCKET_MIN_RR``;
      4. the RATCHETED regime (the more cautious of the one observed at entry and
         now -- never a relaxation, see
         ``market_sentiment.more_cautious_meta_regime``) is still Euphoria.
    Returns (eligible, remaining R/R) -- R/R ``None`` if not computable (never an
    invented ratio). Missing/invalid price -> never eligible (fail-closed, same
    doctrine as the rest of the pipeline: missing data unlocks nothing)."""
    from aria_core.skills import market_sentiment

    if (pos.get("strategy") or "momentum") != "momentum":
        return False, None
    entry_price = pos.get("entry_price")
    if not entry_price or not price or price <= 0:
        return False, None
    effective_regime = market_sentiment.more_cautious_meta_regime(
        pos.get("entry_regime"), current_regime,
    )
    if effective_regime != market_sentiment.META_REGIME_EUPHORIA:
        return False, None
    active_stop, _ = _compute_active_stop(
        entry_price=entry_price,
        entry_atr_pct=pos.get("entry_atr_pct"),
        high_water_price=pos.get("high_water_price"),
        invalidation_price=pos.get("invalidation_price"),
        breakeven_locked=bool(pos.get("breakeven_locked")),
    )
    if price <= active_stop:
        return False, None  # ATR stop already touched -- never eligible
    remaining_rr = _remaining_reward_risk(price=price, target_price=pos.get("target_price"), active_stop=active_stop)
    if remaining_rr is None or remaining_rr < SATELLITE_POCKET_MIN_RR:
        return False, remaining_rr
    return True, remaining_rr


def _effective_tp_stages(target_price: float | None, entry_price: float | None) -> tuple[float, ...]:
    """Profit-taking stages for ONE position -- fixes a real defect found in
    cross-review (07/19, Gemini round 5): the R/R computed at entry
    (``entry_signals.detect_entry``) relies on a real TECHNICAL ``target`` (the
    top of the golden pocket window -- the level the setup was aiming for). But
    the old exit management completely ignored this level: TP1 always fell on a
    FIXED percentage (``TP_STAGES[0]``, +50%), unrelated to the target that had
    justified the entry -- a setup with a high R/R but a closer technical target
    (e.g. +25%) could turn around and hit the trailing stop without any profit
    ever being taken at the level actually aimed for.

    TP1 now anchors on ``target_price`` (converted to % gain from
    ``entry_price``) when both are known and consistent (``target_price >
    entry_price``) -- otherwise falls back to unchanged ``TP_STAGES`` (e.g.
    positions opened before this fix, or any analyzer that doesn't provide a
    technical target, like the old dormant VC-thesis pilot).

    TP2/TP3 (07/19, Gemini cross-review round 6) -- first version: FIXED steps
    above TP1 (+50pt/+100pt, same gap as ``TP_STAGES``). Real defect found by
    Gemini: these steps remained fixed percentage-of-capital points, never
    proportional to the MAGNITUDE of the setup itself -- a modest TP1 (tight
    setup) still kept a very distant TP2 (often beyond what a token reaches
    before turning around), letting an already-earned profit slip away. Replaced
    by MULTIPLES of the entry->TP1 distance itself (``reward_distance``): TP2 =
    2x that distance, TP3 = 3x -- dynamic end to end, an ambitious setup (TP1
    far) gets stages 2/3 proportionally farther, a tight setup (TP1 close) gets
    them proportionally closer, never an arbitrary fixed point. Strictly
    increasing sequence by construction (``stage1_pct > 0`` guaranteed by the
    check above)."""
    if target_price and entry_price and target_price > entry_price:
        stage1_pct = target_price / entry_price - 1.0
        return (stage1_pct, 2.0 * stage1_pct, 3.0 * stage1_pct)
    return TP_STAGES


def _apply_regime_to_tp_stages(
    stages: tuple[float, ...], effective_regime: str | None,
) -> tuple[float, ...]:
    """Transforms the profit-taking stages according to the EFFECTIVE meta-regime
    already ratcheted for this position (see
    ``market_sentiment.more_cautious_meta_regime``, never the raw current regime
    -- a position never becomes more permissive than its worst observed moment).
    Gemini cross-review, explicit operator go-ahead (07/20, "200k but keep an eye
    on it"):

    - Fear: crushes the 3rd stage -- ultra-fast exit, the ENTIRE remainder sells
      at the old TP2 level (locks in gains before a retracement while liquidity
      regroups on large assets). ``stages[:2]`` is enough: the calling loop
      already treats any overshoot of the LAST stage as a full close
      (``is_last_stage``), no extra logic needed.
    - Euphoria: neutralizes the 3rd stage (``float("inf")``, never reachable) --
      TP1/TP2 keep taking their thirds normally, but the last third becomes a
      PURE moon bag, guided only by the ATR trailing stop, never forced to sell
      by a mechanical stage ("she's going for the 10x's").
    - Neutral/unknown: ``stages`` unchanged -- default historical behavior.

    If ``stages`` has fewer than 3 elements (should never happen, ``TP_STAGES``/
    ``_effective_tp_stages`` always provide 3) -> unchanged, never an
    out-of-bounds index."""
    if len(stages) < 3:
        return stages
    if effective_regime == "peur":
        return stages[:2]
    if effective_regime == "euphorie":
        return (stages[0], stages[1], float("inf"))
    return stages


# 07/20 -- Breakeven Hard Floor (Gemini cross-review, "Track B" validated by the
# operator): mechanism SEPARATE from the high-water time confirmation below,
# addresses the blind spot it leaves open. `_advance_high_water` COMPLETELY
# abandons a high-water candidate if the price falls back below the last
# CONFIRMED high before having held for HIGH_WATER_CONFIRMATION_SECONDS (75s, by
# design -- no partial credit): a fast pump-then-dump (e.g. +50% in under 75s)
# therefore leaves the stop anchored at its level FROM BEFORE the peak, even
# though the position genuinely flirted with a significant gain.
#
# This safety net is INDEPENDENT of the high_water ratchet -- it reads the
# INSTANTANEOUS price of EVERY cycle (never the confirmed high), and as soon as
# it touches, even for a single cycle, a "flash" threshold calibrated on the
# setup's technical target, the stop is IRREVOCABLY moved up to breakeven
# (`entry_price`) -- this lock never goes back down, even if the price
# immediately falls back below the threshold that triggered it.
#
# Threshold = BREAKEVEN_FLOOR_TP1_RATIO of the entry->TP1 distance (the
# technical target already used by _effective_tp_stages), with an absolute
# BREAKEVEN_FLOOR_MIN_PCT floor to never trigger on a setup with a very tight
# TP1, where a fraction of its distance would be narrower than normal market
# noise.
BREAKEVEN_FLOOR_TP1_RATIO = 0.5
BREAKEVEN_FLOOR_MIN_PCT = 0.08


def _breakeven_floor_threshold(target_price: float | None, entry_price: float | None) -> float | None:
    """Gain threshold (fraction, e.g. ``0.08`` = +8%) beyond which breakeven
    locks in -- ``None`` if no valid entry price (never a computation on
    missing data)."""
    if not entry_price or entry_price <= 0:
        return None
    stage1_pct = _effective_tp_stages(target_price, entry_price)[0]
    return max(BREAKEVEN_FLOOR_TP1_RATIO * stage1_pct, BREAKEVEN_FLOOR_MIN_PCT)


# 07/20 -- TIME confirmation of the high water mark (replaces the
# HIGH_WATER_JUMP_CAP_MULTIPLE speed cap from 07/19, Gemini cross-review round
# 7). The speed cap itself had a real defect, found by Gemini: capping the
# MAGNITUDE of the jump allowed per cycle penalizes a wick just as much as a
# genuine legitimate parabolic move (a real price-discovery candle can do +50%
# in a single cycle) -- the width of the move is structurally NOT the right
# signal to tell the two apart. DURATION is: an isolated wick (arbitrage bot,
# one-off manipulation on a thin pool) never lasts more than a few seconds/tens
# of seconds; a real parabolic move does. A new high is therefore only
# ratcheted into the trailing stop after staying above the last CONFIRMED high
# for at least HIGH_WATER_CONFIRMATION_SECONDS -- its MAGNITUDE is never capped
# (once confirmed, the REAL high of the entire window is ratcheted in one go,
# not just the price at the moment of confirmation).
#
# Duration in SECONDS, not number of cycles -- the momentum pipeline has two
# position-management loops at different cadences (heartbeat ~15 min, WebSocket
# ~30s, #196): "2 cycles" has no common meaning between the two (30s vs 30 min),
# an absolute duration does. 75s = middle of the 60-90s range proposed by the
# cross-review (enough to let an arbitrage bot disengage, short enough not to
# perceptibly delay confirmation of a real pump at the scale of the management
# cycles). Sourced from momentum_timing.py (07/20, external cross-review) --
# momentum_entry._WASH_TRADING_CONFIRMATION_SECONDS uses the SAME shared
# constant (a direct import the other way is impossible: this module already
# imports from momentum_entry.py, see momentum_timing.py's comment).
from aria_core.momentum_timing import MOMENTUM_CONFIRMATION_SECONDS as HIGH_WATER_CONFIRMATION_SECONDS


def _advance_high_water(
    confirmed_high_water: float,
    pending_high_water: float | None,
    pending_since: str | None,
    price: float,
    now: datetime,
) -> tuple[float, float | None, str | None]:
    """``(new confirmed high, pending high, candidacy timestamp)`` for ONE
    cycle. Fixes a real risk (07/19, Gemini round 6): ARIA re-reads a SPOT price
    (DexScreener, last transaction) on every cycle for position management -- a
    single abnormal instantaneous reading (wick, arbitrage bot, a large buyer's
    slippage error) can freeze a fictitious high in ``high_water`` -- the ratchet
    NEVER goes back down, so the trailing stop would remain durably anchored to
    a price that may have only existed for an instant.

    Mechanics: as long as ``price`` stays above the last CONFIRMED high, a
    candidacy stays "open" (``pending_high_water``/``pending_since``), updated
    to the REAL maximum observed while it's open. As soon as it has held for at
    least ``HIGH_WATER_CONFIRMATION_SECONDS``, it's confirmed at once (the REAL
    high of the entire window, not just the price at that instant) and the
    confirmed high ratchets. If ``price`` falls back BELOW the last confirmed
    high at any point, the current candidacy is entirely abandoned (proof it
    wasn't sustained) -- a new candidacy starts from scratch if the price
    exceeds it again later.

    Affects ONLY the ``high_water`` state (the ratchet) -- the stop-trigger
    comparison always uses the REAL ``price``, never a value pending
    confirmation (an aberrant DOWNWARD reading therefore does trigger the stop
    if it crosses the threshold -- a deliberate choice, safer for simulated
    capital to react to an ambiguous signal than to ignore it)."""
    if price <= confirmed_high_water:
        return confirmed_high_water, None, None

    if pending_high_water is None or not pending_since:
        return confirmed_high_water, price, now.isoformat()

    pending_high_water = max(pending_high_water, price)
    try:
        elapsed = (now - datetime.fromisoformat(pending_since)).total_seconds()
    except ValueError:
        return confirmed_high_water, price, now.isoformat()

    if elapsed >= HIGH_WATER_CONFIRMATION_SECONDS:
        return pending_high_water, None, None
    return confirmed_high_water, pending_high_water, pending_since


def _advance_breakeven_pending(
    pending_since: str | None, price: float, entry_price: float, flash_threshold: float, now: datetime,
) -> tuple[str | None, bool]:
    """``(new candidacy timestamp, lock confirmed THIS cycle?)`` -- same
    time-confirmation mechanics as ``_advance_high_water`` above (07/20,
    external cross-review: breakeven used to lock on a SINGLE instantaneous
    price reading -- an asymmetry flagged against the high_water ratchet, which
    already has ``HIGH_WATER_CONFIRMATION_SECONDS`` of confirmation before
    ratcheting). Reuses the SAME constant -- same philosophy "a real move
    lasts, a wick doesn't," no 2nd magic duration to justify separately.

    As long as ``price`` stays above the flash threshold (``entry_price *
    (1+flash_threshold)``), a candidacy stays open. As soon as it has held for
    at least ``HIGH_WATER_CONFIRMATION_SECONDS``, the lock is confirmed. If
    ``price`` falls back BELOW the threshold at any point before confirmation,
    the candidacy is entirely abandoned (proof it wasn't sustained) -- starts
    from scratch if the price exceeds the threshold again later. Unlike
    ``_advance_high_water``, no magnitude to remember: once confirmed, the lock
    is a boolean (``breakeven_locked``), never a numeric value to ratchet
    higher."""
    threshold_price = entry_price * (1.0 + flash_threshold)
    if price < threshold_price:
        return None, False
    if not pending_since:
        return now.isoformat(), False
    try:
        elapsed = (now - datetime.fromisoformat(pending_since)).total_seconds()
    except ValueError:
        return now.isoformat(), False
    if elapsed >= HIGH_WATER_CONFIRMATION_SECONDS:
        return pending_since, True
    return pending_since, False

# 07/17 -- explicit operator request: halve the Telegram noise from the periodic
# tracking alert (#197, one per heartbeat cycle -- ~15 min -- as long as a
# position stays open). Sliding window by ELAPSED TIME (not a cycle counter):
# robust if the heartbeat cadence changes one day without needing to touch this
# constant.
TRACKING_ALERT_MIN_INTERVAL_MINUTES = 30

# 07/17 -- explicit operator request after a real loss (BRIAN rebought twice in a
# row after two trailing stops, -$18,561 cumulative over 3 entries): rebuy
# blocked by default unless an EXTREME signal. Relaxed on 07/19 (explicit
# operator decision, following direct observation of the real portfolio):
# "single buy for CURRENTLY-open positions [only] -- I don't mind reopening a
# position if one doesn't already exist, if a new entry point comes up." The
# only protection against double-holding remains ``has_open`` (never two
# SIMULTANEOUS positions on the same contract) -- once closed, a contract
# becomes a candidate like any other, same bar as any normal entry (already
# passed before reaching this point in the pipeline). BRIAN-style wash-trading/
# decoy remains covered by two distinct HARD guards not removed here
# (`momentum_blacklist.py`, volume24h/liquidity ratio cap) -- built specifically
# for this pattern, never dependent on this re-entry gate.

_POS_FIELDS = (
    "id", "contract", "symbol", "cost_usd", "entry_price", "qty",
    "target_price", "invalidation_price", "opened_at", "status",
    "exit_price", "closed_at", "pnl_usd", "pnl_pct", "close_reason",
    "high_water_price", "tp_stage_hit", "initial_qty", "realized_pnl_partial",
    "category", "entry_security_json", "chain", "thesis", "close_notes",
    "entry_atr_pct", "pending_high_water", "pending_high_water_since",
    "strategy", "entry_liquidity_usd", "breakeven_locked", "entry_regime",
    "breakeven_pending_since", "entry_dev_sold_pct", "last_liquidity_usd", "pocket",
    "rr", "align_score", "conviction_tier", "rvol_multiple", "discovery_channel",
    "conviction_process_trail", "conviction_website_corroborated", "conviction_posting_cadence",
    "liquidity_rotation_score", "liquidity_rotation_accelerating", "liquidity_rotation_volume_ratio",
    "mode", "gp_low", "gp_high", "wallet",
)

_ADDED_COLUMNS = [
    ("high_water_price", "REAL"),
    ("tp_stage_hit", "INTEGER NOT NULL DEFAULT 0"),
    ("initial_qty", "REAL"),
    ("realized_pnl_partial", "REAL NOT NULL DEFAULT 0"),
    # #187 -- continuous monitoring + concentration cap (see paper_trader_risk.py)
    ("category", "TEXT NOT NULL DEFAULT ''"),
    ("entry_security_json", "TEXT"),
    # #194 -- multi-chain momentum pivot, each position remembers its chain
    # (Base historically implicit -- default 'base' for already-open positions)
    ("chain", "TEXT NOT NULL DEFAULT 'base'"),
    # #197 (07/15) -- VCResult.these (full VC analysis, already computed by
    # analyze_vc_with_context) persisted at opening -- before this work, never
    # forwarded or saved: only the numeric levels (price/target/invalidation)
    # survived. Explicit operator goal: the cloud session must be able to check
    # afterward, in the DB, WHY ARIA entered -- not just at what price.
    ("thesis", "TEXT"),
    # 07/17 -- explicit operator request: every SALE (not just the buy) must be
    # justified with concrete numbers, to maximize usable data for calibration
    # purposes -- not just a short tag ("stop suiveur"/"invalidation") already
    # used by existing code/tests (untouched here), a separate text explaining
    # WHY with the real levels. Populated on every full close AND every partial
    # profit-take (in this latter case, on the still-open row -- latest note,
    # not a cumulative history).
    ("close_notes", "TEXT"),
    # 07/19 -- ATR (Average True Range) as % of entry price, computed ONCE at
    # opening by momentum_entry.evaluate_momentum_entry (same candles as the
    # entry decision -- never recomputed during the holding period). ``NULL``
    # for any position opened before this work, or by an analyzer that doesn't
    # provide it (e.g. the old VC-thesis pilot) -- the trailing stop then falls
    # back to TRAIL_STOP_PCT (fixed percentage), never an invented value.
    ("entry_atr_pct", "REAL"),
    # 07/20 -- time confirmation of the high water mark (replaces the
    # HIGH_WATER_JUMP_CAP_MULTIPLE speed clamp, see _advance_high_water): a new
    # candidate high, not yet confirmed (the price must stay above the last
    # CONFIRMED high for HIGH_WATER_CONFIRMATION_SECONDS before ratcheting).
    # NULL = no candidacy in progress (default behavior, never an invented
    # value).
    ("pending_high_water", "REAL"),
    ("pending_high_water_since", "TEXT"),
    # 07/20 -- Formula B (VC exit discipline, see VC_MIN_LIQUIDITY_FLOOR_USD/
    # VC_LIQUIDITY_DROP_INVALIDATION_PCT/VC_TAKE_SEED_MULTIPLE above). "momentum"
    # by default -- unchanged behavior (ATR trailing stop + staged TP) for ANY
    # already-open position or any new position whose analyzer doesn't
    # explicitly provide this field. entry_liquidity_usd: pool liquidity at
    # entry, reuses pool_liquidity_usd already passed for sizing (no new
    # network call) -- reference for detecting a structural drop during the
    # holding period.
    ("strategy", "TEXT NOT NULL DEFAULT 'momentum'"),
    ("entry_liquidity_usd", "REAL"),
    # 07/20 -- Breakeven Hard Floor (see _breakeven_floor_threshold above). 0/1
    # -- once set to 1, NEVER goes back down (irrevocable lock, verified by
    # test). 0 by default, never an invented value for a position opened before
    # this work (unchanged behavior: breakeven doesn't lock as long as the
    # price hasn't actually touched the flash threshold AFTER this fix was
    # activated).
    ("breakeven_locked", "INTEGER NOT NULL DEFAULT 0"),
    # 07/20 -- dynamic Regime Switch (see market_sentiment.resolve_meta_regime).
    # Macro meta-regime AT THE TIME OF OPENING -- ``NULL`` for any position
    # opened before this work or any analyzer that doesn't provide it (e.g. the
    # old VC-thesis pilot) -- treated as "neutral" by the management ratchet,
    # never an invented regime.
    ("entry_regime", "TEXT"),
    # 07/20 -- external cross-review: breakeven locking reacted to a SINGLE
    # instantaneous price reading, without the time confirmation the
    # high_water ratchet already applies (asymmetry flagged -- an aberrant tick
    # on a thin pool could wrongly lock breakeven). Same pattern as
    # pending_high_water_since: NULL = no candidacy in progress, set on the
    # first reading that crosses the flash threshold, cleared if the price
    # falls back below that threshold before confirmation
    # (HIGH_WATER_CONFIRMATION_SECONDS, reused as-is -- same philosophy "a real
    # move lasts, a wick doesn't," no 2nd magic constant).
    ("breakeven_pending_since", "TEXT"),
    # 07/22 -- task #4, VC post-entry monitoring (Formula B). Snapshot of the
    # deployer wallet at opening (share of its allocation already resold, see
    # ctx.dev_sold_pct) -- NULL if not resolved at entry, the in-holding check
    # is then fail-open (never a delta computed on missing baseline data).
    ("entry_dev_sold_pct", "REAL"),
    # Last OBSERVED liquidity (updated on EVERY cycle, unlike
    # entry_liquidity_usd which stays fixed at entry) -- detects a SUDDEN drop
    # between two cycles (30%), in addition to the cumulative drop since entry
    # (50%, VC_LIQUIDITY_DROP_INVALIDATION_PCT) already covered. NULL as long
    # as no management cycle has yet run on this position -- initialized to
    # entry_liquidity_usd at opening, never an invented value.
    ("last_liquidity_usd", "REAL"),
    # 07/22 -- Task 2, satellite pocket (see SATELLITE_POCKET_MIN_RR above).
    # 'main' by default (unchanged behavior: force-closed at every weekly
    # reset) -- 'satellite' once promoted by run_weekly_reset, never
    # automatically demoted (leaves the satellite pocket only via its OWN
    # normal close -- trailing stop, TP, or invalidation -- never via a reset).
    ("pocket", "TEXT NOT NULL DEFAULT 'main'"),
    # 07/23 -- performance-breakdown tracking (operator request: segment
    # winrate/PnL by decision factor to find what actually works). All NULL
    # for any position opened before this work or by an analyzer that doesn't
    # provide them -- never an invented value, the breakdown tool skips a
    # trade for any dimension where its own field is missing.
    #
    # rr/align_score: already computed by entry_signals.detect_entry /
    # momentum_entry._technical_alignment and already present in `sig`, simply
    # not persisted until now.
    ("rr", "REAL"),
    ("align_score", "INTEGER"),
    # conviction_tier: derived label ("strong"/"moderate"/"weak") from the same
    # rr/align_score thresholds already used by risk_guard.conviction_size_multiplier
    # -- computed once at opening, never recomputed from a stale position later.
    ("conviction_tier", "TEXT"),
    # rvol_multiple: the real relative-volume multiple from
    # momentum_entry._check_volume_confirmation, previously only formatted
    # into a human-readable reason string, never returned as a number.
    ("rvol_multiple", "REAL"),
    # discovery_channel: "websocket" (momentum_websocket.py, ~30s reaction) vs
    # "scan" (heartbeat momentum_discovery_cycle, periodic REST discovery) --
    # neither analyzer knows this on its own, the caller must pass it in.
    ("discovery_channel", "TEXT"),
    # conviction_process_trail/website_corroborated/posting_cadence: detail
    # from conviction_research.ConvictionResearch, previously only folded into
    # the free-text `thesis`/`reasons`, never exposed as structured fields.
    # process_trail stored as a single newline-joined string (a full list
    # column would need a separate table for no real benefit here).
    ("conviction_process_trail", "TEXT"),
    ("conviction_website_corroborated", "INTEGER"),
    ("conviction_posting_cadence", "TEXT"),
    # 07/23 -- liquidity-rotation signal (operator request: on a low-info token
    # there are no fundamentals to judge, but the buy/sell flow is fully
    # on-chain -- sense whether capital is rotating in right now). Purely
    # observational, never used here to size or gate a position -- tracked so
    # performance_breakdown.py can measure a real correlation to winrate/PnL
    # before it's ever wired into the decision.
    ("liquidity_rotation_score", "REAL"),
    ("liquidity_rotation_accelerating", "INTEGER"),
    ("liquidity_rotation_volume_ratio", "REAL"),
    # Item #101 (26/07): entry mode this position was sourced under
    # ("standard"/"scalping") -- see open_position's docstring. 'standard' by
    # default (unchanged behavior for any analyzer that doesn't provide it,
    # and for any position opened before this work).
    ("mode", "TEXT NOT NULL DEFAULT 'standard'"),
    # Item #101 (26/07), operator request ("aria doit pouvoir connaitre en
    # temps reel toute les valeurs de son golden pocket d'entree et de
    # sortie"): the golden pocket's own bounds (0.618/0.786 retracement),
    # previously computed but never persisted -- only the derived
    # invalidation/target were. NULL for any analyzer that doesn't provide
    # them (e.g. the old VC-thesis pilot), never an invented value.
    ("gp_low", "REAL"),
    ("gp_high", "REAL"),
    # 27/07 -- 3-pocket architecture plan (scalping/swing/VC, each an
    # independent $1M paper wallet): which pocket this position belongs to.
    # Default 'swing' for every position opened before this work -- the
    # existing $1M portfolio's full history becomes the swing pocket
    # (migration decision, see paper_state's own migration comment below).
    ("wallet", "TEXT NOT NULL DEFAULT 'swing'"),
]

# 07/19 -- DEDICATED hot migration for paper_position_archive (see _ensure_tables)
# -- this table was created complete from the start (no column ever added
# after the fact before this day), must now stay in EXACT parity with
# _POS_FIELDS/_ADDED_COLUMNS above on any already-existing database.
_ARCHIVE_ADDED_COLUMNS = [
    ("entry_atr_pct", "REAL"),
    ("pending_high_water", "REAL"),
    ("pending_high_water_since", "TEXT"),
    ("strategy", "TEXT NOT NULL DEFAULT 'momentum'"),
    ("entry_liquidity_usd", "REAL"),
    ("breakeven_locked", "INTEGER NOT NULL DEFAULT 0"),
    ("entry_regime", "TEXT"),
    ("breakeven_pending_since", "TEXT"),
    ("entry_dev_sold_pct", "REAL"),
    ("last_liquidity_usd", "REAL"),
    ("pocket", "TEXT NOT NULL DEFAULT 'main'"),
    # 07/23 -- same performance-breakdown tracking fields as _ADDED_COLUMNS
    # above, kept in parity so archived (post-weekly-reset) positions carry
    # the same data as still-open ones.
    ("rr", "REAL"),
    ("align_score", "INTEGER"),
    ("conviction_tier", "TEXT"),
    ("rvol_multiple", "REAL"),
    ("discovery_channel", "TEXT"),
    ("conviction_process_trail", "TEXT"),
    ("conviction_website_corroborated", "INTEGER"),
    ("conviction_posting_cadence", "TEXT"),
    ("liquidity_rotation_score", "REAL"),
    ("liquidity_rotation_accelerating", "INTEGER"),
    ("liquidity_rotation_volume_ratio", "REAL"),
    # Item #101 (26/07): kept in parity with _ADDED_COLUMNS above.
    ("mode", "TEXT NOT NULL DEFAULT 'standard'"),
    ("gp_low", "REAL"),
    ("gp_high", "REAL"),
    # 27/07 -- kept in parity with _ADDED_COLUMNS above.
    ("wallet", "TEXT NOT NULL DEFAULT 'swing'"),
]

# Hot migration of `paper_state` (#186, 07/15) -- same idempotent pattern as
# `_ADDED_COLUMNS` above. Highest equity ever reached, used by risk_guard.py
# for the drawdown circuit breaker (never NULL after the first call to
# `get_equity_high_water_mark` -- initialized to the starting capital).
_STATE_ADDED_COLUMNS = [
    ("equity_high_water_mark", "REAL"),
    # 07/17 -- timestamp of the last periodic tracking alert sent (see
    # TRACKING_ALERT_MIN_INTERVAL_MINUTES) -- NULL as long as none has been sent yet.
    ("last_tracking_alert_at", "TEXT"),
    # 07/18 -- explicit operator decision: replaces the 30d/7d/14d protocol with a
    # weekly TRAINING loop (see WEEKLY_CYCLE_DAYS/run_weekly_reset below).
    # Current cycle number, incremented on every reset -- never NULL after the
    # first call to _ensure_tables (starts at 1, same default value as the SQL column).
    ("cycle_number", "INTEGER NOT NULL DEFAULT 1"),
    # 25/07 -- operator-requested one-off: a shortened training cycle (24h
    # instead of the usual 7 days) for THIS cycle only. NULL (default) means
    # "use WEEKLY_CYCLE_DAYS as normal" -- never a permanent change to the
    # weekly protocol's doctrine. run_weekly_reset() clears this back to NULL
    # once the cycle closes, so the NEXT cycle reverts to 7 days unless the
    # operator explicitly asks for another short one via reset_portfolio().
    ("cycle_duration_days", "REAL"),
    # Item #101 (26/07) -- which entry mode `run_paper_cycle` sources candidates
    # with: "standard" (swing/momentum, unchanged default) or "scalping" (once
    # fully wired -- operator's explicit decision: the Milly test switches to
    # 100% scalping, REPLACING swing/momentum and the VC pocket, never a mix of
    # both at once). A portfolio-wide setting (not per-position) because the
    # operator's decision is a full switch, not a blend -- see
    # get_trading_mode/set_trading_mode below.
    ("trading_mode", "TEXT NOT NULL DEFAULT 'standard'"),
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hours_since(opened_at: str | None) -> float | None:
    """Holding duration in hours since ``opened_at`` (ISO), for exit notes
    (07/17) -- ``None`` if missing/invalid, never an invented value."""
    if not opened_at:
        return None
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(opened_at)).total_seconds() / 3600.0
    except ValueError:
        return None


def _duration_phrase(opened_at: str | None) -> str:
    hours = _hours_since(opened_at)
    if hours is None:
        return "durée de détention inconnue"
    return f"détenue {hours:.1f}h" if hours < 24 else f"détenue {hours / 24:.1f}j"


def _num(v) -> float | None:
    """Defensive parse of a possibly '$1,234.5'-formatted price -> float, or None."""
    try:
        if v is None:
            return None
        s = str(v).replace("$", "").replace(",", "").strip().split()[0]
        return float(s)
    except (ValueError, IndexError, TypeError):
        return None


def _row_to_pos(row: tuple) -> dict:
    return dict(zip(_POS_FIELDS, row))


async def _ensure_tables() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS paper_position (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                contract TEXT NOT NULL,
                symbol TEXT,
                cost_usd REAL NOT NULL,
                entry_price REAL NOT NULL,
                qty REAL NOT NULL,
                target_price REAL,
                invalidation_price REAL,
                opened_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                exit_price REAL,
                closed_at TEXT,
                pnl_usd REAL,
                pnl_pct REAL,
                close_reason TEXT,
                high_water_price REAL,
                tp_stage_hit INTEGER NOT NULL DEFAULT 0,
                initial_qty REAL,
                realized_pnl_partial REAL NOT NULL DEFAULT 0,
                category TEXT NOT NULL DEFAULT '',
                entry_security_json TEXT,
                chain TEXT NOT NULL DEFAULT 'base',
                thesis TEXT,
                close_notes TEXT
            )
            """
        )
        # Hot migration: adds the position-management columns to existing DBs
        # (SQLite doesn't create them if the table pre-exists). Idempotent, non-destructive.
        existing = {
            row[1]
            for row in await (await db.execute("PRAGMA table_info(paper_position)")).fetchall()
        }
        for name, ddl in _ADDED_COLUMNS:
            if name not in existing:
                await db.execute(f"ALTER TABLE paper_position ADD COLUMN {name} {ddl}")

        # 27/07 -- paper_state migration: from a single-row schema
        # (id INTEGER PRIMARY KEY CHECK(id=1)) to one row PER WALLET (wallet
        # TEXT PRIMARY KEY) -- Phase 1 of the 3-pocket architecture plan
        # (scalping/swing/VC, each an independent $1M paper wallet, see
        # docs/HANDOFF_PAPER_TRADING.md). SQLite can't ALTER a PRIMARY KEY in
        # place, so a DB still on the old schema (has an `id` column, no
        # `wallet` column) is renamed aside, the new schema is created fresh,
        # and the single legacy row is copied forward under wallet='swing'
        # (migration decision: the existing $1M portfolio's full history/
        # identity becomes the swing pocket) before the legacy table is
        # dropped. Idempotent -- a DB already on the new schema has no `id`
        # column left to detect, so the migration branch below is skipped.
        legacy_state_cols = {
            row[1]
            for row in await (await db.execute("PRAGMA table_info(paper_state)")).fetchall()
        }
        legacy_row: dict | None = None
        if legacy_state_cols and "wallet" not in legacy_state_cols:
            legacy_col_order = [
                row[1]
                for row in await (await db.execute("PRAGMA table_info(paper_state)")).fetchall()
            ]
            raw = await (
                await db.execute(
                    f"SELECT {', '.join(legacy_col_order)} FROM paper_state WHERE id = 1"
                )
            ).fetchone()
            if raw is not None:
                legacy_row = dict(zip(legacy_col_order, raw))
            await db.execute("ALTER TABLE paper_state RENAME TO paper_state_legacy_migrated")

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS paper_state (
                wallet TEXT PRIMARY KEY,
                starting_capital REAL NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        state_existing = {
            row[1]
            for row in await (await db.execute("PRAGMA table_info(paper_state)")).fetchall()
        }
        for name, ddl in _STATE_ADDED_COLUMNS:
            if name not in state_existing:
                await db.execute(f"ALTER TABLE paper_state ADD COLUMN {name} {ddl}")

        if legacy_row is not None:
            # Copy every column the legacy row actually had (starting_capital/
            # created_at plus whichever hot-migrated columns already existed
            # on THIS database -- equity_high_water_mark/cycle_number/
            # trading_mode/etc.), never just starting_capital/created_at alone
            # -- otherwise a live prod portfolio's real risk/cycle state would
            # be silently reset by this migration.
            cols_to_copy = [c for c in legacy_row if c != "id"]
            await db.execute(
                f"INSERT OR IGNORE INTO paper_state (wallet, {', '.join(cols_to_copy)}) "
                f"VALUES ('swing', {', '.join('?' for _ in cols_to_copy)})",
                tuple(legacy_row[c] for c in cols_to_copy),
            )
            await db.execute("DROP TABLE paper_state_legacy_migrated")

        # Seed the 2 new pockets fresh at STARTING_CAPITAL_USD (migration
        # decision: scalping/VC start empty -- only 'swing' carries the
        # existing portfolio's history forward). INSERT OR IGNORE also covers
        # the 'swing' row itself on a DB that was already on the new schema
        # (no legacy row to copy -- e.g. a fresh test DB).
        for wallet_name in ("swing", "scalping", "vc"):
            await db.execute(
                "INSERT OR IGNORE INTO paper_state (wallet, starting_capital, created_at) "
                "VALUES (?, ?, ?)",
                (wallet_name, STARTING_CAPITAL_USD, _now()),
            )
        # 07/18 -- weekly verdict (one row per cycle closed by run_weekly_reset).
        # Never a destructive DELETE/UPDATE anywhere other than the reset's own
        # upsert -- this is the real track record of the weekly protocol, must
        # survive indefinitely.
        #
        # 27/07 -- 3-pocket architecture plan, Phase 4: cycle_number was a
        # GLOBAL primary key (documented limitation in run_weekly_reset's
        # docstring) -- a 2nd pocket (scalping) now also calling this reset
        # would collide on the same cycle_number as swing's. Same rename-aside/
        # recreate/copy-forward/drop pattern as paper_state's own migration
        # above -- idempotent (a DB already on the new schema has no `wallet`
        # column left to detect, so this branch is skipped).
        legacy_weekly_cols = {
            row[1]
            for row in await (await db.execute("PRAGMA table_info(paper_weekly_cycle)")).fetchall()
        }
        legacy_weekly_rows: list[dict] = []
        if legacy_weekly_cols and "wallet" not in legacy_weekly_cols:
            legacy_weekly_col_order = [
                row[1]
                for row in await (await db.execute("PRAGMA table_info(paper_weekly_cycle)")).fetchall()
            ]
            raw_rows = await (
                await db.execute(f"SELECT {', '.join(legacy_weekly_col_order)} FROM paper_weekly_cycle")
            ).fetchall()
            legacy_weekly_rows = [dict(zip(legacy_weekly_col_order, raw)) for raw in raw_rows]
            await db.execute("ALTER TABLE paper_weekly_cycle RENAME TO paper_weekly_cycle_legacy_migrated")

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS paper_weekly_cycle (
                wallet TEXT NOT NULL DEFAULT 'swing',
                cycle_number INTEGER NOT NULL,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                target_equity REAL NOT NULL,
                start_capital REAL NOT NULL,
                end_equity REAL,
                return_pct REAL,
                validated INTEGER,
                closed_trades INTEGER,
                win_rate REAL,
                PRIMARY KEY (wallet, cycle_number)
            )
            """
        )
        if legacy_weekly_rows:
            # Every existing row is swing's history (only swing ever called
            # run_weekly_reset before this work) -- never re-attributed to
            # another pocket.
            for row in legacy_weekly_rows:
                cols_to_copy = list(row.keys())
                await db.execute(
                    f"INSERT OR IGNORE INTO paper_weekly_cycle (wallet, {', '.join(cols_to_copy)}) "
                    f"VALUES ('swing', {', '.join('?' for _ in cols_to_copy)})",
                    tuple(row[c] for c in cols_to_copy),
                )
            await db.execute("DROP TABLE paper_weekly_cycle_legacy_migrated")
        # 07/18 -- COMPLETE history never destroyed: unlike reset_portfolio()
        # (DROP TABLE, destructive by design), run_weekly_reset() archives EACH
        # position of the week HERE (including opened-then-force-closed) before
        # clearing the live table -- the weekly track record stays queryable
        # forever. Types copied one-to-one from paper_position (never generated
        # dynamically -- SQLite's TEXT affinity would silently convert a number
        # to a string if the mapping were wrong), columns in the same order as
        # _POS_FIELDS so that run_weekly_reset's INSERT... SELECT stays a simple
        # positional alignment.
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS paper_position_archive (
                archive_id INTEGER PRIMARY KEY AUTOINCREMENT,
                cycle_number INTEGER NOT NULL,
                id INTEGER,
                contract TEXT,
                symbol TEXT,
                cost_usd REAL,
                entry_price REAL,
                qty REAL,
                target_price REAL,
                invalidation_price REAL,
                opened_at TEXT,
                status TEXT,
                exit_price REAL,
                closed_at TEXT,
                pnl_usd REAL,
                pnl_pct REAL,
                close_reason TEXT,
                high_water_price REAL,
                tp_stage_hit INTEGER,
                initial_qty REAL,
                realized_pnl_partial REAL,
                category TEXT,
                entry_security_json TEXT,
                chain TEXT,
                thesis TEXT,
                close_notes TEXT,
                entry_atr_pct REAL
            )
            """
        )
        # 07/19 -- same hot-migration pattern as paper_position/paper_state above:
        # this table was created COMPLETE the first time (no columns added
        # incrementally before this day), so never needed an additive column
        # list -- but _POS_FIELDS (shared with paper_position for
        # run_weekly_reset's positional INSERT...SELECT) just gained
        # entry_atr_pct, and this table must stay in EXACT parity with
        # _POS_FIELDS on any already-existing database (the CREATE TABLE IF NOT
        # EXISTS above never touches an already-created table -- real bug found
        # while running the full suite: sqlite3.OperationalError on
        # run_weekly_reset() as soon as the archive table pre-existed without
        # this column).
        archive_existing = {
            row[1]
            for row in await (await db.execute("PRAGMA table_info(paper_position_archive)")).fetchall()
        }
        for name, ddl in _ARCHIVE_ADDED_COLUMNS:
            if name not in archive_existing:
                await db.execute(f"ALTER TABLE paper_position_archive ADD COLUMN {name} {ddl}")
        await db.commit()


async def starting_capital(wallet: str = "swing") -> float:
    """``wallet`` (27/07, 3-pocket architecture plan): which pocket's $1M
    portfolio to read -- 'swing' (default, the existing/only actively-traded
    portfolio as of this work), 'scalping', or 'vc'. Defaulted rather than
    made mandatory since only 'swing' has any real trading loop wired to it
    today (Phase 2 of the plan wires the other two) -- every current caller
    keeps working unchanged."""
    await _ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT starting_capital FROM paper_state WHERE wallet = ?", (wallet,),
        ) as cur:
            row = await cur.fetchone()
    return float(row[0]) if row else STARTING_CAPITAL_USD


async def reset_portfolio(
    starting: float = STARTING_CAPITAL_USD, *, created_at: str | None = None,
    cycle_duration_days: float | None = None, wallet: str = "swing",
) -> None:
    """Starts fresh (new proof run). DESTRUCTIVE: to be triggered explicitly by
    the operator, never by an automatic loop.

    24/07 -- 5-agent audit finding: this used to DROP ``paper_position``
    without ever archiving it first, unlike ``run_weekly_reset`` (which always
    archives before clearing) -- a manual reset triggered mid-cycle (e.g. after
    a security incident forcing an out-of-band restart, as happened 22/07)
    silently lost every already-closed position's history, with no trace left
    in ``paper_position_archive``. Now archives whatever is still in the live
    table (open AND closed rows) under the CURRENT ``cycle_number`` before
    dropping -- same non-destructive doctrine as the weekly cycle, never a
    silent loss of track record.

    ``cycle_duration_days`` (25/07, operator request: "passe le test de 7 jours
    a 24h"): one-off override of WEEKLY_CYCLE_DAYS for THIS cycle only -- see
    _STATE_ADDED_COLUMNS for why this is never a permanent doctrine change.

    ``wallet`` (27/07, 3-pocket architecture plan): scopes the reset to a
    SINGLE pocket -- ``paper_position``/``paper_state`` are shared tables
    across all 3 pockets, so a reset must never touch rows belonging to a
    DIFFERENT wallet than the one requested."""
    await _ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (
            await db.execute("SELECT cycle_number FROM paper_state WHERE wallet = ?", (wallet,))
        ).fetchone()
        cycle_number = row[0] if row else 0
        cols = ", ".join(_POS_FIELDS)
        await db.execute(
            f"INSERT INTO paper_position_archive (cycle_number, {cols}) "
            f"SELECT ?, {cols} FROM paper_position WHERE wallet = ?",
            (cycle_number, wallet),
        )
        await db.execute("DELETE FROM paper_position WHERE wallet = ?", (wallet,))
        await db.commit()
    await _ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        # Full reset back to a fresh pocket -- matches the pre-27/07 behavior
        # (DROP + recreate the whole table), now done as a scoped UPDATE since
        # paper_state is shared across all 3 wallets and a DROP would wipe the
        # other 2 pockets' state too. Every field a fresh row would otherwise
        # get from its column DEFAULT is reset explicitly here (cycle_number
        # back to 1, last_tracking_alert_at/trading_mode back to their
        # defaults) -- not just the 4 fields the old code set post-recreate.
        await db.execute(
            "UPDATE paper_state SET starting_capital = ?, created_at = ?, "
            "equity_high_water_mark = ?, cycle_duration_days = ?, cycle_number = 1, "
            "last_tracking_alert_at = NULL, trading_mode = 'standard' WHERE wallet = ?",
            (starting, created_at or _now(), starting, cycle_duration_days, wallet),
        )
        await db.commit()


async def get_equity_high_water_mark(wallet: str = "swing") -> float:
    """Highest equity ever reached (#186, drawdown circuit breaker). Initialized
    to the starting capital as long as no higher equity has been observed yet
    -- never NULL after this call (migrated DBs have the column but not the
    value). ``wallet`` (27/07): see ``starting_capital``'s docstring."""
    await _ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT equity_high_water_mark FROM paper_state WHERE wallet = ?", (wallet,),
        ) as cur:
            row = await cur.fetchone()
    if row and row[0] is not None:
        return float(row[0])
    return await starting_capital(wallet=wallet)


async def set_equity_high_water_mark(value: float, *, wallet: str = "swing") -> None:
    await _ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE paper_state SET equity_high_water_mark = ? WHERE wallet = ?", (value, wallet),
        )
        await db.commit()


async def get_last_tracking_alert_at(wallet: str = "swing") -> str | None:
    await _ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT last_tracking_alert_at FROM paper_state WHERE wallet = ?", (wallet,),
        ) as cur:
            row = await cur.fetchone()
    return row[0] if row else None


async def set_last_tracking_alert_at(value: str, *, wallet: str = "swing") -> None:
    await _ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE paper_state SET last_tracking_alert_at = ? WHERE wallet = ?", (value, wallet),
        )
        await db.commit()


async def get_trading_mode(wallet: str = "swing") -> str:
    """Portfolio-wide entry mode ("standard"/"scalping", Item #101, 26/07) --
    "standard" (unchanged behavior) until the operator explicitly switches the
    Milly test to scalping via ``set_trading_mode``. A single switch, never a
    per-position blend (operator's explicit decision: scalping REPLACES
    swing/momentum and the VC pocket on this portfolio, not a mix).

    ``wallet`` (27/07): kept on the 'swing' pocket for now -- this switch
    predates the 3-pocket split and still governs the one and only actively-
    traded portfolio (Phase 2 of the plan will retire it once the scalping
    pocket becomes its own independent wallet with its own sourcing loop)."""
    await _ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT trading_mode FROM paper_state WHERE wallet = ?", (wallet,),
        ) as cur:
            row = await cur.fetchone()
    return row[0] if row and row[0] else "standard"


async def set_trading_mode(mode: str, *, wallet: str = "swing") -> None:
    """Operator-only switch (Item #101) -- no automatic promotion from within
    the codebase; the code observes the naturally-occurring trade volume, it
    never imposes or targets one."""
    if mode not in ("standard", "scalping"):
        raise ValueError(f"trading_mode must be 'standard' or 'scalping', got {mode!r}")
    await _ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE paper_state SET trading_mode = ? WHERE wallet = ?", (mode, wallet),
        )
        await db.commit()


async def get_open_positions(wallet: str | None = None) -> list[dict]:
    """``wallet`` (27/07, 3-pocket architecture plan): optional filter, ``None``
    (default) returns open positions across ALL pockets -- unchanged behavior
    for every existing caller, since only the 'swing' pocket has ever traded
    as of this work (Phase 2 wires the other two). Pass it explicitly once a
    caller genuinely means ONE specific pocket (e.g. counting a wallet's own
    MAX_POSITIONS)."""
    await _ensure_tables()
    cols = ", ".join(_POS_FIELDS)
    query = f"SELECT {cols} FROM paper_position WHERE status = 'open'"
    params: list = []
    if wallet is not None:
        query += " AND wallet = ?"
        params.append(wallet)
    query += " ORDER BY id"
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(query, params) as cur:
            rows = await cur.fetchall()
    return [_row_to_pos(r) for r in rows]


async def get_closed_positions(limit: int = 500, *, wallet: str | None = None) -> list[dict]:
    """``wallet``: see ``get_open_positions``'s docstring -- same optional filter,
    same default (``None`` = all pockets, unchanged behavior)."""
    await _ensure_tables()
    cols = ", ".join(_POS_FIELDS)
    query = f"SELECT {cols} FROM paper_position WHERE status = 'closed'"
    params: list = []
    if wallet is not None:
        query += " AND wallet = ?"
        params.append(wallet)
    # `id DESC` as tie-break (#186): `closed_at` (microsecond resolution) can
    # coincide between two closes that happen close together in the same
    # tick/test -- insertion order remains the reliable recency signal in that
    # case, notably for risk_guard.evaluate_portfolio_risk's consecutive-loss
    # counting.
    query += " ORDER BY closed_at DESC, id DESC LIMIT ?"
    params.append(limit)
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(query, params) as cur:
            rows = await cur.fetchall()
    return [_row_to_pos(r) for r in rows]


async def get_archived_closed_positions(limit: int = 5000, *, wallet: str | None = None) -> list[dict]:
    """Every closed position already archived by a past ``run_weekly_reset``
    (07/23, performance-breakdown tracking: the full track record spans many
    weekly cycles, not just the one in progress -- ``get_closed_positions``
    above only covers the current cycle). Same ``_POS_FIELDS`` shape as an
    open/closed position (``archive_id``/``cycle_number`` deliberately
    excluded -- not needed by any caller so far, easy to add later without
    breaking this shape). ``wallet``: see ``get_open_positions``'s docstring."""
    await _ensure_tables()
    cols = ", ".join(_POS_FIELDS)
    query = f"SELECT {cols} FROM paper_position_archive WHERE status = 'closed'"
    params: list = []
    if wallet is not None:
        query += " AND wallet = ?"
        params.append(wallet)
    query += " ORDER BY closed_at DESC, archive_id DESC LIMIT ?"
    params.append(limit)
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(query, params) as cur:
            rows = await cur.fetchall()
    return [_row_to_pos(r) for r in rows]


async def list_positions_for_contract(contract: str, limit: int = 100) -> list[dict]:
    """All paper positions (open + closed) for a contract, most recent first.

    Feeds the "per-token dossier." The contract key is stored LOWERCASE for
    Base/Robinhood but in its ORIGINAL CASE for Solana (07/18, real bug: a
    uniform ``.lower()`` corrupted every base58 address before it reached
    GoPlus/RugCheck -- see ``momentum_entry.normalize_contract_case``/
    ``open_position`` below). This function doesn't know the caller's chain --
    so it searches case-insensitively (``LOWER(contract) = ?``) rather than
    assuming a normalization it can't reproduce itself.
    """
    await _ensure_tables()
    contract = (contract or "").lower()
    cols = ", ".join(_POS_FIELDS)
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            f"SELECT {cols} FROM paper_position WHERE LOWER(contract) = ? ORDER BY id DESC LIMIT ?",
            (contract, limit),
        ) as cur:
            rows = await cur.fetchall()
    return [_row_to_pos(r) for r in rows]


async def _get_open(
    contract: str, *, strategy: str | None = None, position_id: int | None = None,
    wallet: str | None = None,
) -> dict | None:
    """Case-insensitive search -- same reason as ``list_positions_for_contract``
    above (no ``chain`` parameter here to reconstruct the real normalization).

    ``strategy`` (07/22, task #4, optional): ``None`` (default) preserves
    EXACTLY the historical behavior (any open position on this contract,
    regardless of its strategy) -- all existing callers stay unchanged. When
    provided, filters on THIS specific strategy -- needed to allow the VC+Swing
    combination (explicit operator decision, 07/22): an already-open
    ``vc_thesis`` position must never block the opening of a ``momentum``
    position on the SAME contract, and vice versa.

    ``wallet`` (27/07, 3-pocket architecture plan, optional): same pattern as
    ``strategy`` -- ``None`` (default) preserves historical behavior (any
    pocket), provided when a caller means ONE specific pocket. This is what
    lets 3 pockets legally hold the SAME contract simultaneously: pocket X
    checking ``has_open(contract, wallet="swing")`` must never be blocked by
    pocket Y already holding it under ``wallet="scalping"``.

    ``position_id`` (27/07, multi-pocket prerequisite): when provided, resolves
    DIRECTLY by row id -- ``contract``/``strategy``/``wallet`` are ignored
    entirely for the lookup (still validated as a sanity check below). This is
    the safe path once the SAME contract can legally have multiple
    simultaneously-open positions (one per pocket/wallet) -- a lookup by
    contract alone cannot disambiguate which one the caller means. When
    ``position_id`` is absent (legacy path, still used by external callers with
    no ambiguity today), a SECOND open position sharing this contract (AND the
    same strategy/wallet filter, if any) now raises loudly instead of silently
    resolving to an arbitrary one -- real bug class this closes: a caller that
    forgot to pass ``position_id`` would otherwise corrupt whichever position
    ``fetchone()`` happened to return first."""
    cols = ", ".join(_POS_FIELDS)
    if position_id is not None:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                f"SELECT {cols} FROM paper_position WHERE id = ? AND status = 'open'",
                (position_id,),
            ) as cur:
                row = await cur.fetchone()
        return _row_to_pos(row) if row else None

    contract = (contract or "").lower()
    query = f"SELECT {cols} FROM paper_position WHERE LOWER(contract) = ? AND status = 'open'"
    params: list = [contract]
    if strategy is not None:
        query += " AND strategy = ?"
        params.append(strategy)
    if wallet is not None:
        query += " AND wallet = ?"
        params.append(wallet)
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(query, params) as cur:
            rows = await cur.fetchall()
    if len(rows) > 1:
        raise RuntimeError(
            f"_get_open: {len(rows)} open positions share contract {contract!r} -- "
            "pass position_id explicitly to disambiguate (multi-pocket ambiguity)"
        )
    return _row_to_pos(rows[0]) if rows else None


async def has_open(contract: str, *, strategy: str | None = None, wallet: str | None = None) -> bool:
    return (await _get_open(contract, strategy=strategy, wallet=wallet)) is not None


async def _has_prior_close(contract: str) -> bool:
    """Has the contract already had AT LEAST one closed position (gain or loss,
    whatever the reason -- trailing stop, invalidation, profit stage, safety
    re-scan)? Reuses ``list_positions_for_contract`` (no duplicated query) --
    distinct from ``has_open`` which only looks at the present, never the history."""
    positions = await list_positions_for_contract(contract)
    return any(p["status"] == "closed" for p in positions)


# 07/20 -- external cross-review: the 07/19 relaxed re-entry (see comment on
# the old REENTRY_RR_MIN earlier in this file) has no guard against a contract
# looping loss->rebuy->loss on ITSELF -- exactly the BRIAN incident pattern
# (07/17, "rebought twice in a row after two trailing stops," -$18,561
# cumulative). Distinct from risk_guard.HARD_CONSECUTIVE_LOSSES's global
# circuit breaker (whole portfolio) -- this one is scoped to a SINGLE contract,
# surgical, never blocks another token.
MAX_CONSECUTIVE_LOSSES_PER_CONTRACT = 2

# Item #101 (26/07), operator request ("regle-le pour le scalping"): a looser
# threshold for scalping mode. Statistical reasoning, not an arbitrary bump --
# the workflow research (26/07) cites a 50-65% win rate as typical even for a
# WORKING scalping strategy; at a 50% win rate, back-to-back losses on an
# otherwise-viable contract happen by pure chance ~25% of the time. Keeping
# the swing threshold (2) at this trade frequency would suspend re-entry on
# many contracts that are simply unlucky, not genuinely broken -- fighting the
# operator's explicit goal of observing ARIA's naturally-occurring behavior
# rather than artificially constraining it (see MAX_POSITIONS bypass above,
# same doctrine). 3 still catches a real repeated-failure pattern (a 3-loss
# streak at 50% win rate is only ~12.5% likely by chance) without over-
# suspending on normal scalping noise.
SCALPING_MAX_CONSECUTIVE_LOSSES_PER_CONTRACT = 3


async def _consecutive_losses_for_contract(contract: str, *, limit: int = 20) -> int:
    """Consecutive losses (``pnl_usd < 0``) on THE SAME contract, most recent
    first -- same pattern as ``risk_guard.evaluate_portfolio_risk`` (whole
    portfolio), scoped to a single contract via ``list_positions_for_contract``
    (already case-insensitive, no duplicated query). Stops at the first gain
    encountered (a loss followed by a gain resets the counter to zero) --
    ``pnl_usd`` already includes partial profit-takes (see ``close_position``),
    never a separate metric to maintain."""
    positions = await list_positions_for_contract(contract, limit=limit)
    streak = 0
    for p in positions:
        if p["status"] != "closed":
            continue
        if (p.get("pnl_usd") or 0.0) < 0:
            streak += 1
        else:
            break
    return streak


# 07/24 -- direct operator observation on a real AERO position (sell then
# rebuy in quick succession): "vente puis achat suspect sauf si elle y croit
# deux fois plus". Only targets the IMMEDIATE sell-then-rebuy pattern -- a
# contract whose most recent closed exit was NOT specifically an
# "invalidation" (structural setup failure -- cf. close_reason values in the
# position-management block below) never triggers this guard, whatever
# happened further back in its history.
REENTRY_INVALIDATION_CONVICTION_MULTIPLIER = 2.0


async def _last_invalidation_exit_rr(contract: str, *, limit: int = 20) -> float | None:
    """RR at entry of the most recent CLOSED position on this contract, IF
    that most recent close was specifically an "invalidation" -- `None` if
    the most recent close was for a different reason (trailing stop, take
    profit...), if no closed position exists, or if its rr wasn't recorded
    (nothing to compare a fresh signal against)."""
    positions = await list_positions_for_contract(contract, limit=limit)
    for p in positions:
        if p["status"] != "closed":
            continue
        if p.get("close_reason") != "invalidation":
            return None
        rr = p.get("rr")
        return float(rr) if rr is not None else None
    return None


async def cash_available(wallet: str = "swing") -> float:
    """Cash = starting capital - cost of open positions + realized P&L of closed
    ones + realized P&L of PARTIAL profit-takes on still-open positions (the
    remaining ``cost_usd`` is already proportionally reduced by
    ``reduce_position``, so only the profit beyond the cost basis needs to be
    added back here).

    ``wallet`` (27/07, 3-pocket architecture plan): scopes every sum to ONE
    pocket's own positions -- defaulted to 'swing' (the only actively-traded
    pocket as of this work) rather than made mandatory, so every existing
    caller keeps working unchanged until Phase 2 wires the other two."""
    start = await starting_capital(wallet=wallet)
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COALESCE(SUM(cost_usd), 0), COALESCE(SUM(realized_pnl_partial), 0) "
            "FROM paper_position WHERE status = 'open' AND wallet = ?",
            (wallet,),
        ) as cur:
            open_cost, open_partial = await cur.fetchone()
        async with db.execute(
            "SELECT COALESCE(SUM(pnl_usd), 0) FROM paper_position WHERE status = 'closed' AND wallet = ?",
            (wallet,),
        ) as cur:
            realized = (await cur.fetchone())[0] or 0.0
    return float(start) - float(open_cost or 0.0) + float(realized) + float(open_partial or 0.0)


async def open_position(
    contract: str,
    symbol: str,
    entry_price: float,
    *,
    wallet: str,
    target_price: float | None = None,
    invalidation_price: float | None = None,
    alloc_usd: float | None = None,
    category: str = "",
    entry_security_json: str = "",
    chain: str = "base",
    thesis: str | None = None,
    pool_liquidity_usd: float | None = None,
    entry_atr_pct: float | None = None,
    strategy: str = "momentum",
    entry_regime: str | None = None,
    entry_dev_sold_pct: float | None = None,
    rr: float | None = None,
    align_score: int | None = None,
    conviction_tier: str | None = None,
    rvol_multiple: float | None = None,
    discovery_channel: str | None = None,
    conviction_process_trail: str | None = None,
    conviction_website_corroborated: bool | None = None,
    conviction_posting_cadence: str | None = None,
    liquidity_rotation_score: float | None = None,
    liquidity_rotation_accelerating: bool | None = None,
    liquidity_rotation_volume_ratio: float | None = None,
    mode: str = "standard",
    gp_low: float | None = None,
    gp_high: float | None = None,
) -> dict | None:
    """Opens a FICTITIOUS position at the real entry price. Refuses if already
    open, position cap reached, risk circuit breaker armed, invalid price,
    insufficient cash, or ``category`` concentration cap exceeded without
    enough room (#187, see paper_trader_risk.py -- the alloc is REDUCED to fit
    under the cap when the remaining room is significant, otherwise the
    position is skipped). ``chain`` (#194, multi-chain momentum pivot) persists
    the origin chain so later position management (price, re-scan) knows which
    chain to query. ``thesis`` (#197, 07/15): full VC reasoning
    (``VCResult.these``) persisted as-is -- why ARIA is entering, not just at
    what price. Persistence takes priority over Telegram display: saved HERE,
    regardless of whether any notifier/topic is configured. Returns the
    position or None.

    ``wallet`` (27/07, 3-pocket architecture plan): MANDATORY, no default --
    every caller must now say explicitly which pocket ("swing"/"scalping"/
    "vc") a new position belongs to. Deliberate: a silent default here would
    be a real bug class (same doctrine as ``position_id`` on
    ``close_position``/``reduce_position`` -- silent ambiguity once the SAME
    contract can legally be open in several pockets at once). Persisted as-is;
    its own ``has_open`` check below is scoped to THIS SAME pocket only (an
    open position in a DIFFERENT pocket on the same contract never blocks
    this one), and its own position-COUNT is likewise scoped to THIS pocket
    (``get_open_positions(wallet=wallet)``, never the whole portfolio's). The
    CAP VALUE this inner check compares against stays the legacy
    ``MAX_POSITIONS`` (30) regardless of ``wallet`` -- it is a defense-in-depth
    safety net for ANY caller (manual command, future real-capital pilot),
    not the real per-pocket caps (``MAX_POSITIONS_VC``/``_SWING``/
    ``_SCALPING``), which are enforced one level up, per cycle, by
    ``_open_new_entries_for_wallet``.

    ``mode`` (Item #101, 26/07): the entry mode this signal was sourced under
    ("standard"/"scalping") -- persisted as-is. In "scalping" mode, the
    position-count cap (``MAX_POSITIONS``) is not enforced here either
    (operator's explicit decision: "laisse libre, voyons comment ARIA trade
    sans la force" -- observe the naturally-occurring behavior, real cash
    availability remains the brake). Default ``"standard"``, unchanged
    behavior for any caller that doesn't provide it. Distinct from ``wallet``
    above: ``mode`` is the entry-signal flavor (used, among other things, to
    decide the swap-fee simulation below); ``wallet`` is the pocket the
    resulting position is booked under -- today's single-pocket gate-OFF path
    always books "scalping"-mode signals into the "swing" wallet (Phase 2 of
    the 3-pocket plan hasn't yet retired the old portfolio-wide ``trading_mode``
    switch, see ``get_trading_mode``/``set_trading_mode``).

    ``gp_low``/``gp_high`` (Item #101, 26/07): the golden pocket's own bounds
    (0.618/0.786 retracement) -- persisted so the position's real entry ZONE
    stays queryable in real time (relay conversation, thesis), not just the
    derived invalidation/target. ``None`` for any analyzer that doesn't
    provide them, never an invented value.

    Contract case (07/18, real bug): preserved for Solana (base58, case is part
    of the value), lowercased for Base/Robinhood (EVM hex, as before) --
    ``momentum_entry.normalize_contract_case``. Storing a corrupted Solana
    address would have silently made any later re-scan/price lookup
    (``paper_trader_risk.py``) inoperative on the real chain.

    ``pool_liquidity_usd`` (07/19, Gemini cross-review): REAL liquidity of the
    targeted pool -- used to reduce ``alloc`` if THIS order's price impact on
    THIS pool would drop the structural R/R below its floor
    (``risk_guard.cap_alloc_to_price_impact``). ``None`` by default --
    unchanged behavior for any caller that doesn't provide it (e.g. the old
    dormant VC-thesis pilot). ALSO used (#175, 07/20) to degrade the simulated
    FILL price itself (``risk_guard.simulated_fill_price``, on the FINAL
    alloc) -- the persisted ``entry_price`` (and computed ``qty``) now
    reflects the price actually "paid" by an order of this size on this pool,
    not the spot price quoted before impact.

    ``entry_atr_pct`` (07/19, Gemini cross-review): ATR (volatility) as % of
    entry price, computed once at opening -- persisted as-is, used by position
    management (adaptive trailing stop) instead of fixed ``TRAIL_STOP_PCT``.
    ``None`` by default -- unchanged behavior (fixed-percentage trailing stop)
    for any caller that doesn't provide it.

    ``rr``/``align_score``/``conviction_tier``/``rvol_multiple``/
    ``discovery_channel``/``conviction_process_trail``/
    ``conviction_website_corroborated``/``conviction_posting_cadence`` (07/23,
    operator request: segment winrate/PnL by decision factor to find what
    actually works) -- purely observational, persisted as-is for
    ``performance_breakdown.py``, never used here to size or gate the
    position. All ``None`` by default -- unchanged behavior for any caller
    that doesn't provide them."""
    await _ensure_tables()
    from aria_core.momentum_entry import normalize_contract_case

    contract = normalize_contract_case(contract, chain)
    if not contract or not entry_price or entry_price <= 0:
        return None
    # 27/07 -- scoped to THIS pocket only: an already-open position in a
    # DIFFERENT wallet on the same contract must never block this one (that's
    # the whole point of 3 concurrent pockets).
    if await has_open(contract, wallet=wallet):
        return None
    # 27/07 -- count scoped to THIS pocket (``get_open_positions(wallet=wallet)``)
    # rather than the whole portfolio, so pocket X's own count never blocks
    # pocket Y's first position. The CAP VALUE itself stays the legacy
    # portfolio-wide ``MAX_POSITIONS`` (30) here -- this is a defense-in-depth
    # safety net for ANY caller (see docstring), not the real per-pocket
    # 5/15/unlimited caps (``MAX_POSITIONS_VC``/``_SWING``/``_SCALPING``),
    # which are enforced one level up by the cycle loop itself
    # (``_open_new_entries_for_wallet``) -- keeping this inner net at the old
    # value is what keeps ``test_max_positions_capped`` (wallet="swing", cap
    # 30) passing unchanged.
    if mode != "scalping" and len(await get_open_positions(wallet=wallet)) >= MAX_POSITIONS:
        return None

    # #186 -- defense-in-depth safety chokepoint: checked HERE (not just in
    # run_paper_cycle) to cover ANY current or future caller (e.g. manual
    # command, future real-capital pilot reusing this same function), not just
    # the current heartbeat cycle.
    from aria_core import risk_guard

    blocked, reason = risk_guard.blocks_new_entries(wallet)
    if blocked:
        logger.info("open_position: refused by risk_guard (%s)", reason)
        return None

    start = await starting_capital(wallet=wallet)
    cash = await cash_available(wallet=wallet)
    alloc = alloc_usd if alloc_usd is not None else ALLOC_PCT * start
    # #186 -- risk cap: never reduces alloc beyond its entry value, never a
    # bonus. Without a known invalidation_price, unchanged (trailing stop is
    # the sole guardrail).
    alloc = risk_guard.size_position_by_risk(alloc, entry_price, invalidation_price, start)
    # 07/19 -- price-impact auto-calibrated cap (Gemini cross-review): further
    # reduces alloc if THIS order on THIS specific pool would drop the
    # structural R/R below its floor -- fail-open without known
    # pool_liquidity_usd/target/invalidation (same doctrine as
    # size_position_by_risk just above).
    alloc = risk_guard.cap_alloc_to_price_impact(
        alloc, entry_price, target_price, invalidation_price, pool_liquidity_usd,
    )
    alloc = min(alloc, cash)
    if alloc <= 0:
        return None

    if category:
        from aria_core import paper_trader_risk as risk

        # 27/07 -- scoped to THIS pocket: each of the 3 pockets is its own
        # independent $1M portfolio, so concentration is measured against its
        # OWN deployed capital, never the whole cross-pocket total (a heavy
        # scalping-pocket category exposure must never throttle a VC-pocket
        # buy in the same category, and vice versa).
        opens = await get_open_positions(wallet=wallet)
        already = risk.category_exposure_usd(category, opens)
        alloc = risk.fit_alloc_to_concentration_cap(
            category=category,
            alloc=alloc,
            already_deployed_usd=already,
            starting_capital=start,
            min_alloc=ALLOC_PCT * start * risk.MIN_CONCENTRATION_ALLOC_FRACTION,
        )
        if alloc <= 0:
            return None

    # 07/20 -- #175: simulated FILL price, degraded by the same price-impact
    # model already used to size ``alloc`` above (``cap_alloc_to_price_impact``)
    # -- before this fix, price impact reduced the size but the position still
    # filled at the EXACT quoted spot price, never the price actually "paid"
    # by an order of this size on this pool. Computed on the FINAL alloc
    # (after ALL reductions -- risk/impact/concentration), never the
    # intermediate alloc from ``cap_alloc_to_price_impact``, which may have
    # since been reduced further. ``target_price``/``invalidation_price``
    # stay unchanged (technical chart levels external to us -- our own order
    # doesn't move support/resistance, only the price WE pay). Fail-open to
    # ``entry_price`` without a known ``pool_liquidity_usd`` (e.g. the old
    # dormant VC-thesis pilot) -- unchanged historical behavior for any caller
    # that doesn't provide it.
    # Item #101 (26/07): real DEX swap fee, scoped to scalping mode only (the
    # already-running standard/swing path is left byte-for-byte unchanged --
    # see risk_guard.DEX_SWAP_FEE_PCT's comment).
    fill_price = risk_guard.simulated_fill_price(
        entry_price, alloc, pool_liquidity_usd, apply_swap_fee=(mode == "scalping"),
    )

    qty = alloc / fill_price
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            INSERT INTO paper_position
              (contract, symbol, cost_usd, entry_price, qty, target_price,
               invalidation_price, opened_at, status, high_water_price, initial_qty,
               category, entry_security_json, chain, thesis, entry_atr_pct,
               strategy, entry_liquidity_usd, entry_regime, entry_dev_sold_pct,
               last_liquidity_usd, rr, align_score, conviction_tier, rvol_multiple,
               discovery_channel, conviction_process_trail,
               conviction_website_corroborated, conviction_posting_cadence,
               liquidity_rotation_score, liquidity_rotation_accelerating,
               liquidity_rotation_volume_ratio, mode, gp_low, gp_high, wallet)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (contract, symbol or "", alloc, fill_price, qty, target_price, invalidation_price,
             _now(), fill_price, qty, category or "", entry_security_json or None,
             (chain or "base").lower(), thesis, entry_atr_pct,
             strategy or "momentum", pool_liquidity_usd, entry_regime, entry_dev_sold_pct,
             # 07/22 -- task #4: initialized to the same value as entry_liquidity_usd
             # -- the "sudden drop" comparison (cycle N vs cycle N-1) only makes
             # sense from the 1st management cycle onward; before that, "last
             # observed" == "entry".
             pool_liquidity_usd,
             rr, align_score, conviction_tier, rvol_multiple, discovery_channel,
             conviction_process_trail,
             None if conviction_website_corroborated is None else int(conviction_website_corroborated),
             conviction_posting_cadence,
             liquidity_rotation_score,
             None if liquidity_rotation_accelerating is None else int(liquidity_rotation_accelerating),
             liquidity_rotation_volume_ratio, mode or "standard", gp_low, gp_high, wallet),
        )
        await db.commit()
        pid = cur.lastrowid
    # 27/07 -- resolved by ROW ID, never by bare contract: once 3 pockets can
    # legally hold the SAME contract at once, ``_get_open(contract)`` alone
    # would raise (multi-pocket ambiguity guard, see its docstring) as soon as
    # a 2nd pocket opens a position on a contract another pocket already
    # holds open.
    return await _get_open(contract, position_id=pid) or {"id": pid, "contract": contract, "wallet": wallet}


async def close_position(
    contract: str, exit_price: float, *, reason: str = "manuel", notes: str | None = None,
    position_id: int | None = None,
) -> dict | None:
    """Closes a FICTITIOUS position at the real exit price and records the P&L.
    ``reason`` stays a stable short tag (compared by equality elsewhere/in
    tests); ``notes`` (07/17) carries the full numeric justification --
    separated so as to never break a caller that depends on the exact tag.

    ``position_id`` (27/07, multi-pocket prerequisite): pass the specific
    row's id whenever it's already known (every internal caller inside
    ``_run_paper_cycle_locked`` iterates ``get_open_positions()`` and already
    has ``p["id"]``) -- resolves that EXACT row via ``_get_open``, never
    re-resolving by contract. Without it, ``contract`` alone now raises if
    more than one position shares it (see ``_get_open``) rather than silently
    picking one -- external callers with no ambiguity today (single position
    per contract) are unaffected.

    Final ``pnl_usd`` = P&L of the last leg + ``realized_pnl_partial`` already
    accumulated by any partial profit-takes (07/19, real bug found on position
    #21): ``portfolio_summary()`` only reads ``realized_pnl_partial`` for
    positions still ``open`` -- once ``closed``, only ``pnl_usd`` counts in the
    capital aggregate. Without this addition, the P&L from already-realized
    profit-taking stages silently disappeared from the total capital right at
    final close. ``realized_pnl_partial`` stays unchanged on the row (the share
    of total P&L that came from earlier stages, still visible separately)."""
    await _ensure_tables()
    pos = await _get_open(contract, position_id=position_id)
    if not pos or not exit_price or exit_price <= 0:
        return None
    # Item #101 (26/07): real DEX swap fee on the sell leg -- read from the
    # position's OWN persisted mode (set at open_position time), never a
    # parameter every caller of close_position must remember to pass. Scoped
    # to scalping-mode positions only, same doctrine as the buy-side fee in
    # open_position (see risk_guard.DEX_SWAP_FEE_PCT's comment).
    if pos.get("mode") == "scalping":
        from aria_core.risk_guard import DEX_SWAP_FEE_PCT

        exit_price = exit_price * (1.0 - DEX_SWAP_FEE_PCT)
    proceeds = pos["qty"] * exit_price
    final_leg_pnl = proceeds - pos["cost_usd"]
    pnl_usd = final_leg_pnl + (pos.get("realized_pnl_partial") or 0.0)
    pnl_pct = (exit_price / pos["entry_price"] - 1.0) * 100.0 if pos["entry_price"] else 0.0
    closed_at = _now()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE paper_position
               SET status = 'closed', exit_price = ?, closed_at = ?, pnl_usd = ?,
                   pnl_pct = ?, close_reason = ?, close_notes = ?
             WHERE id = ?
            """,
            (exit_price, closed_at, pnl_usd, pnl_pct, reason, notes, pos["id"]),
        )
        await db.commit()
    return {**pos, "status": "closed", "exit_price": exit_price, "closed_at": closed_at,
            "pnl_usd": pnl_usd, "pnl_pct": pnl_pct, "close_reason": reason, "close_notes": notes}


async def reduce_position(
    contract: str, exit_price: float, sell_qty: float, *, stage: int,
    reason: str = "prise de profit", notes: str | None = None,
    position_id: int | None = None,
) -> dict | None:
    """PARTIAL profit-take: sells a fraction of the position and keeps the rest
    open with a proportionally reduced cost basis (same ``entry_price``, less
    ``qty``/``cost_usd``). The sold leg's P&L is accumulated in
    ``realized_pnl_partial`` -- it stays visible in
    ``cash_available``/``portfolio_summary`` without waiting for the position's
    full close. ``notes`` (07/17): numeric justification of THIS partial take,
    persisted on the still-open row (replaces the previous one -- latest note,
    not a cumulative history).

    ``position_id`` (27/07, multi-pocket prerequisite): same reasoning as
    ``close_position`` -- pass it whenever already known."""
    await _ensure_tables()
    pos = await _get_open(contract, position_id=position_id)
    if not pos or not exit_price or exit_price <= 0 or sell_qty <= 0:
        return None
    # Item #101 (26/07): same real DEX swap fee as close_position, applied to
    # this partial sell leg too -- see its comment.
    if pos.get("mode") == "scalping":
        from aria_core.risk_guard import DEX_SWAP_FEE_PCT

        exit_price = exit_price * (1.0 - DEX_SWAP_FEE_PCT)
    sell_qty = min(sell_qty, pos["qty"])
    frac = sell_qty / pos["qty"] if pos["qty"] else 0.0
    sold_cost = pos["cost_usd"] * frac
    proceeds = sell_qty * exit_price
    pnl_usd = proceeds - sold_cost
    new_qty = pos["qty"] - sell_qty
    new_cost = pos["cost_usd"] - sold_cost
    new_realized_partial = (pos.get("realized_pnl_partial") or 0.0) + pnl_usd
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE paper_position
               SET qty = ?, cost_usd = ?, realized_pnl_partial = ?, tp_stage_hit = ?, close_notes = ?
             WHERE id = ?
            """,
            (new_qty, new_cost, new_realized_partial, stage, notes, pos["id"]),
        )
        await db.commit()
    pnl_pct = (exit_price / pos["entry_price"] - 1.0) * 100.0 if pos["entry_price"] else 0.0
    return {
        **pos, "sold_qty": sell_qty, "exit_price": exit_price, "pnl_usd": pnl_usd,
        "pnl_pct": pnl_pct, "close_reason": reason, "close_notes": notes, "remaining_qty": new_qty,
        # 27/07, real bug found (operator screenshot): the periodic tracking
        # alert built LATER in the same cycle read this position's snapshot
        # from BEFORE this reduction (taken at the top of the management
        # loop, never refreshed) -- displayed the stale pre-reduction
        # cost_usd for one cycle (e.g. "35 000$" right after a partial exit
        # that had already reduced it to "23 333$" in the DB). ``cost_usd``
        # itself stays the OLD value via ``**pos`` (unchanged for any
        # existing caller) -- this new explicit field is what the cycle loop
        # now uses to refresh its own in-memory tracking snapshot.
        "remaining_cost_usd": new_cost,
        "tp_stage_hit": stage,
    }


async def _update_vc_liquidity_watermark(position_id: int, current_liq: float) -> None:
    """Task #4 (07/22): updates ``last_liquidity_usd`` on EVERY management cycle
    of a ``vc_thesis`` position -- never fixed at entry like
    ``entry_liquidity_usd``, this is what enables detecting a SUDDEN drop
    between two consecutive cycles, in addition to (never instead of) the
    cumulative drop since entry already monitored."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE paper_position SET last_liquidity_usd = ? WHERE id = ?",
            (current_liq, position_id),
        )
        await db.commit()


async def _check_vc_dev_wallet_recent_selling(
    contract: str, chain: str, entry_sold_pct: float | None,
) -> tuple[bool, str]:
    """Task #4 (07/22): re-checks the deployer wallet's behavior DURING the
    holding period of a ``vc_thesis`` position -- until now, ``dev_wallet.py``
    was only consulted ONCE, at entry (via
    ``_default_analyzer``/``analyze_vc_with_context``).

    Compares the CURRENT ``sold_pct_of_received`` (fresh, re-scanned) to the
    snapshot taken at opening (``entry_sold_pct``, persisted on the position)
    -- a rise of at least ``VC_DEV_SOLD_DELTA_ALERT_PCT`` percentage points
    signals a significant RECENT sale, never visible in the entry-only
    judgment. ``entry_sold_pct is None`` (deployer/transfers never resolved at
    entry) -> fail-open, no comparison invented without a real baseline. Any
    network failure -> fail-open (never blocking, normal price/liquidity
    monitoring continues)."""
    if entry_sold_pct is None:
        return False, ""
    try:
        from aria_core.services.blockscout import get_blockscout_client
        from aria_core.skills.dev_wallet import gather_dev_wallet_facts

        client = get_blockscout_client(chain)
        info = await client.get_address_info(contract)
        creator = info.creator_address if info.available else None
        if not creator:
            return False, ""
        facts = await gather_dev_wallet_facts(contract, creator, client=client)
    except Exception as exc:  # noqa: BLE001 -- never blocking, monitoring continues
        logger.info("_check_vc_dev_wallet_recent_selling: %s failed (%s)", contract, exc)
        return False, ""

    current = facts.sold_pct_of_received
    if current is None:
        return False, ""
    delta = current - entry_sold_pct
    if delta >= VC_DEV_SOLD_DELTA_ALERT_PCT:
        return True, (
            f"dev wallet a vendu {delta:.1f} points de % supplémentaires depuis l'entrée "
            f"({entry_sold_pct:.1f}% -> {current:.1f}% de sa dotation reçue)"
        )
    return False, ""


async def _update_high_water(
    position_id: int, price: float,
    pending_high_water: float | None = None, pending_since: str | None = None,
) -> None:
    """``pending_high_water``/``pending_since`` (07/20) persist the high-water
    candidacy pending time confirmation (see ``_advance_high_water``) -- ``None``
    (default, backward-compatible) clears any candidacy in progress."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE paper_position SET high_water_price = ?, pending_high_water = ?, "
            "pending_high_water_since = ? WHERE id = ?",
            (price, pending_high_water, pending_since, position_id),
        )
        await db.commit()


async def _update_breakeven_pending(position_id: int, pending_since: str | None) -> None:
    """Persists the breakeven-lock candidacy (see ``_advance_breakeven_pending``)
    -- ``None`` clears any candidacy in progress (price fell back below the
    flash threshold before confirmation)."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE paper_position SET breakeven_pending_since = ? WHERE id = ?",
            (pending_since, position_id),
        )
        await db.commit()


async def _lock_breakeven_floor(position_id: int) -> None:
    """Locks breakeven (Breakeven Hard Floor, see ``_breakeven_floor_threshold``)
    -- irrevocable, never reset elsewhere (no UPDATE function ever sets
    ``breakeven_locked`` back to 0). Also clears the pending candidacy (moot
    once the definitive lock is set)."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE paper_position SET breakeven_locked = 1, breakeven_pending_since = NULL "
            "WHERE id = ?",
            (position_id,),
        )
        await db.commit()


async def _set_position_pocket(position_id: int, pocket: str) -> None:
    """07/22 -- Task 2, satellite pocket. UNIDIRECTIONAL promotion ('main' ->
    'satellite') done by ``run_weekly_reset`` -- no function ever moves a
    position back from 'satellite' to 'main', leaving the satellite pocket
    happens only via its own close (normal management), never via a reset."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE paper_position SET pocket = ? WHERE id = ?", (pocket, position_id),
        )
        await db.commit()


async def portfolio_summary(*, price_lookup=None, wallet: str = "swing") -> dict:
    """Portfolio snapshot: cash, total value (marked to market if price_lookup),
    % return, realized/unrealized P&L, win rate. ``price_lookup(contract)`` async -> price.

    ``wallet`` (27/07, 3-pocket architecture plan): scopes the ENTIRE snapshot
    to ONE pocket -- defaulted to 'swing' (the only actively-traded pocket as
    of this work), never mixing pockets (starting_capital/opens/closed must
    always agree on which portfolio they describe)."""
    start = await starting_capital(wallet=wallet)
    opens = await get_open_positions(wallet=wallet)
    closed = await get_closed_positions(limit=100_000, wallet=wallet)
    realized = (
        sum((p["pnl_usd"] or 0.0) for p in closed)
        + sum((p.get("realized_pnl_partial") or 0.0) for p in opens)
    )
    cash = start - sum(p["cost_usd"] for p in opens) + realized

    from aria_core.risk_guard import simulated_exit_price

    open_value = 0.0
    unrealized = 0.0
    for p in opens:
        price = None
        if price_lookup is not None:
            try:
                price = await price_lookup(p["contract"])
            except Exception:  # noqa: BLE001 — an unavailable price doesn't stop the snapshot
                price = None
        if price and price > 0:
            # 07/22 -- item #18 (stress test): the displayed spot price alone
            # assumes the ENTIRE position could be liquidated with zero
            # slippage -- a fictitious x50 was possible on a pool that had
            # become thin. Discounted by simulated exit impact, same formula
            # as the buy (simulated_fill_price). "Live" liquidity
            # (last_liquidity_usd, vc_thesis only for now) preferred if known,
            # otherwise falls back to ENTRY liquidity -- an honest
            # approximation, never no discount at all instead.
            liq = p.get("last_liquidity_usd") or p.get("entry_liquidity_usd")
            position_value_at_spot = p["qty"] * price
            exit_price = simulated_exit_price(price, position_value_at_spot, liq)
            value = p["qty"] * exit_price
        else:
            value = p["cost_usd"]
        open_value += value
        unrealized += value - p["cost_usd"]

    equity = cash + open_value
    ret_pct = (equity / start - 1.0) * 100.0 if start else 0.0
    wins = [p for p in closed if (p["pnl_usd"] or 0.0) > 0]
    win_rate = (len(wins) / len(closed) * 100.0) if closed else None
    return {
        "starting": start,
        "cash": cash,
        "equity": equity,
        "return_pct": ret_pct,
        "realized_pnl": realized,
        "unrealized_pnl": unrealized,
        "open_positions": len(opens),
        "closed_trades": len(closed),
        "win_rate": win_rate,
    }


# ── FICTITIOUS alerts (operator) — always stamped SIMULATION ──────────────────

def _strategy_label(pos: dict) -> str:
    """Short strategy label for Telegram alerts -- distinguishes what actually
    produced this position (26/07, operator request: every alert used to say
    a fixed "mode trading" header regardless of scalping/standard/vc_thesis,
    making it impossible to tell at a glance which discipline governs a given
    position). ``vc_thesis`` checked first since it's a fully separate pipeline
    (safety_screen/vc_analysis, never the momentum mode switch); otherwise
    falls back to the portfolio-wide scalping/standard switch persisted on
    the position itself (``mode``, never rétroactif -- see get_trading_mode)."""
    if pos.get("strategy") == "vc_thesis":
        return "venture capital"
    if pos.get("mode") == "scalping":
        return "scalping"
    return "swing trading"


def format_buy_alert(pos: dict) -> str:
    name = pos.get("symbol") or (pos.get("contract") or "")[:10]
    # 07/17 -- explicit operator request: show the % of starting capital
    # (STARTING_CAPITAL_USD, never the current equity -- this is exactly the
    # basis each position is sized against, see run_paper_cycle) committed by
    # THIS position, not just the raw $ amount.
    cost = pos.get("cost_usd") or 0.0
    pct_of_capital = (cost / STARTING_CAPITAL_USD * 100.0) if STARTING_CAPITAL_USD else 0.0
    lines = [
        f"🧪 SIMULATION — portefeuille papier 1 M$ ({_strategy_label(pos)})",
        f"ACHAT FICTIF {name}",
        f"Contrat {pos.get('contract', '')}",
        f"Entrée {pos['entry_price']:.6g} · taille {cost:,.0f} $ ({pct_of_capital:.1f}% du capital de départ)",
    ]
    if pos.get("target_price"):
        lines.append(f"Cible {pos['target_price']:.6g}")
    if pos.get("invalidation_price"):
        lines.append(f"Invalidation {pos['invalidation_price']:.6g}")
    # #197 (07/15) -- the VC thesis (why ARIA is entering, not just at what
    # price) was computed but never shown. Displayed here truncated (mobile
    # Telegram readability) -- the FULL text is always persisted as-is in the
    # DB (thesis, see open_position), never truncated where it matters for
    # after-the-fact verification.
    thesis = (pos.get("thesis") or "").strip()
    if thesis:
        lines.append(f"Thèse : {thesis[:500]}")
    if pos.get("contract"):
        lines.append(f"DexScreener : {token_url(pos['contract'], chain=pos.get('chain') or 'base')}")
    lines.append("Aucun argent réel — preuve de performance en cours.")
    return "\n".join(lines)


def _format_tracked_position_line(t: dict) -> str:
    """One compact line for a still-open position, its DexScreener link GLUED to
    the SAME line rather than a separate one.

    24/07 -- real UX bug found by the operator: a lone URL on its own line gets
    extra vertical spacing ABOVE it in the Telegram client (link-preview
    styling), making it visually read as belonging to the FOLLOWING position
    line instead of the one it's actually about. Appending the URL to the same
    line removes the ambiguity outright, regardless of client rendering quirks."""
    name = t.get("symbol") or (t.get("contract") or "")[:10]
    entry = t.get("entry_price") or 0.0
    price = t.get("price") or 0.0
    qty = t.get("qty") or 0.0
    cost = t.get("cost_usd") or 0.0
    value = qty * price
    pnl = value - cost
    pnl_pct = (price / entry - 1.0) * 100.0 if entry else 0.0
    sign = "+" if pnl >= 0 else ""
    # 07/17 -- explicit operator request: capital invested + % of starting
    # capital (STARTING_CAPITAL_USD, the fixed basis each position is sized
    # against at opening -- never the current equity, which would move
    # afterward and no longer faithfully represent the size decided AT THE
    # TIME of the buy).
    pct_of_capital = (cost / STARTING_CAPITAL_USD * 100.0) if STARTING_CAPITAL_USD else 0.0
    # 26/07 -- per-position label, not a single header one: this alert can
    # list positions opened under DIFFERENT strategies/modes at once (a
    # standard/swing position still open while the portfolio-wide switch has
    # since moved to scalping, mode is never rétroactif -- see get_trading_mode).
    line = (
        f"{name} ({_strategy_label(t)}) : {price:.6g} ({sign}{pnl_pct:.1f}%) · P&L latent {sign}{pnl:,.0f} $ · "
        f"capital {cost:,.0f} $ ({pct_of_capital:.1f}% du capital de départ)"
    )
    # 27/07, operator request (seeing a partial-exit alert with no entry
    # price/hold time): same two fields as format_sell_alert/
    # format_partial_exit_alert, appended here too for the periodic tracking
    # line -- entry_price is already computed above (``entry``), reused as-is.
    if entry:
        hold = _format_hold_duration(t.get("opened_at"))
        line += f" · entrée {entry:.6g}" + (f" · détenue {hold}" if hold else "")
    if t.get("contract"):
        line += f" · {token_url(t['contract'], chain=t.get('chain') or 'base')}"
    return line


async def build_open_positions_tracking_lines(*, price_lookup=None, wallet: str | None = None) -> list[str]:
    """On-demand equivalent of the per-position lines inside
    ``format_position_tracking_alert`` -- WITHOUT its header/footer -- for a
    caller (``/feedback``) that already renders its own aggregated header and
    just wants the same compact, one-line-per-position rendering appended.
    Reuses ``get_open_positions()`` (no duplicated query); an unavailable
    live price degrades to the entry price (never blocks, never invents a
    made-up figure beyond that honest fallback).

    27/07 -- 3-pocket architecture plan, Phase 5: ``wallet`` scopes the lines
    to ONE pocket (``None`` keeps the historical behavior -- every pocket's
    open positions, used by callers that never scoped this before)."""
    opens = await get_open_positions(wallet=wallet)
    tracked = []
    for p in opens:
        price = None
        if price_lookup is not None:
            try:
                price = await price_lookup(p["contract"])
            except Exception:  # noqa: BLE001 -- an unavailable price never blocks the block
                price = None
        tracked.append({
            "contract": p.get("contract"),
            "symbol": p.get("symbol"),
            "chain": p.get("chain"),
            "entry_price": p.get("entry_price"),
            "price": price if price and price > 0 else p.get("entry_price"),
            "qty": p.get("qty"),
            "cost_usd": p.get("cost_usd"),
            "mode": p.get("mode"),
            "strategy": p.get("strategy"),
        })
    return [_format_tracked_position_line(t) for t in tracked]


def format_position_tracking_alert(
    tracked: list[dict], *, cash: float | None = None, equity: float | None = None,
) -> str:
    """PERIODIC tracking of already-open positions (#197, 07/15) -- not just on
    buy/sell. ``tracked``: list of dicts {contract, symbol, entry_price, price,
    qty, cost_usd}, one entry per position STILL open at the end of the cycle
    (positions closed THIS round are already covered by format_sell_alert, not
    duplicated here). Empty list -> empty string (nothing to send, the caller
    doesn't notify).

    ``cash``/``equity`` (07/17): found under real conditions -- the header
    displayed "portefeuille papier 1 M$" hardcoded on EVERY alert, regardless
    of the REAL value at the time (already $998,415 after the first loss) --
    the operator couldn't know how much was left without separately checking
    /feedback or /ledger. Optional (``None`` -> old generic label, an honest
    degradation rather than an invented figure if the caller doesn't compute
    them)."""
    if not tracked:
        return ""
    n = len(tracked)
    position_word = "position ouverte" if n == 1 else "positions ouvertes"
    if equity is not None and cash is not None:
        header = (
            f"🧪 SIMULATION — suivi positions ouvertes "
            f"(portefeuille papier : équité {equity:,.0f} $, cash {cash:,.0f} $, "
            f"{n} {position_word})"
        )
    else:
        header = f"🧪 SIMULATION — suivi positions ouvertes (portefeuille papier 1 M$, {n} {position_word})"
    lines = [header] + [_format_tracked_position_line(t) for t in tracked]
    lines.append("Aucun argent réel.")
    return "\n".join(lines)


def _format_hold_duration(opened_at: str | None, *, until: str | None = None) -> str:
    """27/07, operator request (seeing a partial-exit alert with no entry
    price/hold time): human-readable duration since ``opened_at`` (ISO,
    persisted at ``open_position`` time) up to ``until`` (ISO, e.g.
    ``closed_at`` for a full close) or now (partial exit / still-open
    tracking, no close timestamp exists yet). ``""`` if ``opened_at`` is
    missing/unparsable -- never a fabricated duration."""
    if not opened_at:
        return ""
    try:
        start = datetime.fromisoformat(opened_at)
        end = datetime.fromisoformat(until) if until else datetime.now(timezone.utc)
    except ValueError:
        return ""
    delta = end - start
    total_minutes = max(0, int(delta.total_seconds() // 60))
    days, rem_minutes = divmod(total_minutes, 1440)
    hours, minutes = divmod(rem_minutes, 60)
    if days > 0:
        return f"{days}j {hours}h"
    if hours > 0:
        return f"{hours}h{minutes:02d}"
    return f"{minutes}min"


def format_sell_alert(closed: dict) -> str:
    name = closed.get("symbol") or (closed.get("contract") or "")[:10]
    pnl = closed.get("pnl_usd") or 0.0
    pct = closed.get("pnl_pct") or 0.0
    sign = "+" if pnl >= 0 else ""
    lines = [
        f"🧪 SIMULATION — portefeuille papier 1 M$ ({_strategy_label(closed)})",
        f"VENTE FICTIVE {name} ({closed.get('close_reason', '')})",
        f"Sortie {closed['exit_price']:.6g} · P&L {sign}{pnl:,.0f} $ ({sign}{pct:.1f}%)",
    ]
    hold = _format_hold_duration(closed.get("opened_at"), until=closed.get("closed_at"))
    entry_line = f"Entrée {closed['entry_price']:.6g}" if closed.get("entry_price") else ""
    if entry_line and hold:
        lines.append(f"{entry_line} · détenue {hold}")
    elif entry_line:
        lines.append(entry_line)
    elif hold:
        lines.append(f"Détenue {hold}")
    notes = (closed.get("close_notes") or "").strip()
    if notes:
        lines.append(f"Pourquoi : {notes}")
    if closed.get("contract"):
        lines.append(f"DexScreener : {token_url(closed['contract'], chain=closed.get('chain') or 'base')}")
    lines.append("Aucun argent réel.")
    return "\n".join(lines)


def format_partial_exit_alert(partial: dict) -> str:
    name = partial.get("symbol") or (partial.get("contract") or "")[:10]
    pnl = partial.get("pnl_usd") or 0.0
    pct = partial.get("pnl_pct") or 0.0
    sign = "+" if pnl >= 0 else ""
    lines = [
        f"🧪 SIMULATION — portefeuille papier 1 M$ ({_strategy_label(partial)})",
        f"PRISE DE PROFIT PARTIELLE FICTIVE {name} ({partial.get('close_reason', '')})",
        f"Sortie {partial['exit_price']:.6g} · {sign}{pnl:,.0f} $ ({sign}{pct:.1f}%) sur la tranche vendue",
        f"Position restante : {partial.get('remaining_qty', 0):.6g} unités",
    ]
    hold = _format_hold_duration(partial.get("opened_at"))
    entry_line = f"Entrée {partial['entry_price']:.6g}" if partial.get("entry_price") else ""
    if entry_line and hold:
        lines.append(f"{entry_line} · détenue {hold}")
    elif entry_line:
        lines.append(entry_line)
    elif hold:
        lines.append(f"Détenue {hold}")
    notes = (partial.get("close_notes") or "").strip()
    if notes:
        lines.append(f"Pourquoi : {notes}")
    if partial.get("contract"):
        lines.append(f"DexScreener : {token_url(partial['contract'], chain=partial.get('chain') or 'base')}")
    lines.append("Aucun argent réel.")
    return "\n".join(lines)


def format_summary(summary: dict) -> str:
    wr = summary.get("win_rate")
    wr_str = f"{wr:.0f}%" if wr is not None else "n/a"
    return "\n".join([
        "🧪 SIMULATION — portefeuille papier 1 M$ (mode trading)",
        f"Valeur totale : {summary['equity']:,.0f} $ ({summary['return_pct']:+.2f}%)",
        f"Cash {summary['cash']:,.0f} $ · {summary['open_positions']} positions ouvertes",
        f"Réalisé {summary['realized_pnl']:+,.0f} $ · latent {summary['unrealized_pnl']:+,.0f} $",
        f"Trades clôturés {summary['closed_trades']} · réussite {wr_str}",
        "Aucun argent réel — track record de preuve.",
    ])


# ── Prod defaults (network/LLM), injectable in tests ───────────────────────────────────

async def _bonding_pair_lookup(contract: str):
    """24/07 -- bonding-entry chantier: a token still on a Virtuals bonding
    curve has NO DexScreener pair (no DEX pool until graduation) -- without
    this branch, ``_default_pair_lookup`` would return ``None`` for every
    single management cycle of a bonding position, forever (price never
    refreshed, stop/TP never checked, the position just sits there). Returns
    a real ``PairSnapshot`` (same type ``_default_pair_lookup`` returns for a
    normal chain, so every call site downstream needs zero changes) built
    from Virtuals-native data: price from the latest real trade (converted
    $VIRTUAL -> USD, see ``virtuals.virtual_usd_rate``), liquidity already in
    USD. ``pair_address`` left empty (never a fabricated address) --
    ``_robust_close_price`` short-circuits to the spot price already computed
    here in that case (honest degradation, see its own docstring), the same
    behavior as any other pair with an unknown pool address.

    ``None`` if the token can no longer be resolved, or if the $VIRTUAL/USD
    rate is unavailable (never a fabricated USD price) -- same semantics as
    ``_default_pair_lookup``'s "no liquid pair found".

    Graduation handoff: once a bonding token graduates, it gets a REAL Base
    DEX pool and ``vp-api``'s trade history for it is unconfirmed/likely
    stale (never verified live post-graduation) -- ``is_in_bonding(token)``
    turning ``False`` is the signal to hand off to the exact same DexScreener
    path a standard momentum position already uses, rather than keep reading
    a bonding-only data source past its relevance."""
    from aria_core.services.dexscreener import PairSnapshot, fetch_token_pairs
    from aria_core.services.virtuals import is_in_bonding, virtual_usd_rate, virtuals_client

    token = await virtuals_client.fetch_by_address(contract, chain="BASE")
    if token is None:
        return None

    if not is_in_bonding(token):
        contract_lower = (contract or "").strip().lower()
        pairs = await fetch_token_pairs(contract, chain="base")
        own_pairs = [p for p in pairs if (p.base_address or "").lower() == contract_lower]
        if not own_pairs:
            return None
        return max(own_pairs, key=lambda p: p.liquidity_usd)

    trades = await virtuals_client.fetch_recent_trades(contract, limit=1)
    if not trades:
        return None
    rate = await virtual_usd_rate()
    if rate is None:
        return None
    price_usd = trades[0].price * rate
    if price_usd <= 0:
        return None
    return PairSnapshot(
        price_usd=price_usd,
        liquidity_usd=token.liquidity_usd or 0.0,
        base_address=(contract or "").strip().lower(),
        base_symbol=token.symbol or "",
    )


async def _doppler_pair_lookup(contract: str):
    """24/07 -- Doppler chantier: same gap as ``_bonding_pair_lookup`` above,
    for a Bankr/Doppler-launched token instead of a Virtuals bonding-curve
    one -- a Doppler token's Uniswap v4 pool has NO DexScreener entry either
    (confirmed empirically: neither CLOWNS nor BANK, both real, live pools,
    show up there), so without this branch a position opened on one would
    sit forever with a frozen entry price, its trailing stop/take-profit
    never able to fire.

    Returns a real ``PairSnapshot`` built from ``services.doppler`` (current
    ``sqrtPriceX96`` -> price, converted to USD via the real WETH/USD rate).
    ``liquidity_usd`` left at 0.0 -- this module doesn't yet compute a pool's
    total value locked, an honest gap, not an invented number.
    ``pair_address`` left empty (never fabricated) -- same degradation as
    ``_bonding_pair_lookup``, ``_robust_close_price`` falls back to the spot
    price already computed here. ``None`` if the pool can't be found or the
    price read fails -- never a fabricated price."""
    from aria_core.services import doppler
    from aria_core.services.dexscreener import PairSnapshot

    price_usd = await doppler.get_token_price_usd(contract)
    if price_usd is None or price_usd <= 0:
        return None
    return PairSnapshot(
        price_usd=price_usd,
        liquidity_usd=0.0,
        base_address=(contract or "").strip().lower(),
    )


async def _default_pair_lookup(contract: str, *, chain: str = "base"):
    """07/17 -- factored out of ``_default_price_lookup`` so the open-position
    management loop can reuse the SAME DexScreener pair for both the current
    price AND the volume/liquidity ratio re-scan
    (``paper_trader_risk.rescan_open_position``), without duplicating the
    network call. Returns ``None`` if no liquid pair is found -- never an
    invented pair.

    07/19 -- same fix as ``momentum_entry._best_pair`` (real bug, position
    PLAZM #21 == actually ESHARE): ``fetch_token_pairs`` returns ANY pair
    involving ``contract``, including as a mere QUOTE of ANOTHER token's pool
    -- without a filter on ``PairSnapshot.base_address``, this function could
    return the price/volume/liquidity of a completely different token (the one
    using ``contract`` as the quote of a pool more liquid than its own). It is
    THIS function that feeds the periodic Telegram tracking of open positions
    -- the wrong price displayed for position #21 (~0.0176 instead of the real
    ESHARE price, ~$5.84) came directly from this, not just from the entry.

    24/07 -- bonding-entry chantier: ``chain`` doubles as the bonding marker
    (``bonding_entry.CHAIN_MARKER``, never a real DexScreener chain id) --
    routed to ``_bonding_pair_lookup`` instead, DexScreener has structurally
    no pair for a token still on a bonding curve.

    24/07 -- Doppler chantier: same doubling for ``doppler.CHAIN_MARKER``
    (a Bankr-launched token's Uniswap v4 pool also has no DexScreener
    entry)."""
    from aria_core.bonding_entry import CHAIN_MARKER
    from aria_core.services import doppler

    if chain == CHAIN_MARKER:
        return await _bonding_pair_lookup(contract)
    if chain == doppler.CHAIN_MARKER:
        return await _doppler_pair_lookup(contract)

    from aria_core.services.dexscreener import fetch_token_pairs

    contract_lower = (contract or "").strip().lower()
    pairs = await fetch_token_pairs(contract, chain=chain)
    own_pairs = [p for p in pairs if (p.base_address or "").lower() == contract_lower]
    if not own_pairs:
        return None
    return max(own_pairs, key=lambda p: p.liquidity_usd)


async def _default_price_lookup(contract: str, *, chain: str = "base") -> float | None:
    """Generalized multi-chain (#194) -- DexScreener directly (already
    multi-chain, services/dexscreener.py) rather than scan_base_token
    (Base-specific, and above all much heavier: full honeypot + TA +
    mint-authority for just a tracking price). ``chain`` defaults to
    ``"base"`` -- unchanged behavior for any caller that doesn't specify it."""
    best = await _default_pair_lookup(contract, chain=chain)
    if best is None:
        return None
    return best.price_usd if best.price_usd > 0 else None


# 07/20 -- #173, cross-review: the weekly reset used to force-close every
# still-open position on a SINGLE instantaneous spot tick
# (``_default_price_lookup``) -- vulnerable to an isolated wick occurring
# right at reset time (same risk class already handled elsewhere for ongoing
# management -- trailing-stop anti-wick, Breakeven Hard Floor -- but never for
# THIS specific one-off event). Short window: the reset is weekly, no need for
# long history, just enough to withstand ONE aberrant tick.
_RESET_PRICE_CANDLE_WINDOW = 5
_RESET_PRICE_MIN_CANDLES = 3


async def _robust_close_price(contract: str, chain: str, pair) -> float | None:
    """ROBUST close price for the weekly reset (#173) -- median of the last
    ``_RESET_PRICE_CANDLE_WINDOW`` OHLCV candles (same 5-stage cascade as the
    momentum pipeline, ``momentum_entry._fetch_candles`` -- never a second
    duplicated client) rather than a single spot tick: an isolated wick on ONE
    candle doesn't dominate a median over several. Below
    ``_RESET_PRICE_MIN_CANDLES`` usable candles (missing/invalid candles) ->
    ``None``, the caller then falls back to the spot price already on hand
    (``pair.price_usd``, zero extra network call) -- never worse than
    historical behavior, never blocking."""
    if pair is None or not pair.pair_address:
        return None
    from aria_core import momentum_entry

    try:
        candles = await momentum_entry._fetch_candles(
            pair.pair_address, chain, contract=contract, pair=pair,
        )
    except Exception:  # noqa: BLE001 — never blocking, the caller degrades to spot
        return None
    closes = sorted(
        c.close for c in candles[-_RESET_PRICE_CANDLE_WINDOW:] if c.close and c.close > 0
    )
    if len(closes) < _RESET_PRICE_MIN_CANDLES:
        return None
    mid = len(closes) // 2
    if len(closes) % 2 == 1:
        return closes[mid]
    return (closes[mid - 1] + closes[mid]) / 2.0


async def _default_analyzer(contract: str) -> dict | None:
    """Signal for a contract from the REAL VC analysis. Returns action + levels."""
    from aria_core.skills.vc_analysis import analyze_vc_with_context
    from aria_core import paper_trader_risk as risk

    result, ctx = await analyze_vc_with_context(contract)
    action = "BUY" if getattr(result, "recommandation", "") == "BUY" else "HOLD"
    price = ctx.best_pair.price_usd if ctx.best_pair else None
    target = _num(getattr(result, "cible", None)) or (ctx.ta_entry.cible if ctx.ta_entry else None)
    inval = _num(getattr(result, "invalidation", None)) or (
        ctx.ta_entry.invalidation if ctx.ta_entry else None
    )
    category = risk.derive_category(ctx.launchpad, bonding_phase=ctx.bonding_phase)
    entry_snapshot = await risk.capture_entry_snapshot(contract, ctx)
    return {
        "action": action,
        "symbol": ctx.best_pair.base_symbol if ctx.best_pair else "",
        "price": price,
        "target": target,
        "invalidation": inval,
        "category": category,
        "entry_security_json": entry_snapshot.to_json(),
        # #197 (07/15) -- VCResult.these was already computed here but never
        # forwarded: lost as soon as this function returned. Forwarded up to
        # open_position() by run_paper_cycle below.
        "these": getattr(result, "these", "") or "",
        # 07/20 -- Formula B: this pipeline (safety_screen/vc_analysis,
        # fundamentals + safety, never Fibonacci/RSI) sources "vc_thesis"
        # positions -- exit without a trailing stop, fundamental invalidation
        # (liquidity), see paper_trader.py. No position is opened via this path
        # on the current $1M test (momentum default, see
        # _momentum_candidates_and_chain_map below) -- infrastructure ready for
        # when the 85% VC pocket resumes.
        "strategy": "vc_thesis",
        # 07/20 -- #174: forwarded to the real sizing (run_paper_cycle,
        # risk_guard.vc_thesis_alloc_usd) -- before this fix, never passed to
        # open_position, so every vc_thesis position silently fell back to the
        # MAX cap (5% of capital) regardless of the LLM's real judgment (0-10%).
        "taille_pct": _num(getattr(result, "taille_pct", None)),
        # ``liquidity_usd`` -- reference for fundamental invalidation during
        # the holding period (structural drop vs. entry). None if no pair
        # resolved -- never an invented value, the % check below is then
        # simply fail-open (only the absolute floor stays active).
        "liquidity_usd": ctx.best_pair.liquidity_usd if ctx.best_pair else None,
        # 07/22 -- task #4: snapshot of the deployer wallet at entry (share of
        # its allocation already resold) -- reference for detecting a
        # significant RECENT sale during the holding period (Formula B,
        # post-entry monitoring). None if the deployer or its transfers
        # couldn't be resolved -- never an invented value, the in-holding
        # check is then simply fail-open.
        "dev_sold_pct": getattr(ctx, "dev_sold_pct", None),
    }


async def _bonding_candidates(*, limit: int = 20) -> list[str]:
    """24/07, bonding-entry chantier: Virtuals bonding-curve candidates,
    sourced the SAME way ``launchpad_discovery.discover_bonding_candidates``
    already does for the (dormant) VC absorber -- reused here, not
    duplicated. Fails open to an empty list (never blocks the momentum
    cycle) -- a Virtuals outage just means zero bonding candidates this
    cycle, same degradation as every other candidate source in this
    function's caller."""
    from aria_core.services.launchpad_discovery import discover_bonding_candidates

    try:
        by_launchpad = await discover_bonding_candidates(limit_per_launchpad=limit)
    except Exception as exc:  # noqa: BLE001 — never blocking
        logger.info("_bonding_candidates: discovery failed (%s)", exc)
        return []
    contracts = by_launchpad.get("virtuals_bonding") or []
    return contracts[:limit]


async def _momentum_candidates_and_chain_map(*, limit: int = 20) -> tuple[list[str], dict[str, str]]:
    """#194, momentum pivot -- default candidate source for THIS TEST (replaces
    ``candidate_ranking.top_candidates()`` ONLY as ``run_paper_cycle``'s
    default when neither ``candidates`` nor ``analyzer`` are provided by the
    caller -- ``screened_pool``/the 85% VC pocket are neither modified nor used
    less elsewhere, explicit and reversible operator decision). Returns the
    list of contracts (keeps its historical ``list[str]`` shape, unchanged for
    the rest of the loop) + the contract->chain table for the momentum
    analyzer below.

    24/07, bonding-entry chantier: Virtuals bonding candidates are appended
    to the SAME list, tagged ``bonding_entry.CHAIN_MARKER`` in the chain map
    instead of a real chain id -- wired directly into this active $1M test
    (operator's explicit choice, not a separate/dormant pocket). A contract
    already present via the standard momentum discovery (already graduated,
    real DEX pair) keeps its real chain -- bonding sourcing never overwrites
    an existing entry."""
    from aria_core import momentum_entry
    from aria_core.bonding_entry import CHAIN_MARKER

    found = await momentum_entry.discover_momentum_candidates()
    chain_by_contract = {c["contract"]: c["chain"] for c in found}
    contracts = [c["contract"] for c in found[:limit]]

    bonding_contracts = await _bonding_candidates(limit=limit)
    for addr in bonding_contracts:
        if addr in chain_by_contract:
            continue
        chain_by_contract[addr] = CHAIN_MARKER
        contracts.append(addr)

    return contracts, chain_by_contract


def _default_momentum_analyzer(
    chain_by_contract: dict[str, str], weekly_context: dict | None = None,
    current_regime: str | None = None, *, relaxed: bool = False, mode: str = "standard",
):
    """Closes over the contract->chain table built at sourcing time (#194) --
    keeps the historical ``analyzer(contract)`` signature unchanged, no
    existing caller (tests, other pilots) is affected. ``weekly_context``
    (07/18)/``current_regime`` (07/20, Regime Switch), both optional: computed
    ONCE per cycle by the caller (see ``_run_paper_cycle_locked``), passed
    as-is to each candidate -- never recomputed per candidate.

    ``relaxed`` (07/23, daily-trade-floor): passes ``relaxed=True`` to
    ``evaluate_momentum_entry`` so the daily-floor cycle can sample ARIA's best
    available pick with the quality bars waived (safety always enforced) --
    default ``False``, unchanged behavior for the normal path.

    ``mode`` (Item #101, 26/07): forwarded as-is to
    ``evaluate_momentum_entry`` -- ``"standard"`` (default, unchanged) or
    ``"scalping"`` (resolved ONCE per cycle by the caller via
    ``get_trading_mode()``, a portfolio-wide switch, never per-candidate).

    24/07, bonding-entry chantier: a contract tagged ``bonding_entry.
    CHAIN_MARKER`` in ``chain_by_contract`` is routed to
    ``evaluate_bonding_entry`` instead -- a wholly separate decision engine
    (no DexScreener/GoPlus dependency, see ``bonding_entry.py``'s own
    docstring for why). ``relaxed`` (daily-trade-floor) is NOT forwarded to
    it -- V1, deliberately out of scope (see ``evaluate_bonding_entry``'s own
    docstring on why its gates are already simpler/fewer than the standard
    pipeline's)."""
    from aria_core import bonding_entry, momentum_entry

    async def analyzer(contract: str) -> dict | None:
        chain = chain_by_contract.get(contract, "base")
        if chain == bonding_entry.CHAIN_MARKER:
            return await bonding_entry.evaluate_bonding_entry(
                contract, weekly_context=weekly_context, current_regime=current_regime,
            )
        return await momentum_entry.evaluate_momentum_entry(
            contract, chain, weekly_context=weekly_context, current_regime=current_regime,
            relaxed=relaxed, mode=mode,
        )

    return analyzer


# ── 3-pocket architecture, Phase 2 (27/07) ───────────────────────────────────
# Approved plan: scalping/swing/VC run as 3 PERMANENTLY CONCURRENT independent
# $1M paper wallets (Phase 1, commit 1d6ba7c1, migrated the schema -- zero
# behavior change). This gate turns on the real concurrent-sourcing loop.

def multi_pocket_sourcing_enabled() -> bool:
    """Dedicated gate, OFF by default (fail-closed) -- same idiom as
    ``daily_trade_floor_enabled()`` below. While OFF, ``_run_paper_cycle_locked``
    behaves EXACTLY as before this chantier: a single active pocket ("swing"),
    driven by the existing ``trading_mode`` switch (``get_trading_mode``/
    ``set_trading_mode``, still 'swing'-scoped -- Phase 2 doesn't retire it,
    see their own docstrings). Once ON, all 3 pockets (scalping/swing/vc)
    source new entries INDEPENDENTLY every cycle, each with its own
    candidates/analyzer/position cap -- the SAME contract can legally be held
    by 2 or 3 pockets simultaneously (see ``_open_new_entries_for_wallet``)."""
    return os.environ.get("ARIA_MULTI_POCKET_SOURCING_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


# ── Daily trade FLOOR (07/23, diagnostic) ────────────────────────────────────

def daily_trade_floor_enabled() -> bool:
    """Dedicated gate, OFF by default (fail-closed). Turns on the diagnostic
    daily-trade-floor cycle (``run_daily_trade_floor_cycle``)."""
    return os.environ.get("ARIA_DAILY_TRADE_FLOOR_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


async def count_positions_opened_today(*, now: datetime | None = None, wallet: str = "swing") -> int:
    """Number of positions OPENED since 00:00 UTC today (live ``paper_position``
    table). ``opened_at`` is stored as an ISO-8601 string in the same
    ``+00:00`` format as ``day_start`` below, so the string comparison is a
    valid chronological one. A weekly reset (rare -- 7-day cadence) archives the
    live table, so right after one this could momentarily undercount; acceptable
    for a soft diagnostic floor (never a hard invariant).

    ``wallet`` (27/07, 3-pocket architecture plan): defaults to ``"swing"`` --
    the ONLY pocket this diagnostic floor ever books into (see
    ``_run_daily_trade_floor_locked``'s own ``wallet="swing"`` comment on its
    ``open_position`` call). Gate OFF: byte-for-byte unchanged (only "swing"
    ever holds a position). Gate ON: without this scoping, scalping's
    unlimited/high-frequency trades would inflate this count, making the floor
    believe today's target is already met while "swing" itself had zero --
    defeating the floor's entire diagnostic purpose."""
    now = now or datetime.now(timezone.utc)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    await _ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM paper_position WHERE opened_at >= ? AND wallet = ?",
            (day_start, wallet),
        ) as cur:
            row = await cur.fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def _daily_floor_target(now: datetime) -> int:
    """Pro-rata floor target for the current point in the day: paces the
    ``DAILY_TRADE_FLOOR`` evenly rather than dumping all of them at once (or
    all at 23:59). ``ceil`` so the target becomes 1 as soon as the day starts
    (ARIA is nudged to act early), reaching ``DAILY_TRADE_FLOOR`` by day's end."""
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    fraction = min(1.0, max(0.0, (now - day_start).total_seconds() / 86400.0))
    return math.ceil(DAILY_TRADE_FLOOR * fraction)


async def run_daily_trade_floor_cycle(*, notifier=None, now: datetime | None = None) -> dict:
    """Diagnostic floor (07/23, operator: "force ARIA to make at least 5 trades/
    day so we can judge her picks, even if she loses"). An INDEPENDENT additive
    cycle that never touches the normal ``run_paper_cycle`` decision path -- it
    only tops up small, tagged trades when ARIA is behind the daily pace.

    Guarantees preserved:
      - Hard SAFETY guardrails always enforced (relaxed momentum eval waives
        only quality bars, never scam protection).
      - Respects the risk circuit breaker (operator decision 07/23): stops
        forcing if the drawdown / consecutive-loss hard stop is armed.
      - Respects ``MAX_POSITIONS``, available cash, and never re-buys a contract
        already open.
      - Forced trades are sized by the SAME dynamic risk/ATR formula as a
        normal conviction pick (25/07, ``compute_entry_alloc`` -- no longer a
        fixed 1% ceiling) and tagged ``discovery_channel="floor"`` so
        ``/performance`` separates them from ARIA's other entries by SOURCE,
        never by size.
      - Kill-switch (``/stop``) honored (this path bypasses ``heartbeat._tick``).

    Shares ``_run_cycle_lock`` with ``run_paper_cycle`` -- never two cycles
    mutating the portfolio at once."""
    if not daily_trade_floor_enabled():
        return {"outcome": "skipped", "reason": "gate_off"}
    from aria_core import outgoing_pause

    if outgoing_pause.is_paused():
        return {"outcome": "skipped", "reason": "paused"}
    async with _run_cycle_lock:
        return await _run_daily_trade_floor_locked(notifier=notifier, now=now)


async def _run_daily_trade_floor_locked(*, notifier=None, now: datetime | None = None) -> dict:
    """Body of ``run_daily_trade_floor_cycle`` -- only under ``_run_cycle_lock``."""
    await _ensure_tables()
    from aria_core import risk_guard

    now = now or datetime.now(timezone.utc)
    actions: dict = {"outcome": "ok", "opened": [], "target": 0, "already_today": 0}

    # Risk circuit breaker (operator decision 07/23): the floor never forces a
    # trade past the drawdown / consecutive-loss hard stop -- observing her risk
    # management kick in is itself diagnostic.
    # 27/07 -- 3-pocket architecture plan, Phase 3: this diagnostic floor always
    # books into "swing" (see the wallet="swing" comment on open_position()
    # below) -- its own risk check must stay scoped to that SAME pocket now
    # that risk_guard's state is per-pocket, never a stale unscoped call.
    risk_state = await risk_guard.evaluate_portfolio_risk(wallet="swing")
    if risk_state.blocked:
        actions["outcome"] = "skipped"
        actions["reason"] = "risk_circuit_breaker"
        return actions

    today = await count_positions_opened_today(now=now)
    target = _daily_floor_target(now)
    actions["already_today"] = today
    actions["target"] = target
    deficit = target - today
    if deficit <= 0:
        actions["outcome"] = "on_pace"
        return actions

    to_open = min(deficit, FLOOR_MAX_OPENS_PER_CYCLE)
    start = await starting_capital()

    # 25/07, operator request: ARIA must know her real equity and P&L against
    # THIS window's own target (not the normal weekly +10%) -- same mechanism
    # as the weekly cycle's pacing context (_weekly_pacing_line), just fed
    # this window's real numbers so the "Contexte de rythme" line in her
    # prompt reflects a 24h/$75k target instead of the dormant weekly one.
    weekly_context: dict | None = None
    try:
        target_equity = start + DAILY_FLOOR_TARGET_PROFIT_USD
        progress_pct = (risk_state.equity / start - 1.0) * 100.0 if start else 0.0
        target_pct = (DAILY_FLOOR_TARGET_PROFIT_USD / start) * 100.0 if start else 0.0
        weekly_context = {
            "cycle_number": await get_current_cycle_number(),
            "day": 1,
            "days_total": 1,
            "equity": risk_state.equity,
            "target_equity": target_equity,
            "progress_pct": progress_pct,
            "remaining_pct": target_pct - progress_pct,
        }
    except Exception as exc:  # noqa: BLE001 -- never blocking, degrades to no context
        logger.info("daily_floor: pacing context unavailable (%s)", exc)
        weekly_context = None

    from aria_core.skills import market_sentiment

    try:
        current_regime = await market_sentiment.resolve_meta_regime()
    except Exception:  # noqa: BLE001
        current_regime = market_sentiment.META_REGIME_NEUTRAL

    # 26/07 -- real bug found (operator report via a live Telegram screenshot):
    # this cycle never resolved trading_mode, so it always forwarded the
    # "standard" default to _default_momentum_analyzer even while the
    # portfolio-wide switch was set to "scalping" -- a real position (AERO)
    # was opened this way, its thesis showing the full conviction_research
    # diligence (Website/Docs/X/Tavily/x402 twit.sh) that scalping mode is
    # specifically supposed to skip (Item #101). Same resolution as
    # _run_paper_cycle_locked (get_trading_mode(), once per cycle) -- this
    # additive cycle must never silently diverge from the portfolio's actual
    # mode.
    trading_mode = await get_trading_mode()

    candidates, chain_map = await _momentum_candidates_and_chain_map(limit=20)
    analyzer = _default_momentum_analyzer(
        chain_map, weekly_context, current_regime=current_regime, relaxed=True,
        mode=trading_mode,
    )

    opened = 0
    for contract in candidates:
        if opened >= to_open:
            break
        # 27/07 -- 3-pocket architecture plan: this diagnostic floor always books
        # into "swing" (see the wallet="swing" comment on open_position() below),
        # so both checks here must be scoped to "swing" too -- left unscoped,
        # under gate ON a candidate legitimately already open in a DIFFERENT
        # pocket (scalping/vc) would (a) silently inflate this count-based cap
        # with positions this cycle never touches, and worse (b) make
        # has_open(contract) hit the multi-pocket ambiguity guard in _get_open
        # (RuntimeError) as soon as 2+ pockets happen to already hold the same
        # contract -- crashing this cycle for a candidate it hasn't even
        # evaluated yet. Gate OFF: byte-for-byte unchanged (only "swing" ever
        # holds a position, get_open_positions(wallet="swing") == get_open_positions()).
        if len(await get_open_positions(wallet="swing")) >= MAX_POSITIONS:
            break
        if await has_open(contract, wallet="swing"):
            continue
        try:
            sig = await analyzer(contract)
        except Exception as exc:  # noqa: BLE001 -- a crashing analysis never stops the floor
            logger.info("daily_floor: analysis %s failed (%s)", contract, exc)
            continue
        if not sig or sig.get("action") != "BUY" or not sig.get("floor_trade"):
            continue
        price = sig.get("price")
        if not price or price <= 0:
            continue
        # 25/07, operator request ("enleve le truc qui force les positions
        # avec 1% du capital"): the fixed FLOOR_TRADE_ALLOC_PCT sizing
        # mechanically capped the upside of even a genuinely strong floor
        # candidate -- now uses the SAME risk/ATR sizing formula as a normal
        # conviction pick (compute_entry_alloc), so a real signal gets a real
        # allocation. discovery_channel="floor" (below) still tags the trade
        # for /performance -- only the SIZE changes, never the diagnostic
        # labeling of where it came from.
        entry_alloc, conviction_tier = compute_entry_alloc(sig, start, weekly_context, risk_state)
        if await cash_available() < entry_alloc:
            continue
        pos = await open_position(
            contract,
            sig.get("symbol", ""),
            price,
            # 27/07 -- this diagnostic floor cycle is a wholly separate
            # mechanism (see its own module docstring) never touched by the
            # 3-pocket architecture plan -- always books into "swing" (the
            # single pocket this cycle has ever traded into), regardless of
            # ``multi_pocket_sourcing_enabled()``.
            wallet="swing",
            target_price=sig.get("target"),
            invalidation_price=sig.get("invalidation"),
            alloc_usd=entry_alloc,
            category=sig.get("category", ""),
            chain=sig.get("chain") or "base",
            thesis=("; ".join(sig.get("reasons") or []) or None),
            pool_liquidity_usd=sig.get("liquidity_usd"),
            entry_atr_pct=sig.get("entry_atr_pct"),
            strategy="momentum",
            entry_regime=sig.get("regime"),
            rr=sig.get("rr"),
            align_score=sig.get("align_score"),
            conviction_tier=conviction_tier or "floor",
            rvol_multiple=sig.get("rvol_multiple"),
            discovery_channel="floor",
            liquidity_rotation_score=sig.get("liquidity_rotation_score"),
            liquidity_rotation_accelerating=sig.get("liquidity_rotation_accelerating"),
            liquidity_rotation_volume_ratio=sig.get("liquidity_rotation_volume_ratio"),
        )
        if pos:
            opened += 1
            actions["opened"].append(pos)
            if notifier:
                try:
                    await notifier(format_buy_alert(pos))
                except Exception:  # noqa: BLE001
                    pass

    if opened == 0 and deficit > 0:
        actions["outcome"] = "no_safe_candidate"
    return actions


# ── Weekly training cycle (07/18, replaces the 30d/7d/14d protocol) ──────

async def get_current_cycle_number(wallet: str = "swing") -> int:
    await _ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT cycle_number FROM paper_state WHERE wallet = ?", (wallet,),
        ) as cur:
            row = await cur.fetchone()
    return int(row[0]) if row and row[0] is not None else 1


async def cycle_started_at(wallet: str = "swing") -> str:
    await _ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT created_at FROM paper_state WHERE wallet = ?", (wallet,),
        ) as cur:
            row = await cur.fetchone()
    return row[0] if row and row[0] else _now()


def weekly_target_equity(start_capital: float) -> float:
    return start_capital * WEEKLY_TARGET_MULTIPLIER


async def _effective_cycle_days(wallet: str = "swing") -> float:
    """25/07, operator one-off ("passe le test de 7 jours a 24h"):
    ``paper_state.cycle_duration_days`` overrides ``WEEKLY_CYCLE_DAYS`` for
    THIS cycle only when set (via ``reset_portfolio``) -- NULL (the default
    for any normal reset) falls back to the standard 7-day doctrine."""
    await _ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT cycle_duration_days FROM paper_state WHERE wallet = ?", (wallet,),
        ) as cur:
            row = await cur.fetchone()
    if row and row[0]:
        return float(row[0])
    return float(WEEKLY_CYCLE_DAYS)


async def weekly_cycle_due(wallet: str = "swing") -> bool:
    """True if the effective cycle duration (see ``_effective_cycle_days``,
    ``WEEKLY_CYCLE_DAYS`` by default) has elapsed since the start of the
    current cycle (``paper_state.created_at``). Never brought forward, even if
    the target is already reached -- a REPEATED training loop, not an exit
    gate crossed once."""
    started = await cycle_started_at(wallet=wallet)
    try:
        started_dt = datetime.fromisoformat(started)
    except ValueError:
        return False
    if started_dt.tzinfo is None:
        started_dt = started_dt.replace(tzinfo=timezone.utc)
    elapsed = datetime.now(timezone.utc) - started_dt
    cycle_days = await _effective_cycle_days(wallet=wallet)
    return elapsed.total_seconds() >= cycle_days * 86400


async def run_weekly_reset(*, price_lookup=None, wallet: str = "swing") -> dict:
    """Weekly cycle review + reset (explicit operator decision, 07/18) --
    fully replaces the 30d/7d/14d protocol as the TRAINING and DECISION method
    toward real capital: ARIA restarts at $1M EVERY week, +10% target ($1.1M)
    VALIDATED every week, whether the previous one succeeded or not.

    Unlike ``reset_portfolio`` (destructive by design, reserved for an
    explicit operator trigger), this function NEVER destroys history:
    1. evaluates each open position for the SATELLITE POCKET (07/22, Task 2,
       option 3 explicitly confirmed by the operator): a position whose
       potential is still intact (see ``_satellite_pocket_eligible`` --
       Euphoria ratchet regime, ATR stop not touched, solid REMAINING R/R) is
       PROMOTED to 'satellite' rather than force-closed, within the limit of a
       hard cap (``SATELLITE_POCKET_MAX_PCT_OF_CAPITAL``) -- priority to the
       best remaining R/R if several candidates compete for the spot, never
       an arbitrary order;
    2. force-closes mark-to-market (REAL price, never invented -- degrades to
       the entry cost if the price can't be found) EVERY OTHER still-open
       position (main pocket, or a satellite candidate rejected for lack of
       room) -- a week is judged on its own, EXCEPT the satellite pocket,
       which by construction lives on its own schedule;
    3. final snapshot (``portfolio_summary``) -> the ``validated`` verdict
       judges ONLY the MAIN pocket (``summary["cash"]``, never
       ``summary["equity"]`` which would include the floating valuation of
       the still-open satellite pocket --
       never a way to artificially postpone a weekly failure, nor to
       undeservedly dress up a weekly success);
    4. archives the week's history in ``paper_position_archive`` (never lost)
       then clears the live table -- EXCEPT still-open 'satellite' positions,
       which survive as-is into the following week (then managed by the
       normal cycle, on their own schedule, never re-closed here);
    5. records the verdict in ``paper_weekly_cycle`` (permanent track record,
       one row per week, never rewritten afterward except by this function
       itself);
    6. restarts fresh: $1M capital, timestamp, equity high-water mark,
       cycle_number+1;
    7. lifts the dedicated risk circuit breaker (``risk_guard``) -- fresh
       week, fresh discipline, never an old hard cap that would block the
       following week.

    Known limitation (v1, documented rather than hidden): ``risk_guard``'s
    drawdown circuit breaker reads ``portfolio_summary()`` (FULL equity,
    satellite pocket included) -- a satellite pocket losing value can
    therefore contribute to a drawdown trigger the following week, even
    though its result didn't count toward THE weekly verdict itself.
    Deliberately low cap (5% by default) to bound this impact; separating the
    two pockets in ``risk_guard`` would remain a distinct project if the need
    is confirmed under real conditions.

    ``wallet`` (27/07, 3-pocket architecture plan): scopes the ENTIRE weekly
    review to ONE pocket -- defaulted to 'swing' (the only pocket this
    training loop has ever run against). ``paper_position``/``paper_state``
    are now shared across all 3 pockets, so every query below is filtered by
    ``wallet`` -- a reset must never archive/close/reset a DIFFERENT pocket's
    positions or state.
    """
    await _ensure_tables()
    price_lookup = price_lookup or _default_price_lookup
    using_default_price_lookup = price_lookup is _default_price_lookup
    cycle_number = await get_current_cycle_number(wallet=wallet)
    started_at = await cycle_started_at(wallet=wallet)
    start_capital = await starting_capital(wallet=wallet)
    target_equity = weekly_target_equity(start_capital)

    from aria_core.skills import market_sentiment

    try:
        current_regime = await market_sentiment.resolve_meta_regime()
    except Exception as exc:  # noqa: BLE001 — never blocking, degrades to neutral
        logger.info("run_weekly_reset: meta-regime unavailable (%s) -- defaulting to neutral", exc)
        current_regime = market_sentiment.META_REGIME_NEUTRAL

    open_positions = await get_open_positions(wallet=wallet)
    existing_satellite = [p for p in open_positions if (p.get("pocket") or "main") == "satellite"]
    already_satellite_cost = sum(p["cost_usd"] for p in existing_satellite)
    satellite_room = max(
        0.0, SATELLITE_POCKET_MAX_PCT_OF_CAPITAL * STARTING_CAPITAL_USD - already_satellite_cost,
    )

    to_close: list[tuple[dict, float | None, str]] = []
    candidates: list[tuple[dict, float, str, float]] = []
    for pos in open_positions:
        if (pos.get("pocket") or "main") == "satellite":
            continue  # already satellite from a previous week -- never re-closed or re-evaluated here

        price = None
        price_source = "indisponible"
        try:
            if using_default_price_lookup:
                chain = pos.get("chain") or "base"
                pair = await _default_pair_lookup(pos["contract"], chain=chain)
                robust = await _robust_close_price(pos["contract"], chain, pair)
                if robust and robust > 0:
                    price = robust
                    price_source = "médiane bougies (anti-mèche, #173)"
                elif pair is not None and pair.price_usd and pair.price_usd > 0:
                    price = pair.price_usd
                    price_source = "spot (bougies indisponibles)"
            else:
                price = await price_lookup(pos["contract"])
                price_source = "de marché" if (price and price > 0) else "indisponible"
        except Exception:  # noqa: BLE001 — an unavailable price never blocks the reset
            price = None

        eligible, remaining_rr = _satellite_pocket_eligible(pos, price, current_regime)
        if eligible:
            candidates.append((pos, price, price_source, remaining_rr))
        else:
            to_close.append((pos, price, price_source))

    # Limited budget -- admits the BEST remaining R/R first (defensible,
    # never an arbitrary database order to break a tie under a hard cap).
    candidates.sort(key=lambda c: c[3], reverse=True)
    satellite_added: list[dict] = []
    satellite_rejected_no_room = 0
    for pos, price, price_source, remaining_rr in candidates:
        if pos["cost_usd"] <= satellite_room:
            await _set_position_pocket(pos["id"], "satellite")
            satellite_room -= pos["cost_usd"]
            satellite_added.append({
                "contract": pos["contract"], "symbol": pos.get("symbol"),
                "cost_usd": pos["cost_usd"], "remaining_rr": remaining_rr,
            })
        else:
            satellite_rejected_no_room += 1
            to_close.append((pos, price, price_source))

    force_closed: list[dict] = []
    for pos, price, price_source in to_close:
        exit_price = price if (price and price > 0) else pos["entry_price"]
        closed = await close_position(
            pos["contract"], exit_price,
            reason="reset_hebdomadaire",
            notes=(
                f"Clôture forcée -- fin du cycle #{cycle_number} ({_duration_phrase(pos.get('opened_at'))}), "
                f"prix {price_source if (price and price > 0) else 'indisponible, valorisé au coût d’entrée'}."
            ),
            position_id=pos["id"],
        )
        if closed:
            force_closed.append(closed)

    # 07/22 -- Task 2: total cost now locked in the satellite pocket
    # (carried-over + newly admitted this cycle) -- computed BEFORE the
    # snapshot, to neutralize its effect on the MAIN pocket's verdict (see below).
    satellite_reserved_usd = already_satellite_cost + sum(a["cost_usd"] for a in satellite_added)

    summary = await portfolio_summary(wallet=wallet)
    # The week's verdict judges ONLY the MAIN pocket. ``summary["cash"]``
    # subtracts the cost of ANY still-open position -- at this point, only the
    # satellite pocket (everything else was just force-closed above) -- so this
    # cost must be ADDED BACK to neutralize its effect: the satellite pocket
    # must neither help nor penalize THIS verdict, as if its capital had been
    # set aside before the week started rather than spent by it. ``open_value``
    # (the satellite pocket's floating valuation) NEVER enters this
    # computation. Identical to the old behavior when no satellite position
    # exists (cash == equity once everything is closed, satellite_reserved_usd
    # == 0) -- backward-compatible by construction.
    end_equity = summary["cash"] + satellite_reserved_usd
    return_pct = (end_equity / start_capital - 1.0) * 100.0 if start_capital else 0.0
    validated = end_equity >= target_equity
    ended_at = _now()

    async with aiosqlite.connect(DB_PATH) as db:
        cols = ", ".join(_POS_FIELDS)
        # Archives + clears the live table -- EXCEPT the satellite pocket
        # (position still OPEN by construction, managed on its own schedule,
        # never wiped here). Scoped to THIS wallet only -- paper_position is
        # now shared across all 3 pockets (27/07).
        await db.execute(
            f"INSERT INTO paper_position_archive (cycle_number, {cols}) "
            f"SELECT ?, {cols} FROM paper_position WHERE pocket != 'satellite' AND wallet = ?",
            (cycle_number, wallet),
        )
        await db.execute(
            "DELETE FROM paper_position WHERE pocket != 'satellite' AND wallet = ?", (wallet,),
        )
        # 27/07 -- 3-pocket architecture plan, Phase 4: cycle_number is scoped
        # per wallet now (migrated schema above) -- scalping adopting this
        # same weekly reset no longer collides with swing's own numbering.
        await db.execute(
            """
            INSERT INTO paper_weekly_cycle
              (wallet, cycle_number, started_at, ended_at, target_equity, start_capital,
               end_equity, return_pct, validated, closed_trades, win_rate)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(wallet, cycle_number) DO UPDATE SET
              ended_at = excluded.ended_at, target_equity = excluded.target_equity,
              start_capital = excluded.start_capital, end_equity = excluded.end_equity,
              return_pct = excluded.return_pct, validated = excluded.validated,
              closed_trades = excluded.closed_trades, win_rate = excluded.win_rate
            """,
            (wallet, cycle_number, started_at, ended_at, target_equity, start_capital,
             end_equity, return_pct, int(validated), summary["closed_trades"], summary["win_rate"]),
        )
        next_cycle = cycle_number + 1
        await db.execute(
            "UPDATE paper_state SET starting_capital = ?, created_at = ?, "
            "equity_high_water_mark = ?, cycle_number = ?, last_tracking_alert_at = NULL, "
            "cycle_duration_days = NULL WHERE wallet = ?",
            (STARTING_CAPITAL_USD, ended_at, STARTING_CAPITAL_USD, next_cycle, wallet),
        )
        await db.commit()

    # Fresh week, fresh discipline -- local import (risk_guard already imports
    # paper_trader, never the reverse at module level, see open_position above).
    from aria_core import risk_guard

    # 27/07 -- 3-pocket architecture plan, Phase 3: risk_guard's circuit
    # breaker is now per-pocket -- resumes ONLY the pocket THIS reset just
    # ran for (``wallet``, already threaded through this whole function),
    # never a hardcoded "swing" that would silently no-op for a future
    # pocket that also adopts this weekly reset (Phase 4).
    risk_guard.resume_new_entries(wallet, by="weekly_reset_auto")

    # 07/22 -- Task 2: full transparency on the satellite pocket, never a
    # silent mechanism (same doctrine as the rest of the project -- crossing a
    # guardrail or an exemption always stays visible in the report).
    # ``satellite_reserved_usd`` already computed above (added back into end_equity).
    return {
        "cycle_number": cycle_number,
        "started_at": started_at,
        "ended_at": ended_at,
        "start_capital": start_capital,
        "target_equity": target_equity,
        "end_equity": end_equity,
        "return_pct": return_pct,
        "validated": validated,
        "closed_trades": summary["closed_trades"],
        "win_rate": summary["win_rate"],
        "force_closed": len(force_closed),
        "next_cycle_number": next_cycle,
        "satellite_added_this_cycle": satellite_added,
        "satellite_open_positions": len(existing_satellite) + len(satellite_added),
        "satellite_reserved_usd": satellite_reserved_usd,
        "satellite_rejected_no_room": satellite_rejected_no_room,
    }


def format_weekly_cycle_report(report: dict, *, wallet: str = "swing") -> str:
    wr = report.get("win_rate")
    wr_str = f"{wr:.0f}%" if wr is not None else "n/a"
    verdict = "✅ VALIDÉ" if report["validated"] else "❌ non atteint"
    # 27/07 -- 3-pocket architecture plan, Phase 4: with scalping now also
    # calling this weekly reset, the pocket must be explicit in the report --
    # otherwise two consecutive Telegram messages (swing, then scalping)
    # would be indistinguishable.
    pocket_label = {"swing": "SWING", "scalping": "SCALPING"}.get(wallet, wallet.upper())
    lines = [
        f"🧪 SIMULATION — bilan hebdomadaire {pocket_label} (cycle d'entraînement 1M$)",
        f"Semaine #{report['cycle_number']} : {verdict} (objectif {report['target_equity']:,.0f} $)",
        f"Départ {report['start_capital']:,.0f} $ → clôture {report['end_equity']:,.0f} $ "
        f"({report['return_pct']:+.2f}%)",
        f"Trades clôturés {report['closed_trades']} · réussite {wr_str}",
    ]
    if report.get("force_closed"):
        lines.append(f"{report['force_closed']} position(s) encore ouverte(s) clôturée(s) au prix du marché.")
    # 07/22 -- Task 2: the satellite pocket (never wiped, never counted in the
    # verdict above) always stays visible in the report -- never a silent
    # mechanism.
    satellite_open = report.get("satellite_open_positions") or 0
    if satellite_open:
        added_this_cycle = len(report.get("satellite_added_this_cycle") or [])
        lines.append(
            f"🛰️ Poche satellite : {satellite_open} position(s) épargnée(s) du reset "
            f"({added_this_cycle} nouvelle(s) cette semaine, "
            f"{report.get('satellite_reserved_usd', 0.0):,.0f} $ réservés, hors verdict ci-dessus)."
        )
    if report.get("satellite_rejected_no_room"):
        lines.append(
            f"{report['satellite_rejected_no_room']} position(s) éligible(s) à la poche satellite "
            "mais refusée(s) faute de place (plafond atteint) -- clôturée(s) normalement."
        )
    lines.append(
        f"Nouvelle semaine #{report['next_cycle_number']} : capital principal remis à "
        f"{STARTING_CAPITAL_USD:,.0f} $, 0 position. Aucun argent réel."
    )
    return "\n".join(lines)


def compute_entry_alloc(
    sig: dict, start: float, weekly_context: dict | None, risk_state,
) -> tuple[float, str | None]:
    """Entry sizing for a BUY signal -- extracted (07/23, limit-order
    mechanism) from the inline block below so a limit-order trigger can
    recompute sizing with FRESH context (regime/risk_state/weekly may have
    moved since the order was placed) via the exact same formula as a direct
    buy. Zero behavior change from extraction -- same branching/order as
    before. Returns ``(entry_alloc_usd, conviction_tier)``."""
    from aria_core import risk_guard

    # 07/20 -- #174 (Formula B): a vc_thesis position provides ``taille_pct``
    # (rich LLM judgment, 0-10% of capital) but never ``rr``/``align_score``
    # (deterministic thresholds specific to momentum) -- checked FIRST, before
    # any conviction-stage computation, so this last one never silently
    # degrades to its MAX fallback (5% flat) for lack of a signal to read.
    vc_alloc_usd = risk_guard.vc_thesis_alloc_usd(sig.get("taille_pct"), start)
    if vc_alloc_usd is not None:
        base_alloc_usd = vc_alloc_usd
    else:
        risk_budget_pct = risk_guard.conviction_risk_budget_pct(
            sig.get("rr"), sig.get("align_score"), fundamental_score=sig.get("potential_score"),
            volume_confirmed=sig.get("volume_confirmed"),
        )
        conviction_mult = risk_guard.conviction_size_multiplier(
            sig.get("rr"), sig.get("align_score"), fundamental_score=sig.get("potential_score"),
            volume_confirmed=sig.get("volume_confirmed"),
        )
        entry_atr_pct = sig.get("entry_atr_pct")
        if risk_budget_pct is not None and entry_atr_pct:
            trail_pct = _effective_trail_pct(entry_atr_pct)
            base_alloc_usd = risk_guard.size_by_risk_budget(
                risk_budget_pct, trail_pct, start,
                ceiling_usd=conviction_mult * ALLOC_PCT * start,
            )
        else:
            base_alloc_usd = ALLOC_PCT * start * conviction_mult
    conviction_tier = risk_guard.conviction_tier_label(
        sig.get("rr"), sig.get("align_score"), fundamental_score=sig.get("potential_score"),
        volume_confirmed=sig.get("volume_confirmed"),
    )
    # 07/18 (continued, "handbrake" validated after review) -- once the
    # weekly target is already reached, halves NEW entries (never to zero):
    # protects the gain already made without ever blocking an exceptional,
    # doubly-verified setup. DETERMINISTIC rule (risk_guard), never entrusted
    # to the LLM. ``risk_state.alloc_multiplier`` (soft threshold #186) and
    # this risk/ATR sizing are two orthogonal dampeners (portfolio vs.
    # per-trade) -- always composed multiplicatively.
    pacing_mult = risk_guard.weekly_pacing_size_multiplier(weekly_context)
    # 07/20 -- Regime Switch: halves in confirmed Fear macro regime (preserves
    # capital when liquidity regroups on large assets) -- same composition
    # point as pacing_mult above, 1.0 by default (Neutral/Euphoria).
    regime_mult = risk_guard.regime_size_multiplier(sig.get("regime"))
    entry_alloc_usd = base_alloc_usd * risk_state.alloc_multiplier * pacing_mult * regime_mult
    return entry_alloc_usd, conviction_tier


async def _open_new_entries_for_wallet(
    wallet: str,
    candidates: list[str],
    analyzer,
    *,
    price_lookup,
    notifier,
    max_new: int,
    using_default_price_lookup: bool,
    closed_this_cycle: set[str],
    weekly_context: dict | None,
    risk_state,
    discovery_channel: str | None,
    trading_mode: str,
    max_positions_cap: int | None,
    funnel: dict[str, int],
) -> tuple[list[dict], int]:
    """Opens new positions for ONE pocket/wallet -- extracted (27/07, 3-pocket
    architecture plan, Phase 2) from ``_run_paper_cycle_locked``'s inline "2)
    Open new positions" block, UNCHANGED decision logic (loss-streak guard,
    re-entry conviction multiplier, freshness re-check / limit-order fallback,
    sizing via ``compute_entry_alloc``, bonding size reduction, funnel/
    counterfactual tracking, position opening, buy alert) -- a pure extraction,
    never a simplification, so the SAME loop can run once (gate OFF, today's
    single-wallet behavior) or 3 times independently (gate ON, one call per
    pocket -- never mixing candidates/analyzers across pockets).

    ``wallet``: every ``get_open_positions``/``has_open``/``open_position``
    call below is scoped to THIS pocket -- the same contract already open in
    a DIFFERENT pocket never blocks or counts against this one.

    ``max_positions_cap`` (``None`` = unlimited, same doctrine as today's
    scalping-mode bypass): the CALLER decides which cap applies to THIS
    wallet -- today's ``trading_mode``-driven ``MAX_POSITIONS``/unlimited
    choice for the single legacy "swing" pocket under gate OFF, or one of
    ``MAX_POSITIONS_VC``/``_SWING``/``_SCALPING`` per pocket under gate ON.
    Decoupled from ``trading_mode`` on purpose (it used to be derived from it
    inline) -- ``trading_mode`` here only still feeds the per-contract
    loss-streak threshold below (unchanged), never the position-count cap.

    ``funnel`` is mutated IN PLACE by the caller so that multiple calls
    (multi-pocket, gate ON) merge into ONE combined report/persistence,
    rather than 3 separate funnels -- the reason-code keys are shared, pocket
    attribution is preserved on each individual opened position via its own
    persisted ``wallet`` field instead.

    Returns ``(opened_positions, opened_count)``."""
    from aria_core import bonding_entry as _bonding_entry
    from aria_core import counterfactual_tracker
    from aria_core import limit_orders

    start = await starting_capital(wallet=wallet)
    opened_positions: list[dict] = []
    opened = 0
    for contract in candidates:
        if opened >= max_new:
            break
        # Item #101 (26/07), operator explicit decision: no position-count cap
        # in scalping mode -- "laisse libre, voyons comment ARIA trade sans la
        # force" -- observe the naturally-occurring behavior rather than
        # constraining it. Real cash availability (checked inside
        # open_position) remains the natural brake. Unchanged for "standard".
        if max_positions_cap is not None and len(await get_open_positions(wallet=wallet)) >= max_positions_cap:
            break
        if contract in closed_this_cycle:
            continue
        if await has_open(contract, wallet=wallet):
            continue
        try:
            sig = await analyzer(contract)
        except Exception as exc:  # noqa: BLE001 — a crashing analysis doesn't stop the cycle
            logger.info("paper_cycle: analysis %s failed (%s)", contract, exc)
            funnel["analyzer_error"] = funnel.get("analyzer_error", 0) + 1
            continue
        if not sig:
            funnel["no_price_data"] = funnel.get("no_price_data", 0) + 1
            continue
        if sig.get("action") != "BUY":
            reason_code = sig.get("hold_reason") or "unspecified"
            funnel[reason_code] = funnel.get(reason_code, 0) + 1
            # 07/20 -- #176 (learning track b): same choke point as the funnel
            # above (already THE only place that sees every HOLD, momentum
            # AND websocket -- momentum_websocket.py routes through this same
            # run_paper_cycle). Filter/gate already applied INSIDE
            # record_rejection (reasons with no useful counterfactual
            # discarded, never gated here -- passive logging, no network call).
            await counterfactual_tracker.record_rejection(
                contract, sig.get("chain") or "base", sig.get("symbol", ""),
                reason_code, sig.get("price"),
            )
            continue

        # 07/20 -- surgical guard BEFORE the informative re-entry note below:
        # beyond MAX_CONSECUTIVE_LOSSES_PER_CONTRACT consecutive losses on
        # THIS specific contract, the 07/19 relaxed re-entry is suspended for
        # it (never for another token, never risk_guard's global circuit breaker).
        # Item #101 (26/07): looser threshold in scalping mode -- see
        # SCALPING_MAX_CONSECUTIVE_LOSSES_PER_CONTRACT's comment. 27/07 -- this
        # threshold is intentionally NOT wallet-scoped (``_consecutive_losses_
        # for_contract`` reads the contract's history across ALL pockets) --
        # a repeated-failure pattern on a contract is a fact about the
        # CONTRACT, not about which pocket happened to hold it.
        loss_streak = await _consecutive_losses_for_contract(contract)
        loss_streak_threshold = (
            SCALPING_MAX_CONSECUTIVE_LOSSES_PER_CONTRACT if trading_mode == "scalping"
            else MAX_CONSECUTIVE_LOSSES_PER_CONTRACT
        )
        if loss_streak >= loss_streak_threshold:
            funnel["contract_loss_streak"] = funnel.get("contract_loss_streak", 0) + 1
            await counterfactual_tracker.record_rejection(
                contract, sig.get("chain") or "base", sig.get("symbol", ""),
                "contract_loss_streak", sig.get("price"),
            )
            continue

        # 07/24 -- direct operator observation (real AERO position, sell then
        # rebuy in quick succession, flagged as suspect): a fresh signal on a
        # contract just exited on "invalidation" must show meaningfully HIGHER
        # conviction (>= 2x the RR that was already invalidated), never just
        # match or barely beat it -- otherwise this is the same weak setup
        # re-entering on noise, not a genuinely new opportunity.
        last_invalidation_rr = await _last_invalidation_exit_rr(contract)
        if last_invalidation_rr is not None:
            new_rr = sig.get("rr")
            if new_rr is None or new_rr < last_invalidation_rr * REENTRY_INVALIDATION_CONVICTION_MULTIPLIER:
                funnel["invalidation_reentry_insufficient_conviction"] = (
                    funnel.get("invalidation_reentry_insufficient_conviction", 0) + 1
                )
                await counterfactual_tracker.record_rejection(
                    contract, sig.get("chain") or "base", sig.get("symbol", ""),
                    "invalidation_reentry_insufficient_conviction", sig.get("price"),
                )
                continue

        # 07/19 -- relaxed (explicit operator decision, see comment on the old
        # REENTRY_RR_MIN above): a contract already closed becomes a
        # candidate like any other as soon as a new BUY signal comes up -- no
        # extra bar. Informative note only (thesis traceability), never a filter.
        if await _has_prior_close(contract):
            sig.setdefault("reasons", []).append(
                "re-entrée -- ce contrat a déjà eu une position clôturée précédemment"
            )

        price = sig.get("price")
        if not price:
            try:
                if using_default_price_lookup:
                    price = await price_lookup(contract, chain=sig.get("chain") or "base")
                else:
                    price = await price_lookup(contract)
            except Exception:  # noqa: BLE001
                price = None
        if not price or price <= 0:
            continue
        # 07/18 -- explicit operator decision ("more aggressive" = bigger on
        # the BEST setups, not bigger everywhere). 07/19 -- potential_score
        # (conviction_research.py): None if fundamental diligence found
        # nothing/is disabled -- fail-open on unknown, never blocks the
        # technical bonus alone. volume_confirmed
        # (momentum_entry._check_volume_confirmation, Gemini cross-review):
        # False -> conviction penalty, None/True -> no effect.
        #
        # 07/20 -- HYBRID risk-target/ATR sizing (Gemini cross-review round
        # 7): when ``entry_atr_pct`` is known, the conviction stage's risk
        # budget (``conviction_risk_budget_pct``) is divided by the REAL width
        # of the trailing stop for THIS token (same ``_effective_trail_pct``
        # function as position management -- never a separately recomputed
        # width, which could diverge). Falls back to the old fixed-stage
        # system (``conviction_size_multiplier``) if ``entry_atr_pct`` is
        # unknown (analyzer that doesn't provide it, e.g. the old dormant
        # VC-thesis pilot) -- never a risk budget computed on an invented
        # stop width.
        #
        # 07/20 (continued, real bug found while answering an operator
        # question about market-cap proportionality): the cap must NOT be the
        # absolute maximum (5%) for ALL stages -- a shared ceiling let a
        # MODERATE or WEAK signal on a tight stop reach the same stake as a
        # STRONG signal (as soon as the stop falls below ~20%/10%
        # respectively), reversing the very intent of the conviction stages.
        # EACH stage's cap must stay the one from the old fixed-stage system
        # (5%/3.5%/2%) -- ``conviction_mult`` computed once below and reused
        # for BOTH paths (risk/ATR sizing cap, AND the fallback's direct
        # multiplier) guarantees the new system can never exceed what the old
        # one would have given for this SAME stage -- only reduce below it,
        # never level it up.
        # 07/23 -- sizing extracted to ``compute_entry_alloc`` (limit-order
        # mechanism, see below) -- same formula/thresholds as before
        # extraction, reused as-is by a limit-order trigger with fresh
        # context.
        entry_alloc_usd, conviction_tier = compute_entry_alloc(sig, start, weekly_context, risk_state)
        # 24/07, bonding-entry chantier: extra reduction on top of the
        # standard risk/ATR sizing -- structurally higher risk on this path
        # (no honeypot-class check exists for a bonding-curve token, see
        # bonding_entry.py's own docstring), operator-requested caution.
        if sig.get("chain") == _bonding_entry.CHAIN_MARKER:
            entry_alloc_usd *= _bonding_entry.BONDING_SIZE_REDUCTION

        # 07/20 -- freshness re-check right before execution (Gemini
        # cross-review, see _fresh_rr/_execution_rr_still_valid above):
        # ``price`` above was captured at the very start of the evaluation
        # (before honeypot/holder concentration/OHLCV cascade/up to 2
        # sequential LLM calls) -- on a volatile token, several seconds may
        # have passed. R/R is recomputed at the REAL price rather than
        # rejecting on a simple % move (root cause detailed in _fresh_rr's
        # comment) -- a setup still good at the fresh price executes, a
        # degraded setup passes to the next round (never forced on stale data
        # or an R/R that no longer holds).
        try:
            if using_default_price_lookup:
                fresh_price = await price_lookup(contract, chain=sig.get("chain") or "base")
            else:
                fresh_price = await price_lookup(contract)
        except Exception:  # noqa: BLE001 — a network failure must never crash the cycle
            fresh_price = None
        fresh_rr = _fresh_rr(fresh_price, sig.get("target"), sig.get("invalidation"))
        if not _execution_rr_still_valid(sig.get("rr"), fresh_rr):
            funnel["price_stale_at_execution"] = funnel.get("price_stale_at_execution", 0) + 1
            # 07/23 -- limit-order mechanism: a plain reject here silently
            # drops a setup that only got MORE EXPENSIVE since the signal was
            # detected (price drifted upward during honeypot/OHLCV/LLM
            # analysis), not a DEAD one -- the exact CHECK case (0.038 signal
            # price -> 0.044 execution price). ``should_place_limit_order``
            # draws the line explicitly: a structure already broken (price
            # through the invalidation) is still rejected outright below,
            # never turned into a limit order on a dead setup.
            if limit_orders.should_place_limit_order(price, fresh_price, sig.get("invalidation")):
                try:
                    # 27/07 -- 3-pocket architecture plan: scoped to THIS pocket,
                    # same reasoning as has_open/open_position above -- a pending
                    # limit order already placed by a DIFFERENT pocket on the same
                    # contract must never block this one, and a triggered order
                    # must remember (and later execute into) the SAME pocket that
                    # detected it. wallet="swing" implicitly under gate OFF (this
                    # function is only ever called with wallet="swing" there).
                    if not await limit_orders.has_active_order(contract, sig.get("chain") or "base", wallet=wallet):
                        order = await limit_orders.create_pending_order(
                            contract, sig.get("chain") or "base", sig.get("symbol", ""), price, sig,
                            wallet=wallet,
                        )
                        if notifier:
                            await notifier(limit_orders.format_limit_order_placed_alert(order))
                except Exception as exc:  # noqa: BLE001 -- never breaks the cycle
                    logger.info("paper_cycle: could not place limit order for %s (%s)", contract, exc)
            continue
        # ``fresh_price`` is guaranteed valid here in real operation
        # (``_fresh_rr`` returns None on a missing/invalid price, so
        # ``_execution_rr_still_valid`` would already have fail-closed above)
        # -- this guard only protects against an explicitly neutralized
        # ``_execution_rr_still_valid`` (tests dedicated to sizing, unrelated
        # to this specific guard), never reached in production.
        if fresh_price and fresh_price > 0:
            price = fresh_price

        pos = await open_position(
            contract,
            sig.get("symbol", ""),
            price,
            wallet=wallet,
            target_price=sig.get("target"),
            invalidation_price=sig.get("invalidation"),
            alloc_usd=entry_alloc_usd,
            category=sig.get("category", ""),
            entry_security_json=sig.get("entry_security_json", ""),
            chain=sig.get("chain") or "base",
            # bug found on 07/17: ``sig.get("these")`` alone only covered the
            # old VC-thesis analyzer (_default_analyzer, "these" key) -- the
            # momentum analyzer (#194, evaluate_momentum_entry) builds a real
            # "reasons" list (golden pocket/RSI setup, technical alignment,
            # R/R) but never sets "these", so `thesis` silently stayed None on
            # every momentum trade.
            thesis=sig.get("these") or "; ".join(sig.get("reasons") or []) or None,
            pool_liquidity_usd=sig.get("liquidity_usd"),
            entry_atr_pct=sig.get("entry_atr_pct"),
            # 07/20 -- Formula B: the exit discipline applied depends on the
            # real ENTRY pipeline (see comment on VC_MIN_LIQUIDITY_FLOOR_USD),
            # never an independent flag. "momentum" by default -- unchanged
            # behavior for any analyzer that doesn't provide this field.
            strategy=sig.get("strategy") or "momentum",
            # 07/20 -- Regime Switch: macro regime at entry, locked for the
            # life of the position (ratcheted in management, see below).
            entry_regime=sig.get("regime"),
            # 07/22 -- task #4: snapshot of the deployer wallet at entry --
            # None for any analyzer that doesn't provide it (e.g. momentum,
            # which has no such concept), never an invented value.
            entry_dev_sold_pct=sig.get("dev_sold_pct"),
            # 07/23 -- performance-breakdown tracking (operator request):
            # purely observational, never used to size or gate this position.
            rr=sig.get("rr"),
            align_score=sig.get("align_score"),
            conviction_tier=conviction_tier,
            rvol_multiple=sig.get("rvol_multiple"),
            discovery_channel=discovery_channel,
            conviction_process_trail=sig.get("conviction_process_trail"),
            conviction_website_corroborated=sig.get("conviction_website_corroborated"),
            conviction_posting_cadence=sig.get("conviction_posting_cadence"),
            liquidity_rotation_score=sig.get("liquidity_rotation_score"),
            liquidity_rotation_accelerating=sig.get("liquidity_rotation_accelerating"),
            liquidity_rotation_volume_ratio=sig.get("liquidity_rotation_volume_ratio"),
            # Item #101 (26/07): the entry mode the signal was sourced under
            # ("standard"/"scalping") -- persisted on the position and used
            # below to keep MAX_POSITIONS defense-in-depth check consistent
            # with the cycle-level bypass above.
            mode=sig.get("mode") or "standard",
            # Item #101 (26/07): golden pocket bounds -- see open_position's docstring.
            gp_low=sig.get("gp_low"),
            gp_high=sig.get("gp_high"),
        )
        if pos:
            opened += 1
            opened_positions.append(pos)
            if notifier:
                try:
                    await notifier(format_buy_alert(pos))
                except Exception:  # noqa: BLE001
                    pass

    return opened_positions, opened


async def run_paper_cycle(
    *,
    candidates=None,
    analyzer=None,
    price_lookup=None,
    notifier=None,
    max_new: int = 3,
    depeg_check=None,
    skip_position_management: bool = False,
    skip_new_entries: bool = False,
    discovery_channel: str | None = None,
) -> dict:
    """One simulation round, applying the REAL reports:
      1. open positions: continuous safety monitoring (#187) then management
         via trailing stop + staged profit-taking (see
         ``TRAIL_STOP_PCT``/``TP_STAGES``/``_effective_tp_stages`` -- TP1
         anchored on the position's technical target when known, TP2/TP3
         fixed above for the moonbag) — protects gains already made without
         cutting off remaining potential, instead of a binary 100% target OR
         100% invalidation exit;
      2. new buys: on ranked candidates with a real BUY signal (blocked if
         USDC is depegged, #187), opens a fictitious position and issues a
         fictitious buy alert.
    Everything is injectable (candidates/analyzer/price_lookup/notifier/depeg_check)
    -> testable offline, no hidden network call.
    No real execution, never an order: simulation only.

    ``skip_position_management`` (#196, default ``False`` -- unchanged
    historical behavior): skips step 1 (safety re-scan + trailing stop/TP on
    already-open positions) -- reserved for the momentum websocket service,
    triggered much more often (~30s) than the normal heartbeat cycle (15 min),
    so as not to re-scan GoPlus/Blockscout on every open position on every
    push. Step 1ter (portfolio risk snapshot, #186) is ALWAYS still executed
    -- step 2 (new entries) depends on it (cap/circuit breaker), regardless of
    the caller.

    ``skip_new_entries`` (07/22, default ``False`` -- unchanged historical
    behavior): the opposite -- skips step 2 (searching for new candidates to
    buy), keeps only step 1 (monitoring already-open positions). Explicit
    operator decision (07/22): decouple the DISCOVERY cadence (slowed to 1h,
    the #196 WebSocket already covers fast continuous detection) from the
    MONITORING cadence of already-open positions (stays at 15 min -- this is
    what protects against a worsening loss between two passes, never slowed
    without a separate explicit decision). The classic heartbeat cycle
    (``paper_trade_cycle``) now passes ``skip_new_entries=True``; a new
    dedicated cycle (``momentum_discovery_cycle``, 60min) passes
    ``skip_position_management=True`` for the opposite -- the two flags are
    never both true at the same time by the same caller (otherwise the cycle
    would do nothing).

    Every execution goes through ``_run_cycle_lock`` (#196) -- never two
    cycles in parallel (heartbeat + websocket + hourly discovery), which
    would otherwise read the capital/number of open positions before either
    one writes (possible double-allocation).

    ``discovery_channel`` (07/23, performance-breakdown tracking): "websocket"
    or "scan", set by the CALLER (neither analyzer knows on its own where it
    was invoked from) -- persisted as-is on any position opened during this
    cycle, purely observational, never influences the decision itself.
    ``None`` by default -- unchanged behavior for any caller that doesn't
    provide it.
    """
    async with _run_cycle_lock:
        return await _run_paper_cycle_locked(
            candidates=candidates,
            analyzer=analyzer,
            price_lookup=price_lookup,
            notifier=notifier,
            max_new=max_new,
            depeg_check=depeg_check,
            skip_position_management=skip_position_management,
            skip_new_entries=skip_new_entries,
            discovery_channel=discovery_channel,
        )


def _refresh_tracked_after_partial(tracked: list[dict], contract: str, partial: dict) -> None:
    """27/07, robustness suggestion from an independent Grok review of the
    stale-``tracked``-snapshot fix above: looks up the entry by ``contract``
    instead of assuming it's ``tracked[-1]`` -- verified harmless today (the
    management loop below never sorts/filters ``tracked`` between its own
    ``append`` and this call, so ``[-1]`` was never actually wrong), but an
    explicit lookup can't silently break if a future refactor ever
    introduces such a reorder. Only the LAST matching entry is updated
    (``reversed()``) -- same reasoning as ``[-1]`` before: the position
    currently being managed is always the most recently appended one for
    this contract."""
    for t in reversed(tracked):
        if t["contract"] == contract:
            t["qty"] = partial["remaining_qty"]
            t["cost_usd"] = partial["remaining_cost_usd"]
            return


async def _run_paper_cycle_locked(
    *,
    candidates=None,
    analyzer=None,
    price_lookup=None,
    notifier=None,
    max_new: int = 3,
    depeg_check=None,
    skip_position_management: bool = False,
    skip_new_entries: bool = False,
    discovery_channel: str | None = None,
) -> dict:
    """Real body of ``run_paper_cycle`` -- called ONLY under
    ``_run_cycle_lock``, never directly (no concurrency guardrail otherwise)."""
    await _ensure_tables()
    price_lookup = price_lookup or _default_price_lookup
    # #194 -- the default knows how to follow a position's persisted chain
    # (multi-chain); any INJECTED price_lookup (tests, or the momentum
    # pipeline which supplies its own via a closure) keeps its historical
    # single-argument call contract.
    using_default_price_lookup = price_lookup is _default_price_lookup
    actions: dict = {"opened": [], "closed": [], "partial": [], "checked": 0, "tracked": []}
    # #197 (07/15) -- periodic tracking: one entry per position still open at
    # the end of the cycle (current price already fetched below, no extra
    # network call).
    tracked: list[dict] = []

    # 07/20 -- dynamic Regime Switch: meta-regime resolved ONCE per cycle
    # (pure local DB read, ``market_sentiment.resolve_meta_regime()``, zero
    # network call) -- reused both by the management of already-open
    # positions below (ratchet toward the more cautious regime) and by the
    # sourcing of new entries further down (``_default_momentum_analyzer``).
    # Import hoisted OUT of the try (not just the call) so that
    # ``market_sentiment`` always stays bound in this scope, even if the
    # resolution itself fails -- later uses of
    # ``market_sentiment.more_cautious_meta_regime``/``META_REGIME_NEUTRAL``
    # then never depend on the success path. Best-effort, never blocking: a
    # failure degrades to "neutral" (unchanged historical behavior).
    from aria_core.skills import market_sentiment

    try:
        current_regime = await market_sentiment.resolve_meta_regime()
    except Exception as exc:  # noqa: BLE001 — never blocking, degrades to "neutral"
        logger.info("paper_cycle: meta-regime unavailable (%s) -- defaulting to neutral", exc)
        current_regime = market_sentiment.META_REGIME_NEUTRAL

    # Item #101 (26/07) -- resolved ONCE per cycle, same pattern as
    # current_regime above: a portfolio-wide switch (operator-only, via
    # set_trading_mode), never per-candidate.
    trading_mode = await get_trading_mode()

    # Item #105 (26/07): daily profit target check, resolved ONCE per cycle --
    # used by the scalping-mode bearish RSI divergence exit signal further
    # below (secure the gain if the target isn't reached yet, let normal
    # management govern if it already is). Reuses the SAME target as the
    # diagnostic daily-trade-floor cycle (DAILY_FLOOR_TARGET_PROFIT_USD, the
    # operator's real $50-100k/24h target), never a second diverging number.
    # Approximation acknowledged: equity here is cash + cost basis of open
    # positions (no mark-to-market price_lookup, to avoid an extra network
    # call per position just for this check) -- good enough to judge whether
    # the target is "roughly already banked", not a precise dollar figure.
    daily_target_reached = False
    if trading_mode == "scalping":
        try:
            _target_summary = await portfolio_summary()
            _target_start = await starting_capital()
            daily_target_reached = (
                (_target_summary.get("equity") or 0.0) >= _target_start + DAILY_FLOOR_TARGET_PROFIT_USD
            )
        except Exception as exc:  # noqa: BLE001 -- never blocking, degrades to "not reached" (safer default: keeps securing gains)
            logger.info("paper_cycle: daily target check unavailable (%s) -- defaulting to not-reached", exc)
            daily_target_reached = False

    # 1) Manage open positions: first a continuous SAFETY monitoring
    #    (#187 -- honeypot/ownership that appeared after entry, never checked
    #    more than once before), which takes priority over any price-based
    #    management; then trailing stop (never relaxes) and staged
    #    profit-taking on whatever remains open.
    #    #196 -- skipped if ``skip_position_management`` (momentum websocket
    #    service, triggered much more often than the normal heartbeat cycle):
    #    doesn't re-scan GoPlus/Blockscout on every open position on every
    #    candidate push.
    from aria_core import paper_trader_risk as risk

    if not skip_position_management:
        for p in await get_open_positions():
            actions["checked"] += 1
            # 07/17 -- with the DEFAULT price_lookup, the DexScreener pair is
            # fetched ONCE and reused for both the price and the
            # volume/liquidity ratio re-scan below (never a second duplicated
            # network call). An INJECTED price_lookup (tests, momentum
            # pipeline) doesn't provide this pair -- the ratio check is then
            # simply skipped (honest degradation, see
            # paper_trader_risk.rescan_open_position).
            pair = None
            try:
                if using_default_price_lookup:
                    pair = await _default_pair_lookup(p["contract"], chain=p.get("chain") or "base")
                    price = pair.price_usd if pair and pair.price_usd and pair.price_usd > 0 else None
                else:
                    price = await price_lookup(p["contract"])
            except Exception:  # noqa: BLE001
                price = None

            try:
                security_flag = await risk.rescan_open_position(p, pair=pair)
            except Exception as exc:  # noqa: BLE001 — monitoring must never break the cycle
                logger.info("paper_cycle: safety re-scan %s failed (%s)", p["contract"], exc)
                security_flag = None
            if security_flag:
                # Paper position -> automatic close with no risk, this tests the
                # REACTION. With REAL capital this would become an ALERT only
                # (wallet_guard doctrine -- never an automatic sell without
                # operator confirmation), see paper_trader_risk.py.
                exit_price = price if (price and price > 0) else p["entry_price"]
                sec_notes = (
                    f"Re-scan sécurité déclenché en cours de détention ({_duration_phrase(p.get('opened_at'))}) : "
                    + "; ".join(security_flag["reasons"])
                    + " -- fermeture immédiate (position fictive, teste la réaction)."
                )
                closed = await close_position(
                    p["contract"], exit_price, reason="sécurité re-scan", notes=sec_notes,
                    position_id=p["id"],
                )
                if closed:
                    actions["closed"].append(closed)
                    actions.setdefault("security_alerts", []).append(security_flag)
                    if notifier:
                        try:
                            alert = format_sell_alert(closed) + "\n⚠️ " + "; ".join(security_flag["reasons"])
                            await notifier(alert)
                        except Exception:  # noqa: BLE001
                            pass
                continue

            if not price or price <= 0:
                continue

            # #197 -- provisional: removed below if the position closes
            # (fully) in this same round, to never duplicate with format_sell_alert.
            tracked.append({
                "contract": p["contract"], "symbol": p["symbol"], "entry_price": p["entry_price"],
                "qty": p["qty"], "cost_usd": p["cost_usd"], "price": price, "chain": p.get("chain") or "base",
                "mode": p.get("mode"), "strategy": p.get("strategy"),
            })

            # Item #105 (26/07): scalping-mode exit signal -- a confirmed
            # bearish RSI divergence (mirror of the entry-side signal, RSI
            # [60,80] on the recent pivot) closes the position OUTRIGHT if the
            # portfolio hasn't yet reached its daily profit target; if the
            # target is already reached, normal management (trailing stop/TP
            # stages below) keeps governing unchanged -- never a second exit
            # path stacked on top. Costs one extra candle fetch per scalping
            # position per cycle, same scalping ladder as entry (shared
            # GeckoTerminal throttle already coordinated). Scoped strictly to
            # "scalping" -- the already-running standard/swing path never
            # takes this branch.
            if p.get("mode") == "scalping" and pair is not None and pair.pair_address:
                from aria_core import momentum_entry
                from aria_core.skills.entry_signals import bearish_rsi_divergence

                try:
                    exit_candles = await momentum_entry._fetch_candles(
                        pair.pair_address, p.get("chain") or "base",
                        contract=p["contract"], pair=pair, mode="scalping",
                    )
                except Exception:  # noqa: BLE001 -- never blocks position management
                    exit_candles = []
                bearish = False
                bearish_reason = ""
                if exit_candles:
                    bearish, bearish_reason = bearish_rsi_divergence(exit_candles)
                if bearish and not daily_target_reached:
                    exit_gain_pct = (price / p["entry_price"] - 1.0) * 100.0 if p["entry_price"] else 0.0
                    exit_notes = (
                        f"Divergence RSI baissière confirmée ({bearish_reason}) -- objectif de "
                        f"profit 24h pas encore atteint -- sécurisation immédiate "
                        f"({exit_gain_pct:+.1f}% vs entrée), {_duration_phrase(p.get('opened_at'))}."
                    )
                    closed = await close_position(
                        p["contract"], price,
                        reason="divergence RSI baissière (scalping)", notes=exit_notes,
                        position_id=p["id"],
                    )
                    if closed:
                        actions["closed"].append(closed)
                        if notifier:
                            try:
                                await notifier(format_sell_alert(closed))
                            except Exception:  # noqa: BLE001
                                pass
                    continue

            # 07/20 -- Formula B (VC exit discipline, see
            # VC_MIN_LIQUIDITY_FLOOR_USD/VC_LIQUIDITY_DROP_INVALIDATION_PCT/
            # VC_TAKE_SEED_MULTIPLE above) -- ENTIRELY SEPARATE branch from the
            # momentum management below (ATR trailing stop + staged TP), never
            # reached for "strategy" == "momentum" (default, unchanged
            # historical behavior).
            if (p.get("strategy") or "momentum") == "vc_thesis":
                entry_price = p["entry_price"]
                entry_liq = p.get("entry_liquidity_usd")
                last_liq = p.get("last_liquidity_usd")
                current_liq = pair.liquidity_usd if pair is not None else None

                # 07/22 -- task #4: updates the last-observed value BEFORE any
                # check that might close the position this cycle -- best-effort,
                # never blocking (a write failure never breaks position management).
                if current_liq is not None:
                    try:
                        await _update_vc_liquidity_watermark(p["id"], current_liq)
                    except Exception:  # noqa: BLE001
                        pass

                # 07/22 -- task #4, emergency SELL signal #1 (post-entry
                # monitoring, explicit operator decision): the deployer wallet
                # resells a significant share of its allocation DURING the
                # holding period -- until now, dev_wallet.py was only
                # consulted ONCE, at entry. Costs 2 Blockscout calls per cycle
                # per open vc_thesis position (well within the calibrated
                # margin, see docs/api-rate-limit-calibration.md) -- no
                # consequence today, the VC pocket staying at 0% (07/15
                # decision unchanged).
                dev_sold_triggered, dev_sold_reason = await _check_vc_dev_wallet_recent_selling(
                    p["contract"], p.get("chain") or "base", p.get("entry_dev_sold_pct"),
                )
                if dev_sold_triggered:
                    exit_gain_pct = (price / entry_price - 1.0) * 100.0 if entry_price else 0.0
                    exit_notes = (
                        f"Signal SELL d'urgence (surveillance post-entrée VC) : {dev_sold_reason} "
                        f"-- sortie complète ({exit_gain_pct:+.1f}% vs entrée), "
                        f"{_duration_phrase(p.get('opened_at'))}."
                    )
                    closed = await close_position(
                        p["contract"], price, reason="vente déployeur détectée", notes=exit_notes,
                        position_id=p["id"],
                    )
                    if closed:
                        actions["closed"].append(closed)
                        if notifier:
                            try:
                                await notifier(format_sell_alert(closed))
                            except Exception:  # noqa: BLE001
                                pass
                    continue

                liquidity_invalidated = False
                liq_reason = ""
                if current_liq is not None:
                    if current_liq < VC_MIN_LIQUIDITY_FLOOR_USD:
                        liquidity_invalidated = True
                        liq_reason = (
                            f"liquidité tombée sous le plancher absolu "
                            f"({current_liq:,.0f}$ < {VC_MIN_LIQUIDITY_FLOOR_USD:,.0f}$)"
                        )
                    elif (
                        entry_liq and entry_liq > 0
                        and current_liq < entry_liq * VC_LIQUIDITY_DROP_INVALIDATION_PCT
                    ):
                        liquidity_invalidated = True
                        drop_pct = (1 - current_liq / entry_liq) * 100.0
                        liq_reason = (
                            f"liquidité en chute de {drop_pct:.0f}% depuis l'entrée "
                            f"({entry_liq:,.0f}$ -> {current_liq:,.0f}$)"
                        )
                    # 07/22 -- task #4, emergency SELL signal #2: SUDDEN drop
                    # between two consecutive cycles (30%) -- complements,
                    # without ever replacing, the cumulative-since-entry check
                    # above (50%): an LP withdrawal spread over small tranches
                    # across several weeks might never cross the cumulative
                    # threshold at any point T, yet still represent a real
                    # withdrawal in progress -- detected here cycle by cycle
                    # rather than cumulatively since entry.
                    elif (
                        last_liq and last_liq > 0
                        and current_liq < last_liq * (1 - VC_LIQUIDITY_SUDDEN_DROP_PCT)
                    ):
                        liquidity_invalidated = True
                        sudden_drop_pct = (1 - current_liq / last_liq) * 100.0
                        liq_reason = (
                            f"chute SOUDAINE de liquidité entre deux cycles "
                            f"({sudden_drop_pct:.0f}%, {last_liq:,.0f}$ -> {current_liq:,.0f}$) "
                            "-- retrait de LP en formation"
                        )

                if liquidity_invalidated:
                    exit_gain_pct = (price / entry_price - 1.0) * 100.0 if entry_price else 0.0
                    exit_notes = (
                        f"Invalidation fondamentale VC : {liq_reason} -- thèse invalidée "
                        f"({exit_gain_pct:+.1f}% vs entrée), sortie complète, "
                        f"{_duration_phrase(p.get('opened_at'))}."
                    )
                    closed = await close_position(
                        p["contract"], price, reason="invalidation fondamentale (liquidité)",
                        notes=exit_notes, position_id=p["id"],
                    )
                    if closed:
                        actions["closed"].append(closed)
                        if notifier:
                            try:
                                await notifier(format_sell_alert(closed))
                            except Exception:  # noqa: BLE001
                                pass
                    continue

                target = p.get("target_price")
                if target and price >= target:
                    exit_gain_pct = (price / entry_price - 1.0) * 100.0 if entry_price else 0.0
                    exit_notes = (
                        f"Cible complète de la thèse VC atteinte ({price:.6g} >= {target:.6g}, "
                        f"{exit_gain_pct:+.1f}% vs entrée) -- clôture complète, "
                        f"{_duration_phrase(p.get('opened_at'))}."
                    )
                    closed = await close_position(
                        p["contract"], price, reason="cible thèse VC", notes=exit_notes,
                        position_id=p["id"],
                    )
                    if closed:
                        actions["closed"].append(closed)
                        if notifier:
                            try:
                                await notifier(format_sell_alert(closed))
                            except Exception:  # noqa: BLE001
                                pass
                    continue

                # "Take Seed" -- A SINGLE partial exit, as soon as the position
                # doubles, recovers EXACTLY the initial stake (``cost_usd``).
                # ``tp_stage_hit`` reused as a plain boolean marker (0/1) --
                # this branch never joins the momentum staging loop below, no
                # risk of semantic collision.
                already_seeded = bool(p.get("tp_stage_hit"))
                gain_mult = (price / entry_price) if entry_price else 0.0
                if not already_seeded and gain_mult >= VC_TAKE_SEED_MULTIPLE:
                    cost_usd = p["cost_usd"]
                    sell_qty = min(cost_usd / price, p["qty"]) if price > 0 else 0.0
                    if sell_qty > 0:
                        seed_notes = (
                            f"Take Seed : position à {gain_mult:.1f}x l'entrée -- vente de "
                            f"{sell_qty:.6g} (récupère la mise initiale {cost_usd:,.0f}$), "
                            f"reste couru sans stop vers la cible complète de la thèse."
                        )
                        partial = await reduce_position(
                            p["contract"], price, sell_qty, stage=1,
                            reason="take seed 2x", notes=seed_notes, position_id=p["id"],
                        )
                        if partial:
                            actions["partial"].append(partial)
                            # 27/07, real bug found (operator screenshot): this
                            # position's entry in ``tracked`` was appended
                            # BEFORE this reduction, so it still holds the
                            # pre-reduction qty/cost_usd -- the periodic
                            # tracking alert built later this same cycle would
                            # otherwise display a stale capital figure for one
                            # cycle (e.g. the full pre-Take-Seed cost).
                            _refresh_tracked_after_partial(tracked, p["contract"], partial)
                            if notifier:
                                try:
                                    await notifier(format_partial_exit_alert(partial))
                                except Exception:  # noqa: BLE001
                                    pass
                continue

            trail_pct = _effective_trail_pct(p.get("entry_atr_pct"))
            prev_high_water = p.get("high_water_price") or p["entry_price"]
            prev_pending = p.get("pending_high_water")
            prev_pending_since = p.get("pending_high_water_since")
            high_water, pending_hw, pending_since = _advance_high_water(
                prev_high_water, prev_pending, prev_pending_since, price, datetime.now(timezone.utc),
            )
            if (
                high_water != prev_high_water
                or pending_hw != prev_pending
                or pending_since != prev_pending_since
            ):
                await _update_high_water(p["id"], high_water, pending_hw, pending_since)

            # 07/20 -- Breakeven Hard Floor, time confirmation (see
            # _advance_breakeven_pending above -- fixes the asymmetry flagged
            # by an external cross-review: locking on an instantaneous
            # reading, without the confirmation the high_water ratchet
            # already applies).
            entry_price = p["entry_price"]
            flash_threshold = _breakeven_floor_threshold(p.get("target_price"), entry_price)
            breakeven_locked = bool(p.get("breakeven_locked"))
            if not breakeven_locked and entry_price and flash_threshold is not None:
                prev_be_pending = p.get("breakeven_pending_since")
                new_be_pending, be_confirmed = _advance_breakeven_pending(
                    prev_be_pending, price, entry_price, flash_threshold, datetime.now(timezone.utc),
                )
                if be_confirmed:
                    breakeven_locked = True
                    await _lock_breakeven_floor(p["id"])
                elif new_be_pending != prev_be_pending:
                    await _update_breakeven_pending(p["id"], new_be_pending)

            invalidation = p.get("invalidation_price")
            active_stop, stop_source = _compute_active_stop(
                entry_price=entry_price, entry_atr_pct=p.get("entry_atr_pct"),
                high_water_price=high_water, invalidation_price=invalidation,
                breakeven_locked=breakeven_locked,
            )

            if active_stop and price <= active_stop:
                exit_gain_pct = (price / p["entry_price"] - 1.0) * 100.0 if p["entry_price"] else 0.0
                if stop_source == "stop suiveur":
                    peak_gain_pct = (high_water / p["entry_price"] - 1.0) * 100.0 if p["entry_price"] else 0.0
                    trail_origin = "adapté à l'ATR" if p.get("entry_atr_pct") else "fixe"
                    exit_notes = (
                        f"Stop suiveur déclenché : plus haut {high_water:.6g} ({peak_gain_pct:+.1f}% vs entrée), "
                        f"retracement de {trail_pct * 100:.0f}% ({trail_origin}) depuis ce sommet a activé la "
                        f"protection -- sortie {price:.6g} ({exit_gain_pct:+.1f}% net vs entrée), "
                        f"{_duration_phrase(p.get('opened_at'))}."
                    )
                    close_reason = "stop suiveur"
                elif stop_source == "point mort verrouillé":
                    threshold_pct = (flash_threshold or 0.0) * 100.0
                    exit_notes = (
                        f"Point mort verrouillé (Breakeven Hard Floor) : le prix a touché au moins "
                        f"+{threshold_pct:.0f}% à un moment de la détention (seuil flash, indépendant "
                        f"de la confirmation temporelle du plus haut) -- le stop a été remonté "
                        f"irrévocablement au prix d'entrée {entry_price:.6g} -- sortie {price:.6g} "
                        f"({exit_gain_pct:+.1f}% net vs entrée), {_duration_phrase(p.get('opened_at'))}."
                    )
                    close_reason = "breakeven hard floor"
                else:
                    exit_notes = (
                        f"Invalidation technique atteinte : prix {price:.6g} <= seuil {invalidation:.6g} "
                        f"({exit_gain_pct:+.1f}% vs entrée) -- thèse invalidée, sortie immédiate, "
                        f"{_duration_phrase(p.get('opened_at'))}."
                    )
                    close_reason = "invalidation"
                closed = await close_position(
                    p["contract"], price,
                    reason=close_reason,
                    notes=exit_notes,
                    position_id=p["id"],
                )
                if closed:
                    actions["closed"].append(closed)
                    if notifier:
                        try:
                            await notifier(format_sell_alert(closed))
                        except Exception:  # noqa: BLE001 — the alert doesn't break the cycle
                            pass
                continue  # position closed, nothing else to evaluate this round

            # Staged profit-taking: sells a fraction of the INITIAL quantity at
            # each gain stage crossed. Last stage (or negligible remainder) ->
            # full close. ``stages`` (07/19): TP1 anchored on THIS position's
            # technical target if known and consistent, otherwise fixed
            # TP_STAGES fallback -- see _effective_tp_stages().
            initial_qty = p.get("initial_qty") or p["qty"]
            stage_hit = int(p.get("tp_stage_hit") or 0)
            remaining_qty = p["qty"]
            entry_price = p["entry_price"]
            gain_pct = (price / entry_price - 1.0) if entry_price else 0.0
            # 07/20 -- Regime Switch: the EFFECTIVE exit regime ratchets toward
            # the more cautious of the one observed at entry and the one
            # observed now -- never a relaxation, even if the market has since
            # become more optimistic (see docstring of
            # _apply_regime_to_tp_stages/more_cautious_meta_regime).
            effective_exit_regime = market_sentiment.more_cautious_meta_regime(
                p.get("entry_regime"), current_regime,
            )
            stages = _apply_regime_to_tp_stages(
                _effective_tp_stages(p.get("target_price"), entry_price), effective_exit_regime,
            )

            while stage_hit < len(stages) and gain_pct >= stages[stage_hit]:
                stage_hit += 1
                sell_qty = min(initial_qty * TP_STAGE_FRACTION, remaining_qty)
                is_last_stage = stage_hit >= len(stages) or remaining_qty - sell_qty <= TP_QTY_EPSILON
                stage_target_pct = stages[stage_hit - 1] * 100.0
                if is_last_stage:
                    tp_notes = (
                        f"Dernier palier de profit {stage_hit}/{len(stages)} atteint "
                        f"(+{gain_pct * 100:.0f}% vs entrée, seuil visé +{stage_target_pct:.0f}%) -- "
                        f"clôture du reliquat, {_duration_phrase(p.get('opened_at'))}."
                    )
                    closed = await close_position(
                        p["contract"], price,
                        reason=f"palier {stage_hit}/{len(stages)} (clôture)", notes=tp_notes,
                        position_id=p["id"],
                    )
                    if closed:
                        actions["closed"].append(closed)
                        if notifier:
                            try:
                                await notifier(format_sell_alert(closed))
                            except Exception:  # noqa: BLE001
                                pass
                    break

                partial_pct = TP_STAGE_FRACTION * 100.0
                remaining_after_pct = max(0.0, 100.0 - stage_hit * TP_STAGE_FRACTION * 100.0)
                partial_notes = (
                    f"Palier de profit {stage_hit}/{len(stages)} atteint "
                    f"(+{gain_pct * 100:.0f}% vs entrée, seuil visé +{stage_target_pct:.0f}%) -- "
                    f"prise de {partial_pct:.0f}% de la position initiale, "
                    f"~{remaining_after_pct:.0f}% restant en jeu."
                )
                partial = await reduce_position(
                    p["contract"], price, sell_qty, stage=stage_hit,
                    reason=f"palier {stage_hit}/{len(stages)}", notes=partial_notes,
                    position_id=p["id"],
                )
                if partial:
                    actions["partial"].append(partial)
                    remaining_qty = partial["remaining_qty"]
                    # 27/07, real bug found (operator screenshot): same stale-
                    # snapshot issue as the Take Seed branch above -- refresh
                    # this position's already-appended ``tracked`` entry so
                    # the periodic tracking alert built later this cycle
                    # shows the real post-reduction capital, not the
                    # pre-reduction one.
                    _refresh_tracked_after_partial(tracked, p["contract"], partial)
                    if notifier:
                        try:
                            await notifier(format_partial_exit_alert(partial))
                        except Exception:  # noqa: BLE001
                            pass

        # 1bis) Periodic tracking of STILL-open positions (#197, 07/15) -- not
        # just on buy/sell. Removes those closed THIS round (already covered
        # by format_sell_alert, never duplicated). A single consolidated
        # message, not one per position (avoids Telegram noise) -- DB
        # persistence (thesis, price, contract) takes priority over this
        # display anyway, which stays best-effort.
        closed_contracts_this_cycle = {c["contract"] for c in actions["closed"]}
        tracked = [t for t in tracked if t["contract"] not in closed_contracts_this_cycle]
        actions["tracked"] = tracked
        if tracked and notifier:
            # REAL equity/cash (07/17) -- reuses the price already fetched
            # this loop for each position (``t["price"]``), no new network
            # call; ``cash_available`` is a plain DB read (already used
            # elsewhere), never a duplicated computation.
            tracking_cash = tracking_equity = None
            try:
                tracking_cash = await cash_available()
                open_value = sum((t.get("qty") or 0.0) * (t.get("price") or 0.0) for t in tracked)
                tracking_equity = tracking_cash + open_value
            except Exception:  # noqa: BLE001 -- the alert degrades to the generic label, never fatal
                pass
            # 07/17 -- halves Telegram noise: only sends if the last send was
            # at least TRACKING_ALERT_MIN_INTERVAL_MINUTES ago. Never blocks a
            # real buy/sell alert (those have their own notifier above, never
            # subject to this window) -- only this periodic tracking is throttled.
            should_notify = True
            try:
                last_at = await get_last_tracking_alert_at()
                if last_at:
                    elapsed_min = (datetime.now(timezone.utc) - datetime.fromisoformat(last_at)).total_seconds() / 60.0
                    should_notify = elapsed_min >= TRACKING_ALERT_MIN_INTERVAL_MINUTES
            except Exception:  # noqa: BLE001 -- when in doubt, notify (graceful degradation)
                should_notify = True
            msg = format_position_tracking_alert(tracked, cash=tracking_cash, equity=tracking_equity)
            if msg and should_notify:
                try:
                    await notifier(msg)
                    await set_last_tracking_alert_at(_now())
                except Exception:  # noqa: BLE001 — the alert doesn't break the cycle
                    pass

    # 1ter) Portfolio risk snapshot (#186) -- once per cycle, AFTER managing
    # already-open positions (which must continue normally even if a circuit
    # breaker is armed) and BEFORE any opening attempt. Updates the persisted
    # equity high-water mark, arms the dedicated circuit breaker if a hard
    # threshold is crossed for the first time.
    #
    # 27/07 -- 3-pocket architecture plan, Phase 3 (real bug fixed):
    # ``multi_pocket_mode`` is resolved HERE, moved up from its original spot
    # at the ``if multi_pocket_sourcing_enabled() and default_sourcing:``
    # branch further below -- ``candidates``/``analyzer`` are plain function
    # parameters, never reassigned before this point, so this yields the
    # exact same value the old ``default_sourcing`` check computed later.
    # Needed HERE because risk_guard's circuit breaker is now per-pocket
    # (``wallet`` mandatory): the snapshot below stays scoped to "swing" (it
    # still feeds ``weekly_context`` a few lines down, and this pocket's own
    # alert), but must NEVER return early in multi-pocket mode -- before this
    # fix, a drawdown on the swing pocket ALONE silently returned before ever
    # reaching the 3-pocket loop, blocking scalping+vc too, even though
    # they're independent $1M portfolios with their OWN risk state.
    from aria_core import risk_guard

    multi_pocket_mode = multi_pocket_sourcing_enabled() and candidates is None and analyzer is None

    risk_state = await risk_guard.evaluate_portfolio_risk(wallet="swing", price_lookup=price_lookup)
    actions["risk_state"] = risk_state
    if risk_state.newly_triggered_hard and notifier:
        try:
            await notifier(risk_guard.format_hard_circuit_breaker_alert(risk_state, "swing"))
        except Exception:  # noqa: BLE001 — the alert doesn't break the cycle
            pass
    elif risk_state.newly_triggered_soft and notifier:
        try:
            await notifier(risk_guard.format_soft_drawdown_alert(risk_state, "swing"))
        except Exception:  # noqa: BLE001
            pass

    # Single-pocket path (gate OFF, or an explicit caller-provided
    # candidates/analyzer): unchanged historical behavior -- there IS only
    # one pocket on this path, so a blocked swing snapshot stops this cycle's
    # new entries entirely, exactly as before this chantier.
    # Multi-pocket path: the swing snapshot above is still computed (feeds
    # weekly_context below and the swing pocket's own alert) but must never
    # return early here -- each of the 3 pockets gets its OWN
    # risk_guard.evaluate_portfolio_risk(pocket_wallet) further down in the
    # loop, and only THAT pocket is skipped (``continue``) if blocked.
    if risk_state.blocked and not multi_pocket_mode:
        # Hard threshold (or global pause): no NEW entry this round -- already-open
        # positions have already been managed normally above (step 1).
        return actions

    if skip_new_entries:
        # 07/22 -- classic heartbeat cycle decoupled from discovery (explicit
        # operator decision): never looks for a new candidate here, discovery
        # now lives in its own cycle (momentum_discovery_cycle, 60min).
        # Monitoring of already-open positions above (step 1) and the
        # portfolio risk snapshot (step 1ter, just above) stay unchanged on
        # every pass.
        return actions

    # 07/18 -- explicit operator decision ("make her smarter"): weekly-cycle
    # cadence context (day X/7, equity vs +10% target), computed ONCE per
    # cycle and reusing risk_state.equity already computed above (no extra
    # network call). Passed to the momentum pipeline (tie-breaker + LLM safety
    # guard) -- best-effort, never blocking for the trading cycle itself.
    weekly_context: dict | None = None
    try:
        cap = await starting_capital()
        target = weekly_target_equity(cap)
        started_dt = datetime.fromisoformat(await cycle_started_at())
        if started_dt.tzinfo is None:
            started_dt = started_dt.replace(tzinfo=timezone.utc)
        elapsed_days = (datetime.now(timezone.utc) - started_dt).total_seconds() / 86400.0
        progress_pct = (risk_state.equity / cap - 1.0) * 100.0 if cap else 0.0
        # 07/18 (continued, cross-review) -- distance to target in percentage
        # points, in addition to raw dollars: an LLM handles a progress ratio
        # ("0.5 pt left to the target") more reliably than a mental
        # subtraction between two large numbers. positive = still some way to
        # go, <=0 = target already reached/exceeded.
        target_pct = (WEEKLY_TARGET_MULTIPLIER - 1.0) * 100.0
        weekly_context = {
            "cycle_number": await get_current_cycle_number(),
            "day": min(WEEKLY_CYCLE_DAYS, int(elapsed_days) + 1),
            "days_total": WEEKLY_CYCLE_DAYS,
            "equity": risk_state.equity,
            "target_equity": target,
            "progress_pct": progress_pct,
            "remaining_pct": target_pct - progress_pct,
        }
    except Exception as exc:  # noqa: BLE001 — never blocking, degrades in the absence of context
        logger.info("paper_cycle: weekly cadence context unavailable (%s)", exc)
        weekly_context = None

    # 2) Open new positions from ranked candidates (real buy signal) --
    #    unless USDC is depegged (#187): this whole portfolio's pricing
    #    assumes a stable USD, we block NEW entries (already-open positions
    #    aren't touched) as long as the depeg hasn't resolved.
    # #194 -- multi-chain momentum pivot: when NEITHER candidates NOR analyzer
    # are provided (the real heartbeat case, run_paper_cycle(notifier=...)
    # with no arguments), replaces the candidate_ranking.top_candidates()/
    # _default_analyzer default (VC-thesis, 85% pocket) with the momentum
    # pipeline for THIS TEST -- explicit, reversible operator decision,
    # screened_pool/safety_screen untouched. Any caller providing ITS OWN
    # candidates or analyzer keeps unchanged historical behavior.
    #
    # 27/07 -- 3-pocket architecture plan, Phase 2 (gate
    # ``multi_pocket_sourcing_enabled()``): the 3-way split below ONLY replaces
    # this SAME default heartbeat case (candidates=None AND analyzer=None) --
    # same scoping precedent as the #194 pivot comment right above (a caller
    # providing its own candidates/analyzer, e.g. momentum_websocket.py's
    # real-time drain or any test, keeps its unchanged single-pocket
    # behavior regardless of this gate, always booking into "swing" as
    # before -- multi-pocket sourcing never overrides an explicit caller's
    # own candidate/analyzer choice).
    # ``default_sourcing``/the gate check is now folded into ``multi_pocket_
    # mode``, resolved earlier (right before the risk snapshot above) so that
    # snapshot could decide whether to return early -- kept as the same
    # boolean expression, just computed once instead of twice.
    funnel: dict[str, int] = {}

    if multi_pocket_mode:
        # gate ON, default heartbeat case: 3 independent pockets (scalping/
        # swing/vc), NEVER mixing candidates/analyzers across them. Momentum
        # discovery (#194) is fetched ONCE and shared by scalping+swing (same
        # real-world scan, only the analyzer's ``mode`` differs) -- never a
        # duplicated network call for the same discovery pass.
        momentum_candidates, _momentum_chain_by_contract = await _momentum_candidates_and_chain_map(limit=20)
        from aria_core.skills.candidate_ranking import top_candidates

        vc_candidates = [c.contract for c in await top_candidates(20)]

        # Nothing to buy anywhere -> no need to check the depeg (same
        # avoidance of a needless network call as the single-pocket path
        # below). A single depeg check/verdict applies to ALL 3 pockets --
        # this whole portfolio's (all pockets') pricing assumes a stable USD.
        depeg_pct = None
        depegged = False
        if momentum_candidates or vc_candidates:
            depeg_check = depeg_check or risk.usdc_depeg_pct
            try:
                depeg_pct = await depeg_check()
            except Exception as exc:  # noqa: BLE001
                logger.info("paper_cycle: USDC depeg check failed (%s)", exc)
                depeg_pct = None
            depegged = depeg_pct is not None and depeg_pct > risk.USDC_DEPEG_THRESHOLD_PCT
        actions["usdc_depeg_pct"] = depeg_pct
        actions["depeg_blocked"] = depegged

        if depegged:
            logger.warning(
                "paper_cycle: USDC depegged %.2f%% (> threshold %.2f%%) -- new entries blocked "
                "this cycle (all pockets)",
                (depeg_pct or 0.0) * 100, risk.USDC_DEPEG_THRESHOLD_PCT * 100,
            )
            return actions

        # 27/07 -- Phase 3: MACRO circuit breaker, checked ONCE per cycle,
        # BEFORE any of the 3 per-pocket risk checks below (a correlated
        # crash across all 3 pockets at once is a reason to stop EVERYTHING,
        # not just let each pocket notice its own drawdown independently).
        # ``newly_triggered`` arms ``outgoing_pause`` itself (the REAL global
        # kill-switch) -- see ``evaluate_macro_risk``'s own docstring for why
        # that's intentional here. Best-effort: a failure here degrades to
        # "not triggered" (never blocks the cycle on an unrelated error),
        # same doctrine as the depeg check just above.
        try:
            macro_state = await risk_guard.evaluate_macro_risk(price_lookup=price_lookup)
        except Exception as exc:  # noqa: BLE001
            logger.info("paper_cycle: macro circuit breaker check failed (%s)", exc)
            macro_state = None
        actions["macro_risk_state"] = macro_state
        if macro_state is not None and macro_state.newly_triggered:
            if notifier:
                try:
                    await notifier(risk_guard.format_macro_circuit_breaker_alert(macro_state))
                except Exception:  # noqa: BLE001
                    pass
            return actions

        # We don't re-enter a name we just EXITED this round (avoids churn: an
        # exit on trailing stop/last stage requires a new signal on the next
        # round, not an immediate rebuy) -- shared across all 3 pockets, same
        # as the single-pocket path below.
        closed_this_cycle = {c["contract"] for c in actions["closed"]}

        scalping_analyzer = _default_momentum_analyzer(
            _momentum_chain_by_contract, weekly_context=weekly_context, current_regime=current_regime,
            mode="scalping",
        )
        swing_analyzer = _default_momentum_analyzer(
            _momentum_chain_by_contract, weekly_context=weekly_context, current_regime=current_regime,
            mode="standard",
        )

        # (wallet, candidates, analyzer, trading_mode-for-thresholds, position cap)
        for pocket_wallet, pocket_candidates, pocket_analyzer, pocket_mode, pocket_cap in (
            ("scalping", momentum_candidates, scalping_analyzer, "scalping", MAX_POSITIONS_SCALPING),
            ("swing", momentum_candidates, swing_analyzer, "standard", MAX_POSITIONS_SWING),
            ("vc", vc_candidates, _default_analyzer, "standard", MAX_POSITIONS_VC),
        ):
            # 27/07 -- Phase 3: independent per-pocket risk state -- SUPERSEDES
            # the single ``risk_state`` snapshot computed above (that one
            # stays "swing"-scoped, only used for weekly_context/its own
            # alert). A drawdown/losing streak on ONE pocket alone must never
            # block the other two: skip (``continue``) THIS pocket only,
            # never a global ``return``.
            pocket_risk_state = await risk_guard.evaluate_portfolio_risk(
                pocket_wallet, price_lookup=price_lookup,
            )
            if pocket_risk_state.newly_triggered_hard and notifier:
                try:
                    await notifier(risk_guard.format_hard_circuit_breaker_alert(pocket_risk_state, pocket_wallet))
                except Exception:  # noqa: BLE001
                    pass
            elif pocket_risk_state.newly_triggered_soft and notifier:
                try:
                    await notifier(risk_guard.format_soft_drawdown_alert(pocket_risk_state, pocket_wallet))
                except Exception:  # noqa: BLE001
                    pass
            if pocket_risk_state.blocked:
                continue

            opened_positions, _ = await _open_new_entries_for_wallet(
                pocket_wallet, pocket_candidates, pocket_analyzer,
                price_lookup=price_lookup, notifier=notifier, max_new=max_new,
                using_default_price_lookup=using_default_price_lookup,
                closed_this_cycle=closed_this_cycle, weekly_context=weekly_context,
                risk_state=pocket_risk_state, discovery_channel=discovery_channel,
                trading_mode=pocket_mode, max_positions_cap=pocket_cap,
                funnel=funnel,
            )
            actions["opened"].extend(opened_positions)
        # momentum_candidates is walked TWICE (once per pocket sharing it,
        # scalping+swing) -- reflects the real evaluation count, not just the
        # distinct-candidate count, for this purely informational log line.
        total_candidates_evaluated = 2 * len(momentum_candidates) + len(vc_candidates)
    else:
        # gate OFF (default), OR a caller explicitly provided its own
        # candidates/analyzer: EXACT historical single-pocket behavior --
        # everything below is byte-for-byte unchanged from before this
        # chantier, just now passing wallet="swing" explicitly to
        # ``_open_new_entries_for_wallet`` (the only new wiring needed to
        # keep working under ``open_position``'s new mandatory ``wallet`` param).
        if candidates is None and analyzer is None:
            candidates, _momentum_chain_by_contract = await _momentum_candidates_and_chain_map(limit=20)
            analyzer = _default_momentum_analyzer(
                _momentum_chain_by_contract, weekly_context=weekly_context, current_regime=current_regime,
                mode=trading_mode,
            )
        elif candidates is None:
            from aria_core.skills.candidate_ranking import top_candidates

            candidates = [c.contract for c in await top_candidates(20)]

        # Nothing to buy -> no need to check the depeg (avoids a needless network
        # call every cycle, including when no candidate is proposed this round).
        depeg_pct = None
        depegged = False
        if candidates:
            depeg_check = depeg_check or risk.usdc_depeg_pct
            try:
                depeg_pct = await depeg_check()
            except Exception as exc:  # noqa: BLE001
                logger.info("paper_cycle: USDC depeg check failed (%s)", exc)
                depeg_pct = None
            depegged = depeg_pct is not None and depeg_pct > risk.USDC_DEPEG_THRESHOLD_PCT
        actions["usdc_depeg_pct"] = depeg_pct
        actions["depeg_blocked"] = depegged

        if depegged:
            logger.warning(
                "paper_cycle: USDC depegged %.2f%% (> threshold %.2f%%) -- new entries blocked this cycle",
                (depeg_pct or 0.0) * 100, risk.USDC_DEPEG_THRESHOLD_PCT * 100,
            )
            return actions

        analyzer = analyzer or _default_analyzer
        # We don't re-enter a name we just EXITED this round (avoids churn: an
        # exit on trailing stop/last stage requires a new signal on the next
        # round, not an immediate rebuy).
        closed_this_cycle = {c["contract"] for c in actions["closed"]}
        # #186 -- soft threshold: halves the allocation of NEW entries (never
        # already-open positions) via ``risk_state.alloc_multiplier``, composed
        # further below with the risk/ATR sizing (or its fixed-stage fallback).
        # open_position THEN applies its own per-trade risk cap (defense in
        # depth, see size_position_by_risk).

        # Per-cycle funnel (mandate #192, 07/16): aggregates WHY each evaluated
        # candidate didn't lead to a buy. Without this, a prolonged outage of the
        # sole hard guardrail (GoPlus, no fallback -- see momentum_entry.py)
        # produces exactly the same observable symptom (zero new positions) as a
        # market genuinely without a valid candidate -- indistinguishable without
        # reading application logs one by one, which defeats the diagnostic
        # purpose of the $1M test (understand HOW ARIA trades, not just WHETHER
        # she trades). Purely additive: changes no decision behavior, only
        # visibility. The ``hold_reason`` field (momentum_entry.py) feeds this
        # counter; an analyzer that doesn't provide it (e.g. the historical
        # VC-thesis pilot, ``_default_analyzer``) falls into the generic
        # "unspecified" bucket, without error.
        #
        # 27/07 -- position-count cap decoupled from ``open_position``'s own
        # inner defense-in-depth check: this is the REAL per-cycle chokepoint,
        # unchanged from before this chantier (bypassed entirely in scalping
        # mode, same doctrine as the scalping MAX_POSITIONS bypass).
        max_positions_cap = None if trading_mode == "scalping" else MAX_POSITIONS
        opened_positions, _ = await _open_new_entries_for_wallet(
            "swing", candidates, analyzer,
            price_lookup=price_lookup, notifier=notifier, max_new=max_new,
            using_default_price_lookup=using_default_price_lookup,
            closed_this_cycle=closed_this_cycle, weekly_context=weekly_context,
            risk_state=risk_state, discovery_channel=discovery_channel,
            trading_mode=trading_mode, max_positions_cap=max_positions_cap,
            funnel=funnel,
        )
        actions["opened"].extend(opened_positions)
        total_candidates_evaluated = len(candidates)

    if funnel:
        actions["momentum_funnel"] = funnel
        logger.info("paper_cycle funnel (new entries, %d candidates): %s", total_candidates_evaluated, funnel)
        # 07/19 -- persists this cycle for a queryable cumulative view over
        # time (momentum_funnel_log.py): without this, this funnel only
        # existed in application logs, never accumulated -- answers ARIA's
        # own proposal ("log the per-step counter for 48h... proof before
        # opinion"). Best-effort: a write failure must never break a real
        # trading cycle for a mere telemetry persistence.
        try:
            await momentum_funnel_log.record_funnel(funnel)
        except Exception as exc:  # noqa: BLE001
            logger.warning("paper_cycle: funnel persistence failed (%s)", exc)

    return actions
