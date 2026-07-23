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

Two distinct mechanisms, never to be confused:
1. Per-trade sizing (``size_position_by_risk``) -- a PURE function, no
   persisted state, caps an allocation based on the distance to
   invalidation. NEVER raises an allocation beyond its entry value -- a
   cap, never a bonus.
2. Portfolio circuit breaker (``evaluate_portfolio_risk``/
   ``blocks_new_entries``) -- persisted state (dedicated JSON file, NOT
   ``outgoing_pause.py`` -- that global kill-switch also cuts cycles
   unrelated to money, e.g. ``knowledge_inbox``). ``blocks_new_entries``
   itself respects ``outgoing_pause`` (a global pause also blocks new paper
   entries) WITHOUT ever being confused with it -- two separate state
   files, two distinct reasons reported to the caller.
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
def simulated_fill_price(
    entry_price: float, alloc_usd: float, pool_liquidity_usd: float | None,
) -> float:
    """Simulated REAL fill price for a buy of ``alloc_usd`` on a pool of
    ``pool_liquidity_usd`` -- always >= ``entry_price`` (a buy pushes the price
    up, never down). Missing/invalid data (alloc/price zero,
    unknown pool liquidity) -> ``entry_price`` unchanged, fail-open -- same doctrine
    as ``cap_alloc_to_price_impact`` (the hard guardrail on liquidity lives in
    ``momentum_entry._MIN_LIQUIDITY_USD``, not here)."""
    if entry_price <= 0 or alloc_usd <= 0:
        return entry_price
    if not pool_liquidity_usd or pool_liquidity_usd <= 0:
        return entry_price
    return entry_price * (1.0 + _price_impact_pct(alloc_usd, pool_liquidity_usd))


def simulated_exit_price(
    current_price: float, position_value_usd: float, pool_liquidity_usd: float | None,
) -> float:
    """Simulated REAL exit price for a sale of ``position_value_usd`` on a pool of
    ``pool_liquidity_usd`` -- always <= ``current_price`` (a sale pushes the price
    down, never up). Symmetric to ``simulated_fill_price`` (buy), same
    impact formula (``_price_impact_pct``), never a second diverging calculation.

    07/22 -- item #18 (stress-test): the displayed PnL of an OPEN position used the
    exact spot price, as if its size could always be liquidated with zero
    slippage -- a fictitious x50 was possible on a pool that had become thin. Missing/
    invalid data -> ``current_price`` unchanged, fail-open (same doctrine as
    ``simulated_fill_price``)."""
    if current_price <= 0 or position_value_usd <= 0:
        return current_price
    if not pool_liquidity_usd or pool_liquidity_usd <= 0:
        return current_price
    return current_price * max(0.0, 1.0 - _price_impact_pct(position_value_usd, pool_liquidity_usd))


# ── 2. Portfolio circuit breaker (persisted state, dedicated file) ────────

SOFT_DRAWDOWN_PCT = 0.10       # -10% from equity high -> alloc halved
HARD_DRAWDOWN_PCT = 0.20       # -20% from the high -> blocks any new entry
HARD_CONSECUTIVE_LOSSES = 5    # 5 consecutive losses -> also blocks any new entry
SOFT_ALLOC_MULTIPLIER = 0.5

_BAND_NONE = "none"
_BAND_SOFT = "soft"
_BAND_HARD = "hard"


def _state_path() -> Path:
    return data_dir() / "risk_guard_state.json"


def _read_raw() -> dict[str, Any] | None:
    """Same three-state semantics as ``outgoing_pause._read_raw``:
    ``{}`` (file absent -- never triggered, not a doubt), ``dict``
    (read correctly), ``None`` (corrupted -- UNKNOWN state)."""
    path = _state_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        logger.warning("risk_guard_state unreadable/corrupted (%s) -- UNKNOWN state", exc)
        return None
    if not isinstance(raw, dict):
        logger.warning("risk_guard_state has unexpected shape (%r) -- UNKNOWN state", type(raw).__name__)
        return None
    return raw


def _write(payload: dict[str, Any]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def new_entry_block_status() -> dict[str, Any]:
    """Current state of the DEDICATED circuit breaker (not ``outgoing_pause``):
    ``{blocked, since, reason, by, last_alert_band, readable}``.
    ``readable=False`` signals a corrupted file -- fail-closed on the
    caller's side (``blocks_new_entries``), same "money" doctrine as
    ``outgoing_pause.money_block_reason``."""
    raw = _read_raw()
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


def block_new_entries(reason: str, *, by: int | str | None = None) -> dict[str, Any]:
    """Arms the hard tier: no more NEW paper positions until
    ``resume_new_entries`` has been called explicitly (never
    automatic -- see the module docstring)."""
    status = new_entry_block_status()
    _write(
        {
            "blocked": True,
            "since": datetime.now(timezone.utc).isoformat(),
            "by": by,
            "reason": (reason or "").strip(),
            "last_alert_band": _BAND_HARD,
        }
    )
    logger.warning("risk_guard: circuit breaker ARMED (hard tier) -- reason=%s", reason)
    return new_entry_block_status()


def resume_new_entries(*, by: int | str | None = None) -> dict[str, Any]:
    """Lifts the circuit breaker. NEVER called automatically by
    ``evaluate_portfolio_risk`` -- reserved for an explicit human action
    (e.g. operator command), even if the drawdown has since recovered."""
    _write(
        {
            "blocked": False,
            "since": None,
            "by": by,
            "reason": "",
            "last_alert_band": _BAND_NONE,
            "resumed_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    logger.warning("risk_guard: circuit breaker LIFTED (manual resume) -- by=%s", by)
    return new_entry_block_status()


def blocks_new_entries() -> tuple[bool, str | None]:
    """``(blocked, reason)`` -- combines the dedicated circuit breaker AND
    ``outgoing_pause`` (a global pause also blocks new paper entries)
    WITHOUT ever confusing the two mechanisms in the reported reason.
    Fail-closed on unreadable state ("money" doctrine)."""
    from aria_core import outgoing_pause

    if outgoing_pause.is_paused():
        return True, "ARIA en pause globale (kill-switch sortant) — aucune nouvelle position paper tant que /start n'est pas donné."

    status = new_entry_block_status()
    if not status["readable"]:
        return True, "état du coupe-circuit portefeuille illisible/corrompu — fail-closed par sécurité"
    if status["blocked"]:
        return True, status["reason"] or "coupe-circuit portefeuille armé — reprise manuelle requise"
    return False, None


@dataclass
class PortfolioRiskState:
    equity: float
    high_water_mark: float
    drawdown_pct: float             # 0..1 from the high
    consecutive_losses: int
    alloc_multiplier: float         # 1.0 normal, SOFT_ALLOC_MULTIPLIER if soft tier
    blocked: bool
    blocked_reason: str | None = None
    newly_triggered_soft: bool = False
    newly_triggered_hard: bool = False


async def evaluate_portfolio_risk(*, price_lookup=None) -> PortfolioRiskState:
    """Snapshot of portfolio risk -- to be called ONCE per cycle, before
    any attempt to open a new position (never before managing
    already-open positions, which must continue normally even with the
    circuit breaker armed). Updates the persisted equity high water mark and arms the
    dedicated circuit breaker if a hard tier is crossed for the first time."""
    from aria_core import paper_trader

    summary = await paper_trader.portfolio_summary(price_lookup=price_lookup)
    equity = float(summary["equity"])

    hwm = await paper_trader.get_equity_high_water_mark()
    if equity > hwm:
        hwm = equity
        await paper_trader.set_equity_high_water_mark(hwm)
    drawdown_pct = max(0.0, (hwm - equity) / hwm) if hwm > 0 else 0.0

    closed = await paper_trader.get_closed_positions(limit=HARD_CONSECUTIVE_LOSSES)
    consecutive_losses = 0
    for p in closed:
        if (p.get("pnl_usd") or 0.0) < 0:
            consecutive_losses += 1
        else:
            break

    status = new_entry_block_status()
    already_blocked = status["blocked"]
    hard_breach = drawdown_pct >= HARD_DRAWDOWN_PCT or consecutive_losses >= HARD_CONSECUTIVE_LOSSES
    newly_triggered_hard = False
    if hard_breach and not already_blocked and status["readable"]:
        reason = (
            f"drawdown {drawdown_pct:.1%} depuis le plus haut d'équité ({hwm:,.0f} $)"
            if drawdown_pct >= HARD_DRAWDOWN_PCT
            else f"{consecutive_losses} pertes consécutives"
        )
        block_new_entries(reason)
        newly_triggered_hard = True
        already_blocked = True

    soft_breach = SOFT_DRAWDOWN_PCT <= drawdown_pct < HARD_DRAWDOWN_PCT
    newly_triggered_soft = False
    if not already_blocked:
        last_band = status["last_alert_band"]
        if soft_breach and last_band != _BAND_SOFT:
            _write(
                {
                    "blocked": False,
                    "since": None,
                    "by": None,
                    "reason": "",
                    "last_alert_band": _BAND_SOFT,
                }
            )
            newly_triggered_soft = True
        elif not soft_breach and last_band == _BAND_SOFT:
            _write({"blocked": False, "since": None, "by": None, "reason": "", "last_alert_band": _BAND_NONE})

    blocked, blocked_reason = blocks_new_entries()
    alloc_multiplier = SOFT_ALLOC_MULTIPLIER if (soft_breach and not blocked) else 1.0

    return PortfolioRiskState(
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


def format_soft_drawdown_alert(state: PortfolioRiskState) -> str:
    return "\n".join([
        "🧪 SIMULATION — coupe-circuit portefeuille (palier SOUPLE)",
        f"Drawdown {state.drawdown_pct:.1%} depuis le plus haut d'équité ({state.high_water_mark:,.0f} $).",
        f"Allocation des NOUVELLES entrées réduite de moitié (×{SOFT_ALLOC_MULTIPLIER}) jusqu'à résorption.",
        "Positions déjà ouvertes : gérées normalement (stop suiveur/prise de profit).",
        "Aucun argent réel.",
    ])


def format_hard_circuit_breaker_alert(state: PortfolioRiskState) -> str:
    return "\n".join([
        "🧪 SIMULATION — coupe-circuit portefeuille (palier DUR)",
        f"{state.blocked_reason or 'seuil de risque franchi'}.",
        "Toute NOUVELLE position paper est bloquée jusqu'à reprise manuelle explicite.",
        "Positions déjà ouvertes : gérées normalement (stop suiveur/prise de profit) — aucune n'est fermée de force.",
        "Reprise : action humaine explicite requise, jamais automatique.",
        "Aucun argent réel.",
    ])
