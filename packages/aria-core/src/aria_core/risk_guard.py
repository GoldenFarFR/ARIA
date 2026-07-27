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


def conviction_size_multiplier(
    rr: float | None, align_score: int | None, *,
    fundamental_score: float | None = None, volume_confirmed: bool | None = None,
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

    Stacking of the two vetoes (07/19, Gemini cross-review, round 5 -- fixes a real
    risk-management flaw: composing both flags into the SAME MODERATE tier treated a
    setup with TWO independent warning signals (weak fundamentals AND unverified
    volume) as equivalent to a setup with only one -- underestimating the cumulative risk)
    -- one flag alone -> MODERATE tier (3.5%); BOTH at once -> direct drop
    to the WEAK tier (2%), never a 3rd tier below (the ``MIN_ALLOC_
    MULTIPLIER`` floor remains the true floor, regardless of the number of vetoes)."""
    if rr is None or align_score is None:
        return MAX_ALLOC_MULTIPLIER
    if rr >= CONVICTION_RR_THRESHOLD and align_score >= CONVICTION_ALIGN_SCORE_THRESHOLD:
        weak_fundamentals = fundamental_score is not None and fundamental_score < FUNDAMENTAL_WEAK_THRESHOLD
        unconfirmed_volume = volume_confirmed is False
        flags = int(weak_fundamentals) + int(unconfirmed_volume)
        if flags >= 2:
            return MIN_ALLOC_MULTIPLIER
        if flags == 1:
            return MODERATE_ALLOC_MULTIPLIER
        return MAX_ALLOC_MULTIPLIER
    if rr >= MODERATE_RR_THRESHOLD:
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
) -> float | None:
    """Risk budget (fraction of capital) for the conviction tier of THIS
    signal -- same tiering and same stacking of the two vetoes as ``conviction_size_
    multiplier`` above (identical word for word, only the OUTPUT tiers
    change: a risk budget in %, not a multiplier on a flat allocation). ``None`` if
    ``rr``/``align_score`` are missing -- signals to the caller to fall back
    on ``conviction_size_multiplier`` (historical behavior), never an invented
    budget for lack of a signal."""
    if rr is None or align_score is None:
        return None
    if rr >= CONVICTION_RR_THRESHOLD and align_score >= CONVICTION_ALIGN_SCORE_THRESHOLD:
        weak_fundamentals = fundamental_score is not None and fundamental_score < FUNDAMENTAL_WEAK_THRESHOLD
        unconfirmed_volume = volume_confirmed is False
        flags = int(weak_fundamentals) + int(unconfirmed_volume)
        if flags >= 2:
            return CONVICTION_RISK_BUDGET_WEAK_PCT
        if flags == 1:
            return CONVICTION_RISK_BUDGET_MODERATE_PCT
        return CONVICTION_RISK_BUDGET_STRONG_PCT
    if rr >= MODERATE_RR_THRESHOLD:
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
) -> str | None:
    """Conviction tier label for THIS signal -- ``None`` if ``rr``/``align_score``
    are missing (never an invented tier for lack of a signal, e.g. the old
    VC-thesis pilot)."""
    if rr is None or align_score is None:
        return None
    if rr >= CONVICTION_RR_THRESHOLD and align_score >= CONVICTION_ALIGN_SCORE_THRESHOLD:
        weak_fundamentals = fundamental_score is not None and fundamental_score < FUNDAMENTAL_WEAK_THRESHOLD
        unconfirmed_volume = volume_confirmed is False
        flags = int(weak_fundamentals) + int(unconfirmed_volume)
        if flags >= 2:
            return "weak"
        if flags == 1:
            return "moderate"
        return "strong"
    if rr >= MODERATE_RR_THRESHOLD:
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
) -> float:
    """Reduces ``alloc_usd`` if the price impact of THIS order on THIS pool would drop the
    structural R/R below ``PRICE_IMPACT_MIN_RR`` -- never an increase beyond the entry
    value (same doctrine as ``size_position_by_risk``). May return ``0.0`` (no
    viable size, even infinitesimal, on this pool with this trade structure).
    Missing/inconsistent data (target, invalidation, or liquidity absent, or a
    non-bullish structure) -> unchanged, fail-open -- the hard guardrail on pool
    liquidity already lives in ``momentum_entry._MIN_LIQUIDITY_USD``, that's not the role of
    this function."""
    if alloc_usd <= 0 or entry_price <= 0:
        return alloc_usd
    if not pool_liquidity_usd or pool_liquidity_usd <= 0:
        return alloc_usd
    if not target_price or not invalidation_price:
        return alloc_usd
    if target_price <= entry_price or invalidation_price >= entry_price:
        return alloc_usd  # non-bullish structure -- not the role of this function

    degraded_entry = entry_price * (1.0 + _price_impact_pct(alloc_usd, pool_liquidity_usd))
    if degraded_entry < target_price:
        degraded_rr = (target_price - degraded_entry) / (degraded_entry - invalidation_price)
        if degraded_rr >= PRICE_IMPACT_MIN_RR:
            return alloc_usd  # negligible impact at this size, nothing to reduce

    # Closed-form solution: exact degraded entry price for which R/R == PRICE_IMPACT_MIN_RR
    # (derived from (target - e) / (e - invalidation) = PRICE_IMPACT_MIN_RR), then worked back
    # to the allocation that produces this degraded price (impact_pct linear in alloc_usd).
    target_degraded_entry = (
        target_price + PRICE_IMPACT_MIN_RR * invalidation_price
    ) / (1.0 + PRICE_IMPACT_MIN_RR)
    if target_degraded_entry <= entry_price:
        return 0.0  # even an infinitesimal size wouldn't meet this floor here

    k = PRICE_IMPACT_RATIO / pool_liquidity_usd
    capped_alloc = (target_degraded_entry / entry_price - 1.0) / k
    return max(0.0, min(alloc_usd, capped_alloc))


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
DEX_SWAP_FEE_PCT = 0.01


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
    return {
        "blocked": bool(data.get("blocked")),
        "since": since,
        "by": data.get("by"),
        "reason": data.get("reason") or "",
        "last_alert_band": data.get("last_alert_band") or _BAND_NONE,
        "readable": readable,
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


def blocks_new_entries(wallet: str) -> tuple[bool, str | None]:
    """``(blocked, reason)`` -- combines THIS POCKET's dedicated circuit
    breaker AND ``outgoing_pause`` (a global pause blocks new paper entries
    in EVERY pocket at once -- the real kill-switch stays global by
    construction, unlike the per-pocket breaker) WITHOUT ever confusing the
    two mechanisms in the reported reason. Fail-closed on unreadable state
    ("money" doctrine)."""
    from aria_core import outgoing_pause

    if outgoing_pause.is_paused():
        return True, "ARIA en pause globale (kill-switch sortant) — aucune nouvelle position paper tant que /start n'est pas donné."

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
    if hard_breach and not already_blocked and status["readable"]:
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

_MACRO_POCKETS = ("scalping", "swing", "vc")


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
    for wallet in _MACRO_POCKETS:
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
