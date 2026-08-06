"""Portfolio risk management (#186, 07/15) — risk-adjusted sizing +
drawdown circuit breaker, applied for now to the $1M paper portfolio only
(``paper_trader.py``). No wiring yet to a real-capital pilot (not built
yet) -- but this module is designed as a reusable seam as-is for the day a
real pilot exists: the two functions below know nothing about "paper" vs
"real", they only work with generic USD/prices/counters.

Research behind this work: Paul Tudor Jones (never >1% of capital risked
per trade, independent of position size) and Ray Dalio/Bridgewater (never
let a drawdown exceed ~1/3 of capital -- beyond that, the mathematical
recovery becomes punitive: -50% requires +100% to get back to zero).
``RISK_CAP_PCT``/``HARD_DRAWDOWN_PCT`` below are deliberately more
conservative than these extreme bounds (2%/20% rather than 1%/33%),
consistent with capital that's still fictional but whose goal is to prove
a discipline transposable to the real thing.

Three distinct mechanisms, never to be confused:
1. Per-trade sizing (``size_position_by_risk``) -- a PURE function, no
   persisted state, caps an allocation based on the distance to
   invalidation. NEVER raises an allocation beyond its entry value -- a
   cap, never a bonus.
2. Per-pocket portfolio circuit breaker (``evaluate_portfolio_risk``/
   ``blocks_new_entries``, both take a mandatory ``wallet``) -- persisted
   state, ONE dedicated JSON file PER POCKET (scalping/swing/vc -- 27/07,
   3-pocket architecture plan Phase 3), NOT ``outgoing_pause.py`` -- that
   global kill-switch also cuts cycles unrelated to money, e.g.
   ``knowledge_inbox``. ``blocks_new_entries`` itself respects
   ``outgoing_pause`` (a global pause also blocks new paper entries in
   EVERY pocket at once) WITHOUT ever being confused with it -- separate
   state files, distinct reasons reported to the caller. A drawdown/losing
   streak on ONE pocket alone must never block the other two -- the real
   gap this Phase 3 work closes (before it, a single shared, unscoped call
   site silently made a scalping-only drawdown block swing+vc too).
3. MACRO circuit breaker (``evaluate_macro_risk``, section 3 below) --
   aggregates equity across all 3 pockets, checked once per cycle BEFORE
   any per-pocket check. Deliberately coarser and more drastic: on first
   breach it arms ``outgoing_pause`` itself (the real, portfolio-external
   kill-switch), covering the blind spot the per-pocket split above
   creates -- a genuinely correlated crash across all 3 pockets at once,
   where each could individually sit just under its own hard threshold.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aria_core.paths import data_dir

logger = logging.getLogger(__name__)

# ── 1. Risk-adjusted sizing (pure function, no state) ─────────────────

RISK_CAP_PCT = 0.02  # 2% of total capital risked at worst case (between PTJ's very
# conservative 1% and the current implicit maximum ~5% of the flat ALLOC_PCT).


def size_position_by_risk(
    alloc_usd: float, entry_price: float, invalidation_price: float | None, capital_total: float,
) -> float:
    """Caps ``alloc_usd`` so that the worst-case loss (if the price hits
    ``invalidation_price``) never exceeds ``RISK_CAP_PCT * capital_total``.
    NEVER raises ``alloc_usd`` beyond its entry value -- a cap, never a
    bonus (a position with a VERY tight stop keeps its original flat
    allocation, it's never inflated by this mechanism).

    Without a known invalidation (``None``, or ``>= entry_price`` -- risk
    not measurable or inconsistent data), ``alloc_usd`` is returned
    unchanged: the trailing stop (``TRAIL_STOP_PCT`` in ``paper_trader.py``)
    then remains the sole guardrail, as before this work."""
    if alloc_usd <= 0 or entry_price <= 0 or capital_total <= 0:
        return alloc_usd
    if invalidation_price is None or invalidation_price <= 0 or invalidation_price >= entry_price:
        return alloc_usd

    risk_fraction = (entry_price - invalidation_price) / entry_price  # % loss if stop hit
    if risk_fraction <= 0:
        return alloc_usd

    risked_usd = alloc_usd * risk_fraction
    cap_usd = RISK_CAP_PCT * capital_total
    if risked_usd <= cap_usd:
        return alloc_usd

    capped_alloc = cap_usd / risk_fraction
    return min(alloc_usd, capped_alloc)


# 07/18 -- explicit operator decision: "more aggressive" means bigger on the
# BEST setups, not bigger everywhere (never a flat bonus). Second PURE function,
# no state -- applies UPSTREAM of size_position_by_risk above, which remains the
# real worst-case loss cap (2% of capital): an allocation inflated by conviction
# stays capped exactly as before on a wide stop, this is never a bet without a
# safety net.
CONVICTION_RR_THRESHOLD = 2.5
# 07/19 -- lowered from 3 to 2 (explicit operator decision, via AskUserQuestion): on
# the first 5 real trades of the momentum pipeline (#194), align_score NEVER
# reached 3/3 -- always "MACD above its signal line" + "bullish candle pattern",
# never "EMA12 > EMA26" at the same time. Hypothesis verified in the code (not
# a bug): a golden-pocket buy (DEEP reload) is structurally in tension with
# "short EMA already crossed back above the long one" -- at the moment the
# price reloads deeply, the fast EMA is often still below the slow one. With the
# threshold at 3, the technical-conviction bonus was thus nearly unreachable for
# this specific entry style, still never a bet without a safety net though (minimum R/R unchanged).
CONVICTION_ALIGN_SCORE_THRESHOLD = 2

# 07/19 (continued) -- full sizing REDESIGN (direct operator feedback after seeing
# the real portfolio: "positions are too big, max buy should be 5% and
# min 2%"). Replaces the previous binary (flat 5% base / exceptional bonus -> 8%,
# ``CONVICTION_SIZE_MULTIPLIER=1.6`` -- REMOVED, the operator now explicitly caps at 5%
# max) with 3 conviction tiers, mapped directly onto the real percentage
# of starting capital (never a multiplier > 1.0 -- 5% IS the cap, not a
# multiplier of a multiplier). ``MODERATE_RR_THRESHOLD`` reuses exactly the
# minimum R/R of the DIRECT buy path (``momentum_entry._RR_MIN_FOR_DIRECT_BUY``, 2.0) --
# deliberately an independent constant here (not a cross-module import) to keep
# ``risk_guard`` autonomous from ``momentum_entry``, same doctrine as ``CONVICTION_RR_
# THRESHOLD`` already independent since the start of this work.
MODERATE_RR_THRESHOLD = 2.0

# 08/04 -- scalping-dedicated thresholds (operator diligence + Fable 5 cross-
# review, same session as the ATR-trail scalping fix of 08/03): the two
# constants above were calibrated for swing BEFORE scalping existed, and the
# 5 legacy scalping variants' R/R never realistically reaches them (v2/v4/v5
# have a FIXED R/R by construction, 1.3-1.5, structurally below MODERATE_RR_
# THRESHOLD).
#
# FIRST calibration pass (same day, since superseded) used 1059 pending
# limit orders' persisted R/R at face value -- p50=0.42/p75=0.99/p90=1.70.
# Fable 5's SECOND diligence pass (same day) found a DEEPER bug upstream:
# the ATR invalidation floor computing those very R/R values was itself
# NEVER scoped by mode (entry_signals.MIN/MAX_ATR_INVALIDATION_PCT, exactly
# the same gap as the ATR-trail before its 08/03 fix) -- confirmed live on 3
# real orders all pinned to exactly -5.0% invalidation. That bug silently
# UNDERSTATED every scalping R/R (a wider-than-necessary swing-calibrated
# floor inflates the risk denominator). Fixed first (entry_signals.
# ATR_INVALIDATION_MULTIPLIER_SCALPING/MIN/MAX_ATR_INVALIDATION_PCT_
# SCALPING, 08/04) -- see entry_signals.py's own comment -- along with a
# range>=2xATR significance filter (rejects setups indistinguishable from
# ATR noise) BEFORE this second, real calibration pass, per the operator's
# explicit process guardrail (never calibrate on a knowingly biased
# distribution).
#
# RETROACTIVE recalculation (04/08, scripted, entry_signals.
# _invalidation_floor_pct_from_ratio imported directly -- never a
# reimplementation of the formula): re-derives what R/R WOULD have been with
# the corrected floor + filter, from ``pending_limit_order.signal_json``'s
# persisted gp_low/entry_atr_pct/target/target_price (real candles aren't
# stored per historical order -- ATR/close is approximated by the persisted
# entry_atr_pct, ATR/entry-price, a close proxy since the two are evaluated
# moments apart in the live pipeline; range_width is reconstructed from
# target/gp_low via the FIXED 0.786 Fibonacci ratio, an exact algebraic
# identity, not a guess). entry_atr_pct was only persisted starting 08/02
# (Item #253) -- of 1164 v6+v7 pending orders, 848 predate it and are
# excluded outright (missing field, never estimated), 2 more are filtered by
# the new significance filter, 23 more have no valid post-fix R/R (broken
# structural consistency) -- final population n=291 (v6: 233, v7: 58), the
# HONEST sample size this calibration actually rests on (smaller than the
# first pass's 1059 -- explicitly NOT backfilled with a guess). New
# distribution: p50=0.66/p75=1.38/p90=2.24 (v6-only: p75=1.46/p90=2.28; v7-
# only: p75=1.00/p90=1.65) -- roughly DOUBLE the biased first pass at every
# percentile, confirming the floor bug had been suppressing scalping R/R
# across the board, not just the 3 orders it was directly caught on.
#
# Calibrated on this corrected sample's p75/p90 (rounded to 1 decimal) -- a
# scalping R/R is a RELATIVE ranking of setup quality, never an absolute
# expectancy (a setup's real exit runs through the ATR trailing stop/staged
# TP, which can blow past its own nominal target -- see the DRV case, R/R
# 0.066 realized +18.3%, Item #252 31/07). First-pass thresholds on an
# already-small n=291 sample, deliberately NOT auto-recalibrated on a
# rolling window (that would silently hand out 5% to "the best of a bad
# batch" even when the whole flow degrades) -- fixed constants, backlog task
# to revisit once significantly more entry_atr_pct-populated orders
# accumulate (same doctrine as the ATR-trail bounds, calibrated on only 7
# trades 08/03).
MODERATE_RR_THRESHOLD_SCALPING = 1.4
CONVICTION_RR_THRESHOLD_SCALPING = 2.2

MIN_ALLOC_MULTIPLIER = 0.4       # 5% * 0.4 = 2% of starting capital (weak tier)
MODERATE_ALLOC_MULTIPLIER = 0.7  # 5% * 0.7 = 3.5% of starting capital (moderate tier)
MAX_ALLOC_MULTIPLIER = 1.0       # 5% * 1.0 = 5% of starting capital (strong tier, hard cap)

# 07/19 -- explicit operator decision (choice confirmed via AskUserQuestion, "adds
# on with AND"): fundamental potential (conviction_research.py -- website/X/
# publication cadence/contract corroboration) becomes a THIRD criterion for the strong
# tier, IN ADDITION to the R/R+technical alignment already required -- never in their place.
# Threshold below which a CONFIRMED (not absent) fundamental score downgrades the tier --
# fail-closed on confirmed-bad data, fail-open on UNKNOWN data
# (``fundamental_score=None``, e.g. research unavailable/gate OFF): a
# technically perfect setup with no fundamental research available keeps EXACTLY the tier
# it would have had before this work -- never reduced below what it has today, same
# fail-open/fail-closed doctrine already validated on wallet-scoring (smart_money.py).
FUNDAMENTAL_WEAK_THRESHOLD = 4.0

# 25/07 -- operator-found gap, real loss: a CHECK position (-27.3%, -$7374) had a
# CONFIRMED fundamental score of 2.0/10 with an explicit rationale ("contenu web
# incoherent et contrat different annonce signalent une usurpation probable") --
# below FUNDAMENTAL_WEAK_THRESHOLD this only downgraded the conviction tier (never
# below the WEAK floor, still buys), the setup was bought anyway on pure technical
# alignment. A score this catastrophic is a different class of signal from merely
# "weak" -- distinct, stricter threshold below which momentum_entry.py rejects the
# candidate outright (HOLD), same fail-open-on-unknown/fail-closed-on-confirmed
# doctrine as everywhere else (None never rejects, only a CONFIRMED bad score).
# Mirrors the doctrine already proven on bonding_entry.py's composite score (a low
# potential_score there can already sink the weighted total below its own
# threshold and reject) -- this closes the same gap on the standard momentum path,
# which had no equivalent until now.
FUNDAMENTAL_REJECT_THRESHOLD = 2.5

# 28/07 -- dex_composite_score.py's additive signal for an already-graduated
# DEX candidate (contract/dev residual risk beyond the honeypot class, dev
# wallet behavior, generalized smart money, liquidity/mcap depth -- see that
# module's docstring). Same fail-open/fail-closed doctrine and same 2-tier
# structure as FUNDAMENTAL_WEAK_THRESHOLD/FUNDAMENTAL_REJECT_THRESHOLD above:
# a CONFIRMED (not None) score below the WEAK threshold becomes a THIRD
# conviction-tier flag, alongside weak_fundamentals/unconfirmed_volume; below
# the stricter REJECT threshold, momentum_entry.py rejects the candidate
# outright. First-pass thresholds, not yet calibrated against real outcomes
# (dex_score_log.py records every scan precisely so this can be revisited via
# performance_breakdown.py once enough observations accumulate).
#
# 28/07 (2nd pass, operator decision) -- dex_composite_score.py's neutral
# base was lowered from 50% to 35% of each pillar's weight (pillar 1 also
# made binary), moving the "nothing confirmed anywhere" structural floor
# from ~67.5/100 down to exactly 35.0/100 (0.35 * 100, since every pillar's
# neutral share is now 35% of its own weight). Reconfirmed rather than
# changed: 35.0 sits deliberately just BELOW WEAK_THRESHOLD (40) -- a
# candidate with zero positive evidence anywhere is now flagged weak BY
# DEFAULT, exactly the operator's stated goal, without needing to touch this
# threshold itself. REJECT_THRESHOLD (15) remains reachable on a genuinely
# bad combination (e.g. one confirmed-bad contract signal, contract_risk=0,
# stacked with a confirmed-weak smart-money/liquidity read) but is no longer
# triggered by a single bad flag alone when every other pillar stays neutral
# (0 + 7.0 + 8.75 + 7.0 = 22.75, still above 15) -- consistent with the
# existing doctrine that an outright reject requires more than one weak
# signal, while a single one already downgrades the conviction tier via
# WEAK_THRESHOLD.
DEX_SECURITY_WEAK_THRESHOLD = 40.0
DEX_SECURITY_REJECT_THRESHOLD = 15.0

# Item #182 (28/07), golden-pocket liberation -- operator-confirmed
# ("l'objectif d'avoir un score plus strict c'est de liberer le golden pocket
# un peu car il filtre trop"): the golden pocket/RSI gate in momentum_entry.py
# is NEVER softened as a criterion (still required, unchanged, to buy
# outright) -- but when the price hasn't reached the zone YET (never when it
# already broke below it, a dead setup) and this independently-computed DEX
# composite score already confirms high quality, a limit order watches and
# waits for the setup to actually form rather than discarding the candidate.
# Starting value, explicit operator decision (28/07): "pour l'instant met 70
# pour voir combien de signaux on va entrer et enssuite on ajustera" -- to be
# revisited once real signal volume is observed (same "first pass, not yet
# calibrated" doctrine as the two thresholds above, dex_score_log.py records
# every scan for exactly this future recalibration).
DEX_QUALITY_WATCH_THRESHOLD = 70.0


def _rr_thresholds(mode: str | None) -> tuple[float, float]:
    """(conviction_threshold, moderate_threshold) for ``mode`` -- scalping
    gets its own dedicated pair (see ``MODERATE_RR_THRESHOLD_SCALPING``'s own
    comment for the full calibration rationale), every other mode (including
    ``None``, unchanged historical behavior) keeps the swing-calibrated
    ``CONVICTION_RR_THRESHOLD``/``MODERATE_RR_THRESHOLD``."""
    if mode == "scalping":
        return CONVICTION_RR_THRESHOLD_SCALPING, MODERATE_RR_THRESHOLD_SCALPING
    return CONVICTION_RR_THRESHOLD, MODERATE_RR_THRESHOLD


def conviction_size_multiplier(
    rr: float | None, align_score: int | None, *,
    fundamental_score: float | None = None, volume_confirmed: bool | None = None,
    dex_security_score: float | None = None, mode: str | None = None,
) -> float:
    """Multiplier applied to ``ALLOC_PCT`` (5%, ``paper_trader.py``) -- never
    beyond ``MAX_ALLOC_MULTIPLIER`` (1.0 = 5% of capital, the hard cap requested
    by the operator), never below ``MIN_ALLOC_MULTIPLIER`` (0.4 = 2%) for
    any actually measured signal. 3 tiers, on the R/R (the only signal that still
    discriminates once technical alignment is capped at a 2/3 threshold -- see above):
    - STRONG (``MAX_ALLOC_MULTIPLIER``, 5%): R/R >= ``CONVICTION_RR_THRESHOLD`` (2.5) AND
      alignment >= ``CONVICTION_ALIGN_SCORE_THRESHOLD`` (2/3) -- the strongest setup.
    - MODERATE (``MODERATE_ALLOC_MULTIPLIER``, 3.5%): R/R >= ``MODERATE_RR_THRESHOLD``
      (2.0, the very floor of the direct buy path) without reaching the strong tier.
    - WEAK (``MIN_ALLOC_MULTIPLIER``, 2%): everything else with a measured signal
      (typically an LLM-confirmed buy on an R/R below the direct floor).

    Missing/incomplete data (``rr`` or ``align_score`` = ``None``) ->
    ``MAX_ALLOC_MULTIPLIER``: UNCHANGED behavior for any caller that doesn't supply
    these signals (e.g. the old VC-thesis pilot, dormant) -- never reduced below what
    it had before this work, only the momentum pipeline (which always supplies these
    two fields on a BUY) is affected by the new 5% cap.

    ``fundamental_score`` (07/19, optional): if the STRONG tier is reached BUT
    fundamental research CONFIRMED a weak potential (< ``FUNDAMENTAL_WEAK_
    THRESHOLD``), downgrades the tier (see stacking below). ``None`` (research not
    performed/unavailable) NEVER downgrades the technical tier.

    ``volume_confirmed`` (07/19, Gemini cross-review, optional): same veto
    doctrine as ``fundamental_score`` -- ``False`` (the relative volume of the entry
    candle could not be verified, cf. ``momentum_entry._check_volume_confirmation``,
    "unknown" state) downgrades the tier (see stacking below). ``None``/``True`` never
    downgrade -- a ``False`` with REAL DATA confirming the absence of
    volume ("not_confirmed" state) never reaches this function: that case is already
    a hard rejection upstream (``hold_reason="volume_not_confirmed"``), never a matter
    of size.

    ``dex_security_score`` (28/07, optional, ``dex_composite_score.py``): same veto
    doctrine as ``fundamental_score`` -- a CONFIRMED score below ``DEX_SECURITY_WEAK_
    THRESHOLD`` downgrades the tier (see stacking below). ``None`` (chain not Base,
    scalping mode, or resolution failed) never downgrades.

    Stacking of the (now up to three) vetoes (07/19, Gemini cross-review, round 5 --
    fixes a real risk-management flaw: composing every flag into the SAME MODERATE
    tier treated a setup with several independent warning signals as equivalent to a
    setup with only one -- underestimating the cumulative risk) -- one flag alone ->
    MODERATE tier (3.5%); two or more at once -> direct drop to the WEAK tier (2%),
    never a 4th tier below (the ``MIN_ALLOC_MULTIPLIER`` floor remains the true floor,
    regardless of the number of vetoes).

    ``mode`` (08/04): ``"scalping"`` switches the R/R thresholds to the
    scalping-dedicated pair (see ``_rr_thresholds``) -- the swing-calibrated
    ones are almost never reached by a scalping setup by construction (see
    ``MODERATE_RR_THRESHOLD_SCALPING``'s own comment). Any other value
    (``None`` included) keeps the original swing thresholds, unchanged."""
    if rr is None or align_score is None:
        return MAX_ALLOC_MULTIPLIER
    conviction_threshold, moderate_threshold = _rr_thresholds(mode)
    if rr >= conviction_threshold and align_score >= CONVICTION_ALIGN_SCORE_THRESHOLD:
        weak_fundamentals = fundamental_score is not None and fundamental_score < FUNDAMENTAL_WEAK_THRESHOLD
        unconfirmed_volume = volume_confirmed is False
        weak_dex_security = dex_security_score is not None and dex_security_score < DEX_SECURITY_WEAK_THRESHOLD
        flags = int(weak_fundamentals) + int(unconfirmed_volume) + int(weak_dex_security)
        if flags >= 2:
            return MIN_ALLOC_MULTIPLIER
        if flags == 1:
            return MODERATE_ALLOC_MULTIPLIER
        return MAX_ALLOC_MULTIPLIER
    if rr >= moderate_threshold:
        return MODERATE_ALLOC_MULTIPLIER
    return MIN_ALLOC_MULTIPLIER


# 07/20 -- HYBRID risk-target/ATR sizing (Gemini cross-review round 7, explicit
# operator go-ahead: "Your composition proposal is brilliant... you can code this
# logic"). Fixes a real flaw in ``conviction_size_multiplier`` above: its
# tiers are FIXED % of capital (5/3.5/2%), totally independent of the width
# of the ATR trailing stop -- a very nervous token (wide stop, e.g. 35%) and a calm token
# (tight stop, e.g. 8%) receive the SAME allocation at the same conviction tier, even though
# the former mathematically risks much more in dollars if the stop is
# hit. ``size_position_by_risk`` (based on the Fibonacci invalidation, fixed at
# entry) already caps the worst-case loss at 2% -- but ATR governs the REAL
# SPACE in which the trailing stop moves once the position is open, never taken
# into account by the initial sizing until now.
#
# Conviction tiers become RISK BUDGETS (fraction of capital one
# accepts to lose IF the ATR trailing stop is hit), divided by the effective ATR
# width to get the $ allocation -- a wide stop mechanically reduces
# the allocation, a tight stop increases it, at constant $ risk for a given
# conviction tier. ``size_position_by_risk`` (invalidation) remains applied AFTERWARD
# in ``open_position``, unchanged, as the final safety net -- never removed or
# bypassed by this new mechanism.
CONVICTION_RISK_BUDGET_STRONG_PCT = 0.015    # 1.5% of capital -- STRONG tier
CONVICTION_RISK_BUDGET_MODERATE_PCT = 0.010  # 1.0% -- MODERATE tier
CONVICTION_RISK_BUDGET_WEAK_PCT = 0.005      # 0.5% -- WEAK tier


def conviction_risk_budget_pct(
    rr: float | None, align_score: int | None, *,
    fundamental_score: float | None = None, volume_confirmed: bool | None = None,
    dex_security_score: float | None = None, mode: str | None = None,
) -> float | None:
    """Risk budget (fraction of capital) for the conviction tier of THIS
    signal -- same tiering and same stacking of the (now up to three) vetoes as
    ``conviction_size_multiplier`` above (identical word for word, only the OUTPUT
    tiers change: a risk budget in %, not a multiplier on a flat allocation). ``None``
    if ``rr``/``align_score`` are missing -- signals to the caller to fall back
    on ``conviction_size_multiplier`` (historical behavior), never an invented
    budget for lack of a signal.

    ``mode`` (08/04): same scalping-dedicated threshold switch as
    ``conviction_size_multiplier`` -- see that function's own docstring."""
    if rr is None or align_score is None:
        return None
    conviction_threshold, moderate_threshold = _rr_thresholds(mode)
    if rr >= conviction_threshold and align_score >= CONVICTION_ALIGN_SCORE_THRESHOLD:
        weak_fundamentals = fundamental_score is not None and fundamental_score < FUNDAMENTAL_WEAK_THRESHOLD
        unconfirmed_volume = volume_confirmed is False
        weak_dex_security = dex_security_score is not None and dex_security_score < DEX_SECURITY_WEAK_THRESHOLD
        flags = int(weak_fundamentals) + int(unconfirmed_volume) + int(weak_dex_security)
        if flags >= 2:
            return CONVICTION_RISK_BUDGET_WEAK_PCT
        if flags == 1:
            return CONVICTION_RISK_BUDGET_MODERATE_PCT
        return CONVICTION_RISK_BUDGET_STRONG_PCT
    if rr >= moderate_threshold:
        return CONVICTION_RISK_BUDGET_MODERATE_PCT
    return CONVICTION_RISK_BUDGET_WEAK_PCT


# 07/23 -- performance-breakdown tracking (operator request: segment winrate/PnL
# by conviction tier to see which one actually performs). Same tiering and same
# stacking of the two vetoes as conviction_size_multiplier/conviction_risk_
# budget_pct above (identical branching, word for word) -- only the output
# changes: a stable string label ("strong"/"moderate"/"weak") to persist on the
# position, instead of a multiplier or a risk-budget fraction. Deliberately a
# 3rd mirror function rather than refactoring the two existing ones to share
# this branching: those are hot, already-tested paths on real capital sizing,
# never touched for a purely observational addition.
def conviction_tier_label(
    rr: float | None, align_score: int | None, *,
    fundamental_score: float | None = None, volume_confirmed: bool | None = None,
    dex_security_score: float | None = None, mode: str | None = None,
) -> str | None:
    """Conviction tier label for THIS signal -- ``None`` if ``rr``/``align_score``
    are missing (never an invented tier for lack of a signal, e.g. the old
    VC-thesis pilot).

    ``mode`` (08/04): same scalping-dedicated threshold switch as
    ``conviction_size_multiplier`` -- see that function's own docstring."""
    if rr is None or align_score is None:
        return None
    conviction_threshold, moderate_threshold = _rr_thresholds(mode)
    if rr >= conviction_threshold and align_score >= CONVICTION_ALIGN_SCORE_THRESHOLD:
        weak_fundamentals = fundamental_score is not None and fundamental_score < FUNDAMENTAL_WEAK_THRESHOLD
        unconfirmed_volume = volume_confirmed is False
        weak_dex_security = dex_security_score is not None and dex_security_score < DEX_SECURITY_WEAK_THRESHOLD
        flags = int(weak_fundamentals) + int(unconfirmed_volume) + int(weak_dex_security)
        if flags >= 2:
            return "weak"
        if flags == 1:
            return "moderate"
        return "strong"
    if rr >= moderate_threshold:
        return "moderate"
    return "weak"


def size_by_risk_budget(
    risk_budget_pct: float, trail_pct: float, capital_total: float, *, ceiling_usd: float | None = None,
) -> float:
    """Allocates ``risk_budget_pct * capital_total / trail_pct`` -- translates a $
    risk budget (how much one accepts to lose if the ATR trailing stop is hit) into a $
    allocation given the REAL stop width for THIS specific token. The wider the
    stop (nervous token), the more the allocation is reduced to maintain the
    same $ risk; the tighter it is (calm token), the more it can rise -- never a
    fixed % identical regardless of volatility.

    ``ceiling_usd`` (optional): absolute cap -- this mechanism never grows
    a position beyond this cap (typically the same historical maximum as
    the old fixed-tier system, e.g. 5% of capital), it only REDUCES it on
    setups where the stop is wide. Without it, no cap here (the caller is
    responsible for supplying one -- ``size_position_by_risk``, based on the Fibonacci
    invalidation and applied separately by the caller, remains the true final
    safety net on the LOSS, independent of this cap on the ALLOCATION).

    ``trail_pct``/``capital_total`` <= 0 -> 0.0 (never a division by zero, never
    an invented allocation)."""
    if trail_pct <= 0 or capital_total <= 0:
        return 0.0
    raw = risk_budget_pct * capital_total / trail_pct
    if ceiling_usd is not None:
        return min(raw, ceiling_usd)
    return raw


# 07/18 (continued, cross-review validated by the operator) -- DETERMINISTIC "hand
# brake", never an LLM: once the weekly target (+10%) has ALREADY been reached, NEW
# entries are halved rather than left at full size -- protects the gain
# already secured without ever cutting new entries to zero (the market doesn't know
# we've "made our week"; an exceptional, doubly-verified setup keeps a
# positive asymmetry, just with a reduced stake). Composed AFTER conviction_size_
# multiplier (8% -> 4%, 5% -> 2.5%), itself capped AFTERWARD by
# size_position_by_risk (2% max loss) -- never a bypass of the cap.
WEEKLY_PACING_DAMPENING_MULTIPLIER = 0.5


def weekly_pacing_size_multiplier(weekly_context: dict | None) -> float:
    """1.0 by default (unchanged behavior, including when ``weekly_context`` is absent
    or incomplete -- never a dampener without proof of context). ``WEEKLY_PACING_DAMPENING_
    MULTIPLIER`` ONLY when current equity has already reached/exceeded the week's
    target (``weekly_context["equity"] >= weekly_context["target_equity"]``)."""
    if not weekly_context:
        return 1.0
    equity = weekly_context.get("equity")
    target = weekly_context.get("target_equity")
    if equity is None or target is None:
        return 1.0
    if equity >= target:
        return WEEKLY_PACING_DAMPENING_MULTIPLIER
    return 1.0


# 07/20 -- dynamic Regime Switch (Gemini cross-review, explicit operator go-ahead
# at $200k in Fear regime): "Fear" halves risk budgets/conviction
# tiers -- preserves capital when liquidity clusters into big assets
# and micro-caps collapse one after another. Composed exactly
# like ``weekly_pacing_size_multiplier`` above (same call site, multiplied onto
# the final allocation) -- never integrated into ``conviction_size_multiplier``/
# ``conviction_risk_budget_pct`` themselves, which remain PURE functions on the
# technical signal alone, independent of the macro regime (separation of concerns
# already established between these layers).
REGIME_FEAR_SIZE_MULTIPLIER = 0.5


def regime_size_multiplier(regime: str | None) -> float:
    """1.0 by default (Neutral/Euphoria/unknown -- unchanged behavior). ``REGIME_
    FEAR_SIZE_MULTIPLIER`` ONLY in confirmed Fear regime -- never a dampener without a
    signal (``None``/absent regime -> 1.0, same fail-open doctrine as ``weekly_
    pacing_size_multiplier`` on an absent ``weekly_context``)."""
    from aria_core.skills.market_sentiment import META_REGIME_FEAR

    if regime == META_REGIME_FEAR:
        return REGIME_FEAR_SIZE_MULTIPLIER
    return 1.0


# 07/20 -- #174: Formula B sizing (VC-thesis, ``vc_analysis.VCResult.taille_pct``,
# 0-10% of capital already clamped at the source by ``MAX_POSITION_SIZE_PCT``). This path
# has neither ``rr`` nor ``align_score`` (rich LLM judgment, no deterministic thresholds) --
# which is precisely why ``conviction_size_multiplier``/``conviction_risk_budget_
# pct`` above, called with these two values at ``None``, would silently degrade
# toward the MAX cap (5% flat) for ANY vc_thesis position, regardless of what the
# LLM had actually judged (0 to 10%). Bound deliberately duplicated (not a cross-
# module import into ``vc_analysis`` -- risk_guard remains a low-level, pure module, with no
# dependency on skills).
VC_THESIS_MAX_TAILLE_PCT = 10.0


def vc_thesis_alloc_usd(taille_pct: float | None, capital_total: float) -> float | None:
    """Allocates ``taille_pct`` % of total capital for a Formula B (VC-thesis) position
    -- ``None`` if ``taille_pct`` is absent/zero/negative, signaling to the caller to fall
    back on the conviction-tier system above (historical behavior, unchanged
    for momentum which never supplies this field)."""
    if taille_pct is None or taille_pct <= 0:
        return None
    bounded = max(0.0, min(taille_pct, VC_THESIS_MAX_TAILLE_PCT))
    return capital_total * bounded / 100.0


# 07/19 -- position cap auto-calibrated by PRICE IMPACT (Gemini cross-review,
# relayed by the operator, 07/19). Replaces the debate over "what fixed % of the pool"
# with a calculation that auto-adjusts to EVERY real pool, without a new arbitrary size
# threshold to choose. Until now, nothing capped a position based on the REAL liquidity
# of the targeted pool (only an absolute floor exists, ``momentum_entry._MIN_LIQUIDITY_USD``)
# -- an order too big for a thin pool artificially moves the price (ARIA would create its
# own "price impact"), a reality paper-trading didn't model.
#
# Principle (standard AMM approximation, cited by Gemini): an order representing X% of
# the pool's total liquidity produces roughly 2*X% price impact on a
# balanced (constant-product, x*y=k) pool. This function DEGRADES the entry price by this
# estimated impact, recomputes the structural R/R (target/invalidation remain fixed
# Fibonacci/RSI levels, independent of order size) with this degraded price, and
# reduces ``alloc_usd`` (closed-form solution, no iteration) until the degraded R/R
# comes back to at least ``PRICE_IMPACT_MIN_RR`` -- deliberately a FIXED floor and not
# the trade's own raw R/R (a path considered then discarded by the math: a very high
# raw R/R would make the floor nearly unreachable at ANY size -- because
# any positive impact strictly lowers the R/R below its own starting value -- the
# opposite of the intended effect: a stronger signal should tolerate MORE
# size, not less).
PRICE_IMPACT_RATIO = 2.0  # standard AMM rule: X% of the pool -> ~2*X% price impact
# Deliberately reuses the same value as ``momentum_entry._RR_AMBIGUOUS_FLOOR`` (minimum
# structural R/R for a signal to even be considered a buy)
# WITHOUT importing that module -- same autonomy doctrine already applied to
# ``CONVICTION_RR_THRESHOLD``/``MODERATE_RR_THRESHOLD`` above (independent
# constant, never a cross-module import).
PRICE_IMPACT_MIN_RR = 1.0

# 08/02 -- real problem found live (2-agent audit + adversarial verify
# workflow, operator go-ahead to fix): scalping's ATR-based stops are
# structurally MUCH tighter (1.5-2.0x ATR, often just a few % of price) than
# swing/vc's Fibonacci/golden-pocket stops -- on a tight scalping setup, the
# margin between the trade's own raw R/R (e.g. V2's 1.5, V1/V3's 2.0) and
# this floor (1.0) is thin enough that the mandatory 1% scalping swap fee
# ALONE (``apply_swap_fee=True`` below) can already push the degraded R/R
# through the floor -- confirmed on real prod data: scalping_v2 never once
# opened a position (0/4 real signals), v1/v3 opened positions sized down
# to $50-$3,600 instead of the $20,000 the conviction tier intended. A
# LOWER floor for scalping specifically gives tight-stop setups the margin
# to survive the swap fee without weakening the guardrail's actual purpose
# (still fully active, still fail-closed on a bad setup) for swing/vc, whose
# wider stops never needed this margin in the first place. First pass, not
# yet calibrated against a large sample of real scalping fills -- revisit
# once more data accumulates (same "first pass" doctrine as
# DEX_QUALITY_WATCH_THRESHOLD above).
PRICE_IMPACT_MIN_RR_SCALPING = 0.5

# 30/07 -- real bug found live (CFI, a real limit order): a candidate's raw R/R
# (11.9) cleared every floor by a wide margin, but the pool's liquidity was only
# $60k -- ``cap_alloc_to_price_impact`` above degraded the size down to a ~2%
# stake purely to keep the R/R at its own PRICE_IMPACT_MIN_RR floor (1.0), which
# is LOWER than the swing pocket's own entry floor at the time (2.0,
# limit_orders.py's ``_RR_MIN_LIMIT_ORDER_SWING`` -- removed, restored, then
# removed again the same week, Items #245/#248/#252) -- a trade that had
# just cleared a real R/R bar was silently re-sized down to a WORSE one.
# That fix alone doesn't
# bound the outright SIZE relative to the pool -- it only protects the R/R
# ratio, and only when target/invalidation are BOTH known (fail-open
# otherwise, see that function's own docstring). This is the missing hard cap,
# independent of R/R: never let an order represent more than this fraction of
# the pool's own liquidity, so a candidate with no computable structural R/R
# yet still gets a sane ceiling. Chosen so the resulting price impact
# (PRICE_IMPACT_RATIO * this) tops out at ~2% -- disproportionate market
# impact even on a "textbook" setup was the whole point of this fix.
MAX_ALLOC_PCT_OF_POOL_LIQUIDITY = 0.01


def _price_impact_pct(alloc_usd: float, pool_liquidity_usd: float) -> float:
    """Estimated price impact (fraction) of an order of ``alloc_usd`` on a pool of
    ``pool_liquidity_usd`` -- standard AMM rule (``PRICE_IMPACT_RATIO``), extracted here
    to be reused identically by ``cap_alloc_to_price_impact`` (sizing)
    AND ``simulated_fill_price`` (#175, real fill price) -- never a second
    diverging calculation between the two."""
    return PRICE_IMPACT_RATIO * (alloc_usd / pool_liquidity_usd)


def cap_alloc_to_price_impact(
    alloc_usd: float, entry_price: float, target_price: float | None,
    invalidation_price: float | None, pool_liquidity_usd: float | None,
    *, apply_swap_fee: bool = False, min_rr: float = PRICE_IMPACT_MIN_RR,
) -> float:
    """Reduces ``alloc_usd`` if the price impact of THIS order on THIS pool would drop the
    structural R/R below ``min_rr`` -- never an increase beyond the entry
    value (same doctrine as ``size_position_by_risk``). May return ``0.0`` (no
    viable size, even infinitesimal, on this pool with this trade structure).
    Missing/inconsistent data (target, invalidation, or liquidity absent, or a
    non-bullish structure) -> unchanged, fail-open -- the hard guardrail on pool
    liquidity already lives in ``momentum_entry._MIN_LIQUIDITY_USD``, that's not the role of
    this function.

    ``apply_swap_fee`` (08/01, real bug found live -- operator caught a scalping
    position, PLAY, sized down to $277 with a FINAL structural R/R of 0.067,
    far below this function's own PRICE_IMPACT_MIN_RR=1.0 floor): this cap used
    to compute its degraded-price ceiling from raw ``entry_price`` alone, never
    accounting for ``simulated_fill_price``'s own ``DEX_SWAP_FEE_PCT`` (1%,
    scalping mode) applied moments later on the SAME allocation -- on an
    already-tight setup, that unanticipated 1% was enough to blow through the
    R/R floor this function is supposed to guarantee. Must be called with the
    SAME ``apply_swap_fee`` value as the ``simulated_fill_price`` call for the
    same order (``paper_trader.open_position``), never independently decided.

    ``min_rr`` (08/02, real problem found live, see PRICE_IMPACT_MIN_RR_SCALPING's
    own comment): defaults to the module-level PRICE_IMPACT_MIN_RR (1.0,
    unchanged behavior for swing/vc, whose wider Fibonacci/golden-pocket stops
    never needed a lower floor) -- the caller passes a lower value for
    scalping's tighter ATR stops, which otherwise get crushed by the mandatory
    1% swap fee alone."""
    if alloc_usd <= 0 or entry_price <= 0:
        return alloc_usd
    if not pool_liquidity_usd or pool_liquidity_usd <= 0:
        return alloc_usd
    if not target_price or not invalidation_price:
        return alloc_usd
    if target_price <= entry_price or invalidation_price >= entry_price:
        return alloc_usd  # non-bullish structure -- not the role of this function

    # Same fee-then-impact order as simulated_fill_price -- the fee is a FLAT
    # multiplier applied before the size-dependent impact, never the reverse.
    fee_adjusted_entry = entry_price * (1.0 + DEX_SWAP_FEE_PCT) if apply_swap_fee else entry_price

    degraded_entry = fee_adjusted_entry * (1.0 + _price_impact_pct(alloc_usd, pool_liquidity_usd))
    if degraded_entry < target_price:
        degraded_rr = (target_price - degraded_entry) / (degraded_entry - invalidation_price)
        if degraded_rr >= min_rr:
            return alloc_usd  # negligible impact at this size, nothing to reduce

    # Closed-form solution: exact degraded entry price for which R/R == min_rr
    # (derived from (target - e) / (e - invalidation) = min_rr), then worked back
    # to the allocation that produces this degraded price (impact_pct linear in alloc_usd).
    target_degraded_entry = (target_price + min_rr * invalidation_price) / (1.0 + min_rr)
    if target_degraded_entry <= fee_adjusted_entry:
        return 0.0  # even an infinitesimal size wouldn't meet this floor here (fee alone breaches it)

    k = PRICE_IMPACT_RATIO / pool_liquidity_usd
    capped_alloc = (target_degraded_entry / fee_adjusted_entry - 1.0) / k
    return max(0.0, min(alloc_usd, capped_alloc))


def cap_alloc_to_pool_share(alloc_usd: float, pool_liquidity_usd: float | None) -> float:
    """Hard cap on the order's own size as a fraction of the pool's REAL
    liquidity (``MAX_ALLOC_PCT_OF_POOL_LIQUIDITY``) -- independent of, and IN
    ADDITION TO, ``cap_alloc_to_price_impact`` above.

    Why a separate function rather than folding this into that one: that
    function only ever activates when target_price/invalidation_price are
    BOTH known (fail-open otherwise, by its own docstring), and its floor is
    the trade's R/R ratio, not its outright size -- a candidate with no
    computable structural R/R yet (or one whose R/R floor is looser than this
    pocket's own entry bar, the exact CFI bug this fix closes) would sail
    through it untouched. This one only needs ``pool_liquidity_usd`` to be
    known, and caps the dollar amount directly.

    Never an increase beyond the entry ``alloc_usd`` (same doctrine as every
    other cap in this module). Missing/non-positive liquidity -> unchanged,
    fail-open -- the hard liquidity FLOOR for a candidate to even be
    considered already lives in ``momentum_entry._MIN_LIQUIDITY_USD``; this
    caps the ORDER size, never the pool's eligibility."""
    if alloc_usd <= 0 or not pool_liquidity_usd or pool_liquidity_usd <= 0:
        return alloc_usd
    return min(alloc_usd, MAX_ALLOC_PCT_OF_POOL_LIQUIDITY * pool_liquidity_usd)


# 07/20 -- #175: ``cap_alloc_to_price_impact`` above already computes a ``degraded_
# entry`` internally to SIZE the position (reduce ``alloc_usd`` if needed),
# but never returns it -- once the size is set, ``open_position`` was still filling
# the position at the EXACT quoted spot price, never at the price actually "paid" by an
# order of this size on this pool. This function closes the gap: same impact
# model (``_price_impact_pct``, never a second diverging calculation), applied to the
# simulated FILL price rather than to size -- called separately by ``paper_trader.
# open_position`` on the FINAL allocation (after ALL reductions -- risk/impact/
# concentration), never the intermediate allocation from ``cap_alloc_to_price_impact``, which
# may have been further reduced since. ``target_price``/``invalidation_price`` never
# move: these are technical chart levels external to us (Fibonacci/RSI),
# our own order doesn't move the support/resistance, only the price WE
# pay.
# Item #101 (26/07), operator request ("verifie [les frais/slippage cumules]"
# on the scalping test): a REAL DEX swap fee, distinct from and IN ADDITION TO
# the price-impact model above -- a protocol-level cost taken on every swap
# regardless of order size, never modeled here until now. Sourced (never
# guessed): Uniswap v3 fee tiers are 1% / 0.3% / 0.05% / 0.01%
# (docs.uniswap.org/support.uniswap.org) -- the lowest tiers are reserved for
# stable/blue-chip pairs; a new/volatile memecoin pair (ARIA's typical
# scalping target) almost always sits in the highest 1% tier. Research (26/07
# workflow) found this cost genuinely material at scalping frequency: 100
# trades/day at a few dollars each in fees alone adds up to hundreds of
# $/day -- ignoring it would make the paper-trading result artificially
# optimistic versus a future real-capital deployment. Deliberately scoped to
# ``apply_swap_fee=True`` callers only (never the default) -- the standard
# swing path is left byte-for-byte unchanged so the already-running Milly
# test's historical behavior/results are never altered retroactively; only
# NEW scalping-mode callers opt in explicitly.
# 08/05 -- 0.01 -> 0.003 (operator-validated after the TIG fill investigation:
# entry displayed +3.02% above spot, reproduced to the cent as spot x 1.01 fee
# x 1.02 impact). The 1% flat fee assumed every scalping target sits in the
# highest fee tier; the pools v8/v6 actually pick (TIG/FAI/SAPIEN -- Aerodrome
# USDC pairs, ~$1M liquidity) are typically 0.05-0.3% tiers. 0.3% keeps the
# simulation conservative (top of the realistic Base range) without the old
# ~6% simulated round-trip friction that structurally crushed scalping
# targets of +2-5%. Price impact itself (PRICE_IMPACT_RATIO) is untouched --
# a large order on a thin pool IS expensive, that part protects us from
# paper-trading over-optimism. Future lever (separate chantier): read the
# pool's REAL fee tier at entry instead of any flat figure.
DEX_SWAP_FEE_PCT = 0.003


def simulated_fill_price(
    entry_price: float, alloc_usd: float, pool_liquidity_usd: float | None,
    *, apply_swap_fee: bool = False,
) -> float:
    """Simulated REAL fill price for a buy of ``alloc_usd`` on a pool of
    ``pool_liquidity_usd`` -- always >= ``entry_price`` (a buy pushes the price
    up, never down). Missing/invalid data (alloc/price zero,
    unknown pool liquidity) -> ``entry_price`` unchanged, fail-open -- same doctrine
    as ``cap_alloc_to_price_impact`` (the hard guardrail on liquidity lives in
    ``momentum_entry._MIN_LIQUIDITY_USD``, not here).

    ``apply_swap_fee`` (Item #101, 26/07, default ``False`` = unchanged
    behavior): adds ``DEX_SWAP_FEE_PCT`` on top of the price-impact model --
    see its comment. Applied even without a known ``pool_liquidity_usd``
    (a real protocol fee is charged regardless of whether impact is
    computable)."""
    if entry_price <= 0 or alloc_usd <= 0:
        return entry_price
    price = entry_price * (1.0 + DEX_SWAP_FEE_PCT) if apply_swap_fee else entry_price
    if not pool_liquidity_usd or pool_liquidity_usd <= 0:
        return price
    return price * (1.0 + _price_impact_pct(alloc_usd, pool_liquidity_usd))


def simulated_exit_price(
    current_price: float, position_value_usd: float, pool_liquidity_usd: float | None,
    *, apply_swap_fee: bool = False,
) -> float:
    """Simulated REAL exit price for a sale of ``position_value_usd`` on a pool of
    ``pool_liquidity_usd`` -- always <= ``current_price`` (a sale pushes the price
    down, never up). Symmetric to ``simulated_fill_price`` (buy), same
    impact formula (``_price_impact_pct``), never a second diverging calculation.

    07/22 -- item #18 (stress-test): the displayed PnL of an OPEN position used the
    exact spot price, as if its size could always be liquidated with zero
    slippage -- a fictitious x50 was possible on a pool that had become thin. Missing/
    invalid data -> ``current_price`` unchanged, fail-open (same doctrine as
    ``simulated_fill_price``).

    ``apply_swap_fee`` (Item #101, 26/07, default ``False`` = unchanged
    behavior): symmetric to ``simulated_fill_price``'s -- see ``DEX_SWAP_FEE_PCT``."""
    if current_price <= 0 or position_value_usd <= 0:
        return current_price
    price = current_price * (1.0 - DEX_SWAP_FEE_PCT) if apply_swap_fee else current_price
    if not pool_liquidity_usd or pool_liquidity_usd <= 0:
        return price
    return price * max(0.0, 1.0 - _price_impact_pct(position_value_usd, pool_liquidity_usd))


# ── 2. Per-pocket portfolio circuit breaker (persisted state, ONE dedicated
# file PER POCKET) ──────────────────────────────────────────────────────────

SOFT_DRAWDOWN_PCT = 0.10       # -10% from equity high -> alloc halved
HARD_DRAWDOWN_PCT = 0.20       # -20% from the high -> blocks any new entry
HARD_CONSECUTIVE_LOSSES = 5    # 5 consecutive losses -> also blocks any new entry
SOFT_ALLOC_MULTIPLIER = 0.5

_BAND_NONE = "none"
_BAND_SOFT = "soft"
_BAND_HARD = "hard"

# 27/07 -- 3-pocket architecture plan, Phase 3 (real security gap closed --
# see the module docstring's item 2). ``wallet`` below is a MANDATORY,
# no-default parameter on every function in this section: it deliberately
# forces every caller (including every existing test) to consciously pick
# WHICH of the 3 independent $1M pockets (scalping/swing/vc) it's reading/
# arming/resuming, rather than silently inheriting a stale implicit
# "swing"/shared default -- exactly the class of bug this chantier fixes
# (before it, a single unscoped circuit-breaker check made a drawdown on ONE
# pocket alone block new entries in ALL 3).


def _state_path(wallet: str) -> Path:
    return data_dir() / f"risk_guard_state_{wallet}.json"


def _read_raw(wallet: str) -> dict[str, Any] | None:
    """Same three-state semantics as ``outgoing_pause._read_raw``:
    ``{}`` (file absent -- never triggered, not a doubt), ``dict``
    (read correctly), ``None`` (corrupted -- UNKNOWN state)."""
    path = _state_path(wallet)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        logger.warning("risk_guard_state[%s] unreadable/corrupted (%s) -- UNKNOWN state", wallet, exc)
        return None
    if not isinstance(raw, dict):
        logger.warning(
            "risk_guard_state[%s] has unexpected shape (%r) -- UNKNOWN state", wallet, type(raw).__name__,
        )
        return None
    return raw


def _write(wallet: str, payload: dict[str, Any]) -> None:
    path = _state_path(wallet)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def migrate_wallet_state(old_wallet: str, new_wallet: str) -> bool:
    """08/01 -- one-off migration helper (legacy "scalping" pocket folded
    into "scalping_v6", see paper_trader.build_scalping_pocket_entries's own
    docstring): moves ``old_wallet``'s circuit-breaker state file to
    ``new_wallet`` -- a pocket's block/resume history is real data (who
    armed it, when, why), never silently dropped on a rename.

    Fails safe: no-op (returns ``False``) if ``old_wallet`` has no state
    file (nothing to migrate), or if ``new_wallet`` ALREADY has one (never
    overwrites existing state -- a rename target that already exists means
    this was already run, or the destination pocket has its own real
    history that must not be clobbered)."""
    old_path = _state_path(old_wallet)
    new_path = _state_path(new_wallet)
    if not old_path.exists():
        return False
    if new_path.exists():
        logger.warning(
            "risk_guard.migrate_wallet_state: %s already has a state file -- refusing to overwrite, "
            "leaving %s in place", new_wallet, old_wallet,
        )
        return False
    new_path.parent.mkdir(parents=True, exist_ok=True)
    os.replace(old_path, new_path)
    return True


def new_entry_block_status(wallet: str) -> dict[str, Any]:
    """Current state of THIS POCKET's dedicated circuit breaker (never
    ``outgoing_pause``, never another pocket's own file):
    ``{blocked, since, reason, by, last_alert_band, readable}``.
    ``readable=False`` signals a corrupted file -- fail-closed on the
    caller's side (``blocks_new_entries``), same "money" doctrine as
    ``outgoing_pause.money_block_reason``."""
    raw = _read_raw(wallet)
    readable = raw is not None
    data = raw or {}
    since: datetime | None = None
    since_raw = data.get("since")
    if isinstance(since_raw, str):
        try:
            since = datetime.fromisoformat(since_raw.replace("Z", "+00:00"))
            if since.tzinfo is None:
                since = since.replace(tzinfo=timezone.utc)
        except ValueError:
            since = None
    last_reminder_at: datetime | None = None
    last_reminder_raw = data.get("last_reminder_at")
    if isinstance(last_reminder_raw, str):
        try:
            last_reminder_at = datetime.fromisoformat(last_reminder_raw.replace("Z", "+00:00"))
            if last_reminder_at.tzinfo is None:
                last_reminder_at = last_reminder_at.replace(tzinfo=timezone.utc)
        except ValueError:
            last_reminder_at = None
    return {
        "blocked": bool(data.get("blocked")),
        "since": since,
        "by": data.get("by"),
        "reason": data.get("reason") or "",
        "last_alert_band": data.get("last_alert_band") or _BAND_NONE,
        "readable": readable,
        "last_reminder_at": last_reminder_at,
    }


def block_new_entries(wallet: str, reason: str, *, by: int | str | None = None) -> dict[str, Any]:
    """Arms the hard tier for THIS POCKET ONLY: no more NEW paper positions in
    this pocket until ``resume_new_entries(wallet, ...)`` has been called
    explicitly for it (never automatic -- see the module docstring). The
    other 2 pockets are entirely unaffected -- separate state file each."""
    _write(
        wallet,
        {
            "blocked": True,
            "since": datetime.now(timezone.utc).isoformat(),
            "by": by,
            "reason": (reason or "").strip(),
            "last_alert_band": _BAND_HARD,
        },
    )
    logger.warning("risk_guard[%s]: circuit breaker ARMED (hard tier) -- reason=%s", wallet, reason)
    return new_entry_block_status(wallet)


def resume_new_entries(wallet: str, *, by: int | str | None = None) -> dict[str, Any]:
    """Lifts THIS POCKET's circuit breaker. NEVER called automatically by
    ``evaluate_portfolio_risk`` -- reserved for an explicit human action
    (e.g. operator command) or this pocket's own weekly reset, even if the
    drawdown has since recovered. Never touches the other 2 pockets' files."""
    _write(
        wallet,
        {
            "blocked": False,
            "since": None,
            "by": by,
            "reason": "",
            "last_alert_band": _BAND_NONE,
            "resumed_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    logger.warning("risk_guard[%s]: circuit breaker LIFTED (manual resume) -- by=%s", wallet, by)
    return new_entry_block_status(wallet)


def paper_risk_circuit_breakers_disabled() -> bool:
    """08/02 -- operator explicit call, live incident: "les coupe circuit ne
    servent à rien à paper test puisque on améliore et reconstruit en temps
    réel ... tu peux les supprimer". Scoped to the automated PAPER (fictional
    capital) risk circuit breakers ONLY -- the per-pocket drawdown/consecutive-
    loss breaker (this module) and the per-contract re-entry cooldown
    (paper_trader.SCALPING_MAX_CONSECUTIVE_LOSSES_PER_CONTRACT/
    MAX_CONSECUTIVE_LOSSES_PER_CONTRACT). Deliberately does NOT touch
    ``outgoing_pause`` (the real manual kill-switch, /stop) -- that one is a
    human decision, never an automated one, and stays fully active regardless
    of this flag. Also never touches any hard security gate (honeypot,
    blacklist, holder concentration, liquidity floor) -- those protect
    against buying a scam, not against a losing streak, and the operator's
    own reasoning ("on améliore et reconstruit en temps réel") applies only
    to risk-management circuit breakers, never to fraud detection.

    OFF by default (fail-closed, same idiom as every other gate in this
    file) so a future deploy that forgets to set this env var gets the safe
    behavior (breakers active) -- flip ON explicitly, same doctrine as every
    other "temporarily loosen a guardrail for observation" gate already in
    this codebase (see paper_trader.scalping_only_sourcing_enabled's own
    comment for the same idiom). MUST be revisited before any real-capital
    transition -- the day capital becomes real, CLAUDE.md's absolute rule
    on human validation applies in full, unconditionally, and this flag
    (scoped to fictional paper capital only) has no bearing on that."""
    return os.environ.get("ARIA_PAPER_RISK_CIRCUIT_BREAKERS_DISABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def blocks_new_entries(wallet: str) -> tuple[bool, str | None]:
    """``(blocked, reason)`` -- combines THIS POCKET's dedicated circuit
    breaker AND ``outgoing_pause`` (a global pause blocks new paper entries
    in EVERY pocket at once -- the real kill-switch stays global by
    construction, unlike the per-pocket breaker) WITHOUT ever confusing the
    two mechanisms in the reported reason. Fail-closed on unreadable state
    ("money" doctrine).

    08/02 -- see paper_risk_circuit_breakers_disabled()'s own docstring: when
    ON, the per-pocket breaker below is skipped entirely -- outgoing_pause
    (the manual kill-switch) is checked FIRST and always still applies,
    regardless of this flag."""
    from aria_core import outgoing_pause

    if outgoing_pause.is_paused():
        return True, "ARIA en pause globale (kill-switch sortant) — aucune nouvelle position paper tant que /start n'est pas donné."

    if paper_risk_circuit_breakers_disabled():
        return False, None

    status = new_entry_block_status(wallet)
    if not status["readable"]:
        return True, f"état du coupe-circuit portefeuille (poche {wallet}) illisible/corrompu — fail-closed par sécurité"
    if status["blocked"]:
        return True, status["reason"] or f"coupe-circuit portefeuille (poche {wallet}) armé — reprise manuelle requise"
    return False, None


@dataclass
class PortfolioRiskState:
    wallet: str                     # which of the 3 pockets this snapshot describes
    equity: float
    high_water_mark: float
    drawdown_pct: float             # 0..1 from the high
    consecutive_losses: int
    alloc_multiplier: float         # 1.0 normal, SOFT_ALLOC_MULTIPLIER if soft tier
    blocked: bool
    blocked_reason: str | None = None
    newly_triggered_soft: bool = False
    newly_triggered_hard: bool = False


async def evaluate_portfolio_risk(wallet: str, *, price_lookup=None) -> PortfolioRiskState:
    """Snapshot of THIS POCKET's risk -- to be called once per cycle PER
    POCKET, before any attempt to open a new position in THIS pocket (never
    before managing already-open positions, which must continue normally
    even with this pocket's circuit breaker armed). Updates THIS pocket's
    persisted equity high-water mark and arms THIS pocket's dedicated
    circuit breaker if a hard tier is crossed for the first time --
    entirely independent of the other 2 pockets' own state (27/07, Phase 3:
    the real gap this closes -- a scalping-only drawdown/losing streak must
    never block swing/vc, and vice versa)."""
    from aria_core import paper_trader

    summary = await paper_trader.portfolio_summary(wallet=wallet, price_lookup=price_lookup)
    equity = float(summary["equity"])

    hwm = await paper_trader.get_equity_high_water_mark(wallet=wallet)
    if equity > hwm:
        hwm = equity
        await paper_trader.set_equity_high_water_mark(hwm, wallet=wallet)
    drawdown_pct = max(0.0, (hwm - equity) / hwm) if hwm > 0 else 0.0

    # 27/07 -- scoped to THIS pocket: without ``wallet=`` here, a losing streak
    # in scalping and an unrelated losing streak in vc would be counted
    # TOGETHER toward a single shared consecutive-loss counter -- the second,
    # confirmed cross-pocket bug this chantier fixes (the first being the
    # early-return bug in the callers, see paper_trader.py). Each pocket now
    # has its own genuinely independent streak.
    closed = await paper_trader.get_closed_positions(limit=HARD_CONSECUTIVE_LOSSES, wallet=wallet)
    consecutive_losses = 0
    for p in closed:
        if (p.get("pnl_usd") or 0.0) < 0:
            consecutive_losses += 1
        else:
            break

    status = new_entry_block_status(wallet)
    already_blocked = status["blocked"]
    hard_breach = drawdown_pct >= HARD_DRAWDOWN_PCT or consecutive_losses >= HARD_CONSECUTIVE_LOSSES
    newly_triggered_hard = False
    # 08/02 -- see paper_risk_circuit_breakers_disabled()'s own docstring: not
    # just "ignore an armed breaker" (blocks_new_entries already does that) --
    # skip ARMING it in the first place, so the hourly "still armed" reminder
    # and the ARMED/LIFTED Telegram alerts stop firing too. The soft-tier
    # alert/sizing-reduction below is deliberately left untouched: it never
    # blocks a trade, only informs and trims size, so it doesn't fall under
    # the operator's "circuit breaker" framing.
    if hard_breach and not already_blocked and status["readable"] and not paper_risk_circuit_breakers_disabled():
        reason = (
            f"drawdown {drawdown_pct:.1%} depuis le plus haut d'équité ({hwm:,.0f} $)"
            if drawdown_pct >= HARD_DRAWDOWN_PCT
            else f"{consecutive_losses} pertes consécutives"
        )
        block_new_entries(wallet, reason)
        newly_triggered_hard = True
        already_blocked = True

    soft_breach = SOFT_DRAWDOWN_PCT <= drawdown_pct < HARD_DRAWDOWN_PCT
    newly_triggered_soft = False
    if not already_blocked:
        last_band = status["last_alert_band"]
        if soft_breach and last_band != _BAND_SOFT:
            _write(
                wallet,
                {
                    "blocked": False,
                    "since": None,
                    "by": None,
                    "reason": "",
                    "last_alert_band": _BAND_SOFT,
                },
            )
            newly_triggered_soft = True
        elif not soft_breach and last_band == _BAND_SOFT:
            _write(wallet, {"blocked": False, "since": None, "by": None, "reason": "", "last_alert_band": _BAND_NONE})

    blocked, blocked_reason = blocks_new_entries(wallet)
    alloc_multiplier = SOFT_ALLOC_MULTIPLIER if (soft_breach and not blocked) else 1.0

    return PortfolioRiskState(
        wallet=wallet,
        equity=equity,
        high_water_mark=hwm,
        drawdown_pct=drawdown_pct,
        consecutive_losses=consecutive_losses,
        alloc_multiplier=alloc_multiplier,
        blocked=blocked,
        blocked_reason=blocked_reason,
        newly_triggered_soft=newly_triggered_soft,
        newly_triggered_hard=newly_triggered_hard,
    )


def format_soft_drawdown_alert(state: PortfolioRiskState, wallet: str) -> str:
    pocket_label = wallet.upper()
    return "\n".join([
        f"🧪 SIMULATION — coupe-circuit portefeuille (poche {pocket_label}, palier SOUPLE)",
        f"Drawdown {state.drawdown_pct:.1%} depuis le plus haut d'équité ({state.high_water_mark:,.0f} $).",
        f"Allocation des NOUVELLES entrées de la poche {pocket_label} réduite de moitié "
        f"(×{SOFT_ALLOC_MULTIPLIER}) jusqu'à résorption.",
        "Positions déjà ouvertes (toutes poches) : gérées normalement (stop suiveur/prise de profit).",
        "Aucun argent réel.",
    ])


def format_hard_circuit_breaker_alert(state: PortfolioRiskState, wallet: str) -> str:
    pocket_label = wallet.upper()
    return "\n".join([
        f"🧪 SIMULATION — coupe-circuit portefeuille (poche {pocket_label}, palier DUR)",
        f"{state.blocked_reason or 'seuil de risque franchi'}.",
        f"Toute NOUVELLE position paper dans la poche {pocket_label} est bloquée jusqu'à reprise manuelle explicite.",
        "Les 2 autres poches et les positions déjà ouvertes (toutes poches) : gérées normalement "
        "(stop suiveur/prise de profit) — rien n'est fermé de force.",
        "Reprise : action humaine explicite requise, jamais automatique.",
        "Aucun argent réel.",
    ])


# ── 2bis. Rappel horaire tant qu'un coupe-circuit DUR reste armé ────────────
# 31/07, demande opérateur explicite ("si aria arrête de trader ... je veut
# une notification dans telegram toute les heures tant que j'ai pas traité le
# problème"). ``newly_triggered_hard`` ci-dessus n'alerte QU'À LA TRANSITION
# (armement initial) -- sans ce mécanisme, un coupe-circuit resté armé
# plusieurs heures/jours (reprise manuelle jamais donnée) redevient
# silencieux après la toute première alerte. Générique : réutilisé identique
# pour le macro breaker plus bas (mêmes fonctions, un ``wallet`` différent
# suffit à cibler le bon fichier d'état -- jamais une deuxième copie).
REMINDER_INTERVAL_SECONDS = 3600.0  # 1h, valeur explicite de l'opérateur


def should_send_pocket_reminder(wallet: str) -> bool:
    """True si le coupe-circuit DUR de cette poche est actuellement armé ET
    qu'aucun rappel n'a été envoyé depuis au moins ``REMINDER_INTERVAL_
    SECONDS``. Jamais vrai pour le palier SOUPLE (celui-ci n'empêche aucune
    entrée, seulement une taille réduite -- pas la situation "ARIA arrête de
    trader" que ce rappel cible).

    08/02 -- voir le docstring de paper_risk_circuit_breakers_disabled() : un
    fichier d'état déjà armé AVANT l'activation de ce gate resterait
    "blocked" indéfiniment (ce gate empêche seulement un NOUVEL armement, pas
    l'état déjà persisté) -- sans ce garde, le rappel horaire continuerait
    d'alerter sur un coupe-circuit qui n'a plus aucun effet réel."""
    if paper_risk_circuit_breakers_disabled():
        return False
    status = new_entry_block_status(wallet)
    if not status["readable"] or not status["blocked"]:
        return False
    last_reminder_at = status["last_reminder_at"]
    if last_reminder_at is None:
        return True
    elapsed = (datetime.now(timezone.utc) - last_reminder_at).total_seconds()
    return elapsed >= REMINDER_INTERVAL_SECONDS


def record_pocket_reminder_sent(wallet: str) -> None:
    """Marque l'instant du rappel envoyé -- ne touche à AUCUN autre champ du
    fichier d'état de cette poche (préserve ``since``/``reason``/``by`` tels
    quels, jamais un ré-armement implicite)."""
    raw = _read_raw(wallet)
    if raw is None:
        return  # état illisible -- rien à corrompre davantage
    payload = dict(raw)
    payload["last_reminder_at"] = datetime.now(timezone.utc).isoformat()
    _write(wallet, payload)


def format_pocket_blocked_reminder_alert(status: dict[str, Any], wallet: str) -> str:
    pocket_label = wallet.upper()
    since = status.get("since")
    since_txt = since.strftime("%Y-%m-%d %H:%M UTC") if since else "date inconnue"
    return "\n".join([
        f"⏰ RAPPEL — coupe-circuit portefeuille (poche {pocket_label}) toujours ARMÉ",
        f"Armé depuis {since_txt} : {status.get('reason') or 'seuil de risque franchi'}.",
        f"Aucune nouvelle position paper dans la poche {pocket_label} tant que la reprise "
        "manuelle n'est pas donnée (/riskresume).",
        "Ce rappel se répète toutes les heures tant que le coupe-circuit reste armé.",
        "Aucun argent réel.",
    ])


# ── 3. MACRO circuit breaker (aggregated across all 3 pockets) ─────────────
# 27/07 -- 3-pocket architecture plan, Phase 3 ("Ajout de robustesse retenu",
# operator-approved). The 3 per-pocket breakers above are now fully
# independent -- correct BY DESIGN (a scalping-only drawdown must never block
# swing/vc) -- but that independence opens a new blind spot: a genuinely
# CORRELATED, portfolio-wide crash (all 3 pockets losing together, e.g. a
# broad crypto-market crash) could see each pocket sit just under its OWN
# hard threshold while the combined book has, in truth, capitulated. This
# mechanism is the backstop for exactly that scenario -- deliberately
# coarser (one aggregate % drawdown against a dedicated MACRO high-water
# mark, its own persisted file, never one of the 3 per-pocket files) and
# deliberately more drastic on trigger: it arms ``outgoing_pause`` itself,
# the REAL global kill-switch (tweets/X replies/ACP spend/scheduled jobs --
# not just these 3 paper pockets, see outgoing_pause.py's own docstring).
# This module still NEVER reimplements/modifies outgoing_pause.py -- it only
# ever CALLS it, exactly like any other caller (module docstring's "never
# recoded" doctrine, unchanged).
MACRO_CIRCUIT_BREAKER_LOSS_PCT = 0.15  # -15% of the COMBINED 3-pocket equity vs its own macro HWM

# 08/01 -- removed the hardcoded tuple here (real bug: it would silently
# blind this breaker to scalping_v1..v5's equity once scalping_variants_
# enabled() is on) -- paper_trader.all_pocket_wallets() is now the single
# source of truth, imported lazily inside evaluate_macro_risk() below (same
# local-import pattern already used there for paper_trader/outgoing_pause).


def _macro_state_path() -> Path:
    return data_dir() / "risk_guard_state_macro.json"


def _read_macro_raw() -> dict[str, Any] | None:
    """Same three-state semantics as ``_read_raw`` above -- its own dedicated
    file, never one of the 3 per-pocket files (the macro HWM tracks a
    DIFFERENT quantity -- the sum of all 3 -- never to be confused with any
    single pocket's own high-water mark)."""
    path = _macro_state_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        logger.warning("risk_guard_state_macro unreadable/corrupted (%s) -- UNKNOWN state", exc)
        return None
    if not isinstance(raw, dict):
        logger.warning(
            "risk_guard_state_macro has unexpected shape (%r) -- UNKNOWN state", type(raw).__name__,
        )
        return None
    return raw


def _write_macro(payload: dict[str, Any]) -> None:
    path = _macro_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


@dataclass
class MacroRiskState:
    total_equity: float
    total_high_water_mark: float
    drawdown_pct: float
    blocked: bool
    newly_triggered: bool = False


async def evaluate_macro_risk(*, price_lookup=None) -> MacroRiskState:
    """Snapshot of the COMBINED risk across all 3 pockets (scalping+swing+vc)
    -- call ONCE per cycle, BEFORE any of the 3 per-pocket
    ``evaluate_portfolio_risk`` calls (a correlated, portfolio-wide crash is
    a reason to stop EVERYTHING at once, not just let each pocket notice its
    own drawdown independently, possibly several cycles apart).

    Deliberately more drastic than the per-pocket breaker: a first-time
    breach (``newly_triggered``) also calls ``outgoing_pause.pause()`` -- see
    this module's own docstring for why that's intentional here, unlike the
    per-pocket breaker above (which only blocks NEW paper entries in its own
    pocket). ``blocked`` reflects the state AFTER this call -- true if
    ``outgoing_pause`` is ALREADY paused for any reason (this call, a manual
    /stop, or anything else), reusing ``outgoing_pause.is_paused()`` rather
    than a second, diverging notion of "paused".

    The persisted ``triggered`` flag is considered live only while
    ``outgoing_pause`` is STILL actually paused: once an operator explicitly
    resumes (``/resume``/``/start``), this breaker is free to re-arm on a
    LATER, independent correlated crash -- never permanently "used up" by a
    single historical trigger."""
    from aria_core import outgoing_pause, paper_trader

    total_equity = 0.0
    # 08/01 -- all_reporting_wallets() (not all_pocket_wallets()): the MACRO
    # breaker must sum EVERY wallet holding real paper capital, including a
    # pocket retired from sourcing (e.g. legacy "scalping" after the 5-variant
    # switch) whose already-open positions still move real equity. See
    # all_reporting_wallets()'s docstring for the live bug this fixes.
    # 06/08 -- deliberately NOT switched to visible_reporting_wallets() (the
    # operator-facing filter): dropping the retired pockets' flat ~$1M each
    # from this sum against the persisted high-water mark would fake a
    # massive drawdown and trip this breaker on nothing. Hidden from every
    # operator surface, still counted here.
    for wallet in await paper_trader.all_reporting_wallets():
        summary = await paper_trader.portfolio_summary(wallet=wallet, price_lookup=price_lookup)
        total_equity += float(summary["equity"])

    raw = _read_macro_raw()
    hwm = float(raw.get("high_water_mark") or 0.0) if raw else 0.0
    if total_equity > hwm:
        hwm = total_equity
    drawdown_pct = max(0.0, (hwm - total_equity) / hwm) if hwm > 0 else 0.0

    currently_paused = outgoing_pause.is_paused()
    persisted_triggered = bool(raw.get("triggered")) if raw else False
    # See docstring above: a stale "triggered" flag left over from a
    # previous breach that the operator has since manually resumed from
    # must never permanently suppress a LATER, independent re-trigger.
    already_triggered = persisted_triggered and currently_paused

    hard_breach = drawdown_pct >= MACRO_CIRCUIT_BREAKER_LOSS_PCT
    newly_triggered = hard_breach and not already_triggered

    _write_macro({
        "high_water_mark": hwm,
        "triggered": already_triggered or hard_breach,
        "last_checked_at": datetime.now(timezone.utc).isoformat(),
    })

    if newly_triggered:
        outgoing_pause.pause(
            by="macro_circuit_breaker",
            reason=(
                f"coupe-circuit MACRO : drawdown {drawdown_pct:.1%} sur l'équité combinée des "
                f"3 poches (scalping+swing+vc) depuis le plus haut ({hwm:,.0f} $) -- krach "
                "corrélé, toutes les poches arrêtées par sécurité."
            ),
        )

    blocked = outgoing_pause.is_paused()

    return MacroRiskState(
        total_equity=total_equity,
        total_high_water_mark=hwm,
        drawdown_pct=drawdown_pct,
        blocked=blocked,
        newly_triggered=newly_triggered,
    )


def format_macro_circuit_breaker_alert(state: MacroRiskState) -> str:
    return "\n".join([
        "🧪 SIMULATION — coupe-circuit MACRO (portefeuille combiné, 3 poches)",
        f"Drawdown {state.drawdown_pct:.1%} sur l'équité combinée (scalping+swing+vc) "
        f"depuis le plus haut ({state.total_high_water_mark:,.0f} $).",
        "TOUTES les poches sont arrêtées -- coupe-circuit MACRO déclenché, pas seulement un "
        "palier par poche.",
        "ARIA est mise en pause globale (kill-switch sortant) -- reprise manuelle explicite "
        "requise (/resume).",
        "Aucun argent réel.",
    ])


# ── Rappel horaire, macro breaker (même doctrine que la 2bis per-pocket ci-dessus) ──
# Restreint au cas où ``outgoing_pause`` est actif À CAUSE de ce breaker précis
# (``triggered`` persisté dans son propre fichier) -- jamais un rappel "coupe-
# circuit" trompeur si l'opérateur a lui-même déclenché /stop pour une raison
# sans rapport avec un drawdown (garde-fou/incident/pause volontaire).


def should_send_macro_reminder() -> bool:
    from aria_core import outgoing_pause

    if not outgoing_pause.is_paused():
        return False
    raw = _read_macro_raw()
    if not raw or not raw.get("triggered"):
        return False
    last_reminder_raw = raw.get("last_reminder_at")
    if not isinstance(last_reminder_raw, str):
        return True
    try:
        last_reminder_at = datetime.fromisoformat(last_reminder_raw.replace("Z", "+00:00"))
        if last_reminder_at.tzinfo is None:
            last_reminder_at = last_reminder_at.replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    elapsed = (datetime.now(timezone.utc) - last_reminder_at).total_seconds()
    return elapsed >= REMINDER_INTERVAL_SECONDS


def record_macro_reminder_sent() -> None:
    raw = _read_macro_raw()
    if raw is None:
        return
    payload = dict(raw)
    payload["last_reminder_at"] = datetime.now(timezone.utc).isoformat()
    _write_macro(payload)


def format_macro_blocked_reminder_alert(state: MacroRiskState) -> str:
    return "\n".join([
        "⏰ RAPPEL — coupe-circuit MACRO toujours ARMÉ (portefeuille combiné, 3 poches)",
        f"Drawdown {state.drawdown_pct:.1%} sur l'équité combinée depuis le plus haut "
        f"({state.total_high_water_mark:,.0f} $).",
        "TOUTES les poches restent arrêtées tant que la reprise manuelle n'est pas donnée (/resume).",
        "Ce rappel se répète toutes les heures tant que le coupe-circuit reste armé.",
        "Aucun argent réel.",
    ])
