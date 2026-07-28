"""Virtuals bonding-curve entry -- a SEPARATE decision engine from
``momentum_entry.py``, wired into the SAME active 1M$ paper-trading test
(operator's explicit go, 24/07).

Why this exists as its own module instead of parametrizing
``evaluate_momentum_entry``: that function depends ENTIRELY on
DexScreener (pairs/liquidity/price) and GeckoTerminal (OHLCV candles) from
its very first step -- neither exists for a token still on a Virtuals
bonding curve (no DEX pool yet). Verified live, 24/07: a bonding token's
"liquidity" is the protocol's own BondingV5 contract reserve, never an
external LP the deployer could drain -- the whole "retrait de LP" risk that
``_MIN_LIQUIDITY_USD`` (momentum_entry.py) guards against doesn't apply the
same way here (same doctrine already established for a recognized launchpad
in ``knowledge/launchpads.yaml``: the mint/LP authority is the PROTOCOL's,
not an individual dev's).

Gates, deliberately DIFFERENT from the standard momentum pipeline (operator
go, 24/07 -- confirmed list from a live discussion on this exact tension):
  - DROPPED: GoPlus honeypot check (Base-DEX-oriented, structurally
    irrelevant to a bonding-curve trade -- there is no separate token
    contract logic to exploit here beyond the protocol's own, audited by
    virtue of being used by every Virtuals token).
  - DROPPED: the $50,000 liquidity floor (a Uniswap-pool-drain guard that
    doesn't apply to a protocol-owned bonding reserve).
  - DROPPED: golden-pocket/RSI computed on DexScreener/GeckoTerminal candles
    (don't exist) -- real OHLCV IS reconstructible from ``vp-api.virtuals.io``'s
    individual-trade history (``services/virtuals.py::fetch_recent_trades`` +
    ``aggregate_trades_to_candles``), so the SAME ``entry_signals.detect_entry``
    engine as the standard pipeline is STILL run and still feeds a score (see
    ``_WEIGHT_TECHNICAL_SETUP``), but (#152, 28/07, reversing this module's
    24/07 design) is no longer a HARD GATE -- a token this young structurally
    lacks enough trade history for a Fibonacci/RSI setup to mean anything, a
    real case (HOLO) was rejected on 24/07 purely for lacking one before its
    team/product was ever evaluated, and 28/07 research (see the "Composite
    score" section below) found this is exactly what a real quantitative
    study of comparable bonding-curve launches would predict. A missing/weak
    signal now scores 0 on this pillar and the composite decides alone.
  - KEPT/ADDED, Virtuals-native (found during diligence, 24/07 -- these
    fields are already returned by the SAME list endpoint
    ``fetch_by_address``/``fetch_by_pretoken`` already call, just never
    captured before): ``dev_holding_pct`` (the team-rug risk the operator
    asked to keep a guard on -- "le filtre de rug d'équipe") and
    ``top10_holder_pct`` (the Virtuals-native equivalent of
    ``_check_holder_concentration``, no Blockscout call needed).
  - KEPT: a minimum "market" floor -- ``liquidity_usd`` (already in USD,
    already returned by the API) used as the proxy, per the operator's own
    observation that liquidity and market cap track closely on a bonding
    curve ("liquidité quasiment 1 pour 1 avec le market cap").

Composite score (24/07, second iteration, same day as the initial deploy):
minutes after shipping the dev_holding/top10 hard gates above, tested against
the 100 real bonding prototypes at the time -- EVERY candidate was rejected on
holder concentration, including the one with the most holders (33, still
100% concentration). Root cause traced to the protocol's own team-vesting
mechanism (confirmed against the official Virtuals whitepaper AND the real
launch form at app.virtuals.io/create, never guessed): team allocation
modules are OFF by default, and even when on, locked 1 year post-TGE -- so
``dev_holding_pct = 0%``/``top10_holder_pct`` mechanically skewed by a thin
buyer pool are structural facts of this market stage, not signals of an
unusually safe or unusually risky token. The operator's own conclusion,
independently cross-checked (external LLM review confirmed every structural
claim before this was coded): on a token this young, the real edge is a bet
on the PRODUCT/TEAM/adoption potential, not on-chain metrics that don't mean
anything yet -- hence a composite score (operator-set weights: 35% dev
security, 35% product/team conviction, 15% technical setup, 15% holder
concentration; BUY at >= 60/100, both starting values to recalibrate once
real outcomes accumulate) REPLACES the dev_holding/top10 hard REJECTS with a
weighted judgment call, once the remaining STRUCTURAL hard gates
(unknown/dangerous dev_holding_pct, real concentration once there IS a big
enough sample, insufficient liquidity, no tradeable price) are already
cleared -- those never became "just another component".

28/07 (research-backed second revision, `docs/HANDOFF_PIPELINE_MOMENTUM.md`):
the technical setup (rr/align) was, until this date, STILL a hard gate ahead
of this score -- contradicting the "score decides" principle above for the
one pillar the operator explicitly wanted to stop gating on (see the "Gates"
section higher up for the full reasoning and the real HOLO case that
surfaced it). It is now scored 0-15 continuously like the other three
pillars, never a veto. Same pass, the holder-concentration sample-size floor
(``_MIN_HOLDERS_FOR_CONCENTRATION_CHECK``) was raised 15 -> 50 and its
below-floor score lowered from a neutral half to a near-zero fraction, per a
live 50-token sample AND independent research both finding this ratio
uninformative well past 15 real holders.

28/07 (research-backed THIRD revision, item #167): an empirical pass against
~380 real live bonding candidates found the composite score was UNREACHABLE
in practice -- 0/380 ever reached it. Two of the remaining structural hard
gates were themselves the cause, both never previously checked against a
live sample: (1) ``_MIN_LIQUIDITY_USD`` (10,000$) sat just above a BIMODAL
launch-config artifact (92.7% of fresh tokens cluster at ~9,591$, a fixed
initial-deposit amount, not a market signal) -- lowered to 5,000$; (2) the
holder-concentration hard reject (top10 > 80% once holder_count >= 50) never
observed a real token below ~93.8% concentration even at 1000+ holders,
directly contradicting its own justification (a single graduated example,
never re-confirmed against a broader sample) -- REMOVED as a hard gate,
downgraded to a pure score component with a recalibrated scale (see
``_TOP10_HOLDER_PCT_SCORE_FLOOR``), same #152 doctrine already applied to
the technical-setup pillar. A third gate ("no candles" -- fewer than
``_TRADES_PER_CANDLE`` real trades) was also found nearly perfectly
correlated with the liquidity gate (both just measure "has anyone genuinely
traded this yet") -- narrowed to its true minimum requirement (at least ONE
real trade, for a real entry price), never a full candle.

28/07, items #156-158/#161-162 (same bonding rework as #167 above): a
supply-proportion sizing cap (``cap_alloc_to_supply_pct``, applied by the
caller alongside ``BONDING_SIZE_REDUCTION``) keeps a paper position from
claiming an implausibly large share of a token's fixed float, independent of
the $-risk/price-impact caps already applied generically elsewhere. The
limit-order mechanism (``limit_orders.py``) is now bonding-aware: a
GoPlus-based re-check is structurally meaningless here, replaced by a native
re-check of this module's own dev-holding/liquidity gates, and an additional
market-cap-proxy floor (``limit_orders.BONDING_LIMIT_ORDER_MIN_LIQUIDITY_
USD``) keeps a limit order from being placed on a bonding token too thin for
"wait for a pullback" to mean anything. The VC pocket's own sourcing (3-pocket
architecture) now also evaluates bonding candidates via this SAME engine --
a Take-Seed exit design assuming a long holding horizon fits a pocket that's
never force-closed by a weekly reset far better than scalping/swing.
Finally, an organic-decline penalty (``_staleness_penalty_multiplier``)
shaves points off the composite score for a token that's aged well past a
typical bonding window without graduating -- waived by a genuine dated
catalyst (an "active" posting cadence from conviction_research.py), never a
hard reject on its own.

Sizing: reuses ``paper_trader.compute_entry_alloc`` (same risk/ATR formula
as the standard pipeline) -- the caller (``paper_trader.py``) then applies
``BONDING_SIZE_REDUCTION`` on top, a dedicated extra reduction reflecting the
structurally higher risk of this path (no honeypot check, thinner overall
market), per the operator's explicit request for a more conservative sizing
here than the standard momentum tier.

Currency, a real gap found while writing this module (never shipped with the
bug): a bonding-curve trade is priced in $VIRTUAL per token
(``VirtualTrade.price``), never USD directly -- but ``paper_trader.py``'s
whole portfolio is 100% USD. Every price level returned here (entry/target/
invalidation) is converted through ``virtuals.virtual_usd_rate()`` before
being handed back -- ``entry_atr_pct`` is the one exception, deliberately
left unconverted: it's a RATIO (ATR / price, both computed in the same
$VIRTUAL unit from the same candles), the conversion factor cancels out
algebraically, converting it would be a no-op at best and a needless second
point of failure at worst.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from aria_core.conviction_research import ConvictionResearch, research_project_potential
from aria_core.skills.entry_signals import detect_entry
from aria_core.skills.ta_levels import Candle

logger = logging.getLogger(__name__)

# A marker, not a real EVM chain id -- lets paper_trader.py/position records
# distinguish this path from a standard Base momentum entry (bonding-aware
# price lookup, see paper_trader._default_pair_lookup) without a separate
# boolean column. Imported by paper_trader.py rather than re-typed there, to
# keep the two files from silently drifting apart on this literal.
CHAIN_MARKER = "virtuals-bonding"

# Extra sizing reduction applied ON TOP of the standard risk/ATR formula
# (paper_trader.compute_entry_alloc) -- structurally higher risk (no
# honeypot-class check exists for this path), operator-requested caution.
BONDING_SIZE_REDUCTION = 0.5

# Native Virtuals dev-rug guard (replaces dev_wallet.py/GoPlus, irrelevant
# here -- see module docstring). Generous relative to the confirmed real
# example (0.08%, "Zero team tokens") -- still catches a genuinely
# team-heavy launch without false-positiving on the common zero-team norm.
_MAX_DEV_HOLDING_PCT = 5.0

# Native Virtuals concentration signal (replaces
# momentum_entry._check_holder_concentration's 80% Blockscout-based check --
# Virtuals-native field, no extra network call). #167, 28/07 -- no longer a
# reject threshold (see the hard-gate removal below) -- now the CEILING of
# the score_holders scale (the worst realistic case: ~100% of supply in the
# top 10), paired with _TOP10_HOLDER_PCT_SCORE_FLOOR below for the best case.
_MAX_TOP10_HOLDER_PCT = 100.0

# #167, 28/07 -- best realistic case found in a live empirical sample
# (workflow, ~380 real bonding candidates surveyed): even at 300-1000+ real
# holders and months of real trading, top10_holder_pct never dropped below
# ~93.8% (the single best case found). This DIRECTLY CONTRADICTED the
# hard-gate design that used to sit here (see the removed 24/07 gate,
# _MIN_HOLDERS_FOR_CONCENTRATION_CHECK/_MAX_TOP10_HOLDER_PCT's original
# 80% threshold): it was rejecting EVERY token that ever reached the
# sample-size floor, making the gate a de facto "always reject once active"
# trap rather than a real signal -- the operator's own single graduated
# reference point (317 holders, top10=54.2%) was never re-confirmed against
# a broader sample and turned out not to generalize. DOWNGRADED from a hard
# gate to a pure score component (same #152 doctrine already applied to the
# technical-setup pillar) -- 90.0 (a safety margin below the 93.8% best case
# actually observed) is the new score-ceiling reference: a token at or below
# this concentration scores the FULL 15 points, anything worse scales down
# linearly to 0 at 100%. Starting calibration on a limited sample (~36
# tokens with enough holders to judge) -- revisit once more real outcomes
# accumulate, same "measure before tightening" doctrine as every other
# starting constant in this pipeline.
_TOP10_HOLDER_PCT_SCORE_FLOOR = 90.0

# #167, 28/07 -- kept at 50 (unchanged): still the sample-size floor below
# which the ratio is uninformative (too few genuine buyers, see
# _HOLDER_CONCENTRATION_UNINFORMATIVE_SCORE_FRACTION below) -- this part of
# the reasoning (mechanically ~100% concentration with few buyers, not a rug
# signal) was never contradicted by the 28/07 empirical pass, only the
# HARD-GATE USE of the ratio above this floor was.
_MIN_HOLDERS_FOR_CONCENTRATION_CHECK = 50

# #152, 28/07 -- below the sample-size floor above, holder concentration is
# uninformative (see the comment there) -- previously scored a neutral half
# (7.5/15), now much lower per the research recommendation ("keep this
# factor's weight very low, near-zero, until ~50 real holders") -- kept
# slightly above literal zero rather than a hard 0, same "unknown is not
# proof of a negative signal" doctrine as the rest of this module's None
# handling (e.g. potential_score=None on a quiet-but-real team).
_HOLDER_CONCENTRATION_UNINFORMATIVE_SCORE_FRACTION = 0.2

# "Market" floor, expressed as the bonding pool's own liquidity (already in
# USD) -- proxy for market cap per the operator's own observation that the
# two track closely on a bonding curve. Deliberately far below
# momentum_entry._MIN_LIQUIDITY_USD (50,000$): that floor guards against an
# LP-drain rug which structurally doesn't apply here (see module docstring).
#
# #167, 28/07 -- real gap found empirically (workflow, live sample of 50 most
# recent Base bonding launches): liquidity at this stage is BIMODAL, not a
# spectrum -- 92.7% of tokens cluster at ~$9,591, 7.3% at ~$20,311, both
# corresponding to holder_count 1-6 (essentially no real buyers yet). These
# are LAUNCH-CONFIG artifacts (which of two fixed initial-deposit options the
# launcher chose), not a market-activity signal -- the old $10,000 floor sat
# just ABOVE the dominant cluster, rejecting 98% of today's real live token
# flow on a number that measures nothing real. Lowered to $5,000 -- still a
# real floor against a near-zero/corrupted-data pool, but no longer vetoing
# the standard launch configuration itself. The genuine filtering work is
# left to the composite score (dev/product/technical/holders), consistent
# with this module's own "tradée par potentiel" doctrine.
_MIN_LIQUIDITY_USD = 5_000.0

# Real trades are grouped into fixed-size buckets (never fixed time
# intervals -- see aggregate_trades_to_candles's own docstring for why).
_TRADES_PER_CANDLE = 5
_TRADES_FETCH_LIMIT = 200

# Same deterministic BUY threshold as the standard pipeline
# (momentum_entry._RR_MIN_FOR_DIRECT_BUY) -- no LLM tie-break branch here
# (V1, deliberately simpler scope): a positive but sub-2.0 R/R is a HOLD,
# never an ambiguous-path LLM call. Revisit once real trade data on this
# path justifies the extra complexity (same "measure before I act" doctrine
# already applied elsewhere in this pipeline).
_RR_MIN_FOR_DIRECT_BUY = 2.0
_ALIGN_SCORE_MIN_FOR_DIRECT_BUY = 2

# 24/07 -- composite score (operator's own design, weights confirmed
# explicitly, formulas cross-checked against an independent external review
# before shipping): replaces separate hard gates for dev security/holder
# concentration with a single weighted score, once the STRUCTURAL hard
# rejects above (unknown/dangerous dev_holding_pct, real top10 concentration
# once there's a big enough sample, insufficient liquidity, no real entry
# signal, rr/align below the direct-buy floor) have already been cleared --
# those never became "just another component", they still gate outright.
# Operator's own reasoning: on a token this young, the real edge is a bet on
# the PRODUCT/TEAM/adoption potential, not on-chain metrics that don't mean
# anything yet -- hence PRODUCT weighted the same as dev security, well
# above the technical setup/holder-concentration pair.
_WEIGHT_DEV_SECURITY = 35.0
_WEIGHT_PRODUCT_CONVICTION = 35.0
_WEIGHT_TECHNICAL_SETUP = 15.0
_WEIGHT_HOLDER_CONCENTRATION = 15.0

# Technical-setup pilier splits its 15 points between the R/R margin above
# the _RR_MIN_FOR_DIRECT_BUY floor (9 pts) and the technical-alignment margin
# above _ALIGN_SCORE_MIN_FOR_DIRECT_BUY (6 pts) -- these two MUST sum to
# _WEIGHT_TECHNICAL_SETUP, checked by a dedicated test.
_RR_SCORE_COMPONENT_MAX = 9.0
_ALIGN_SCORE_COMPONENT_MAX = 6.0
# Reference R/R treated as "excellent" for the scoring curve -- a starting
# value (like the 60/100 threshold below), to be recalibrated once enough
# real R/R values have been observed on this path rather than guessed.
_RR_SCORE_REFERENCE = 5.0

# #152, 28/07 -- fallback target/invalidation multiples of entry price, used
# ONLY when no technical setup exists (see evaluate_bonding_entry). Anchored
# on the new bonding exit design rather than invented separately: 2x matches
# the Take-Seed exit tier (#154), 0.35x (a 65% drawdown) matches the total-
# drawdown stop (#155) -- keeps the R/R this function reports internally
# consistent with how the position will actually be managed, and gives
# `paper_trader._fresh_rr`'s price-freshness re-check a real number to
# compute against instead of silently blocking every potential-based buy.
_FALLBACK_TARGET_MULTIPLE = 2.0
_FALLBACK_INVALIDATION_MULTIPLE = 0.35

# Starting threshold (operator's explicit choice, 24/07: permissive at
# first -- "on commence à soixante sur cent et on la laisse trader, si
# mauvais résultats on ajustera") -- deliberately NOT calibrated from real
# outcomes yet, same "measure before tightening" doctrine as every other
# starting constant in this pipeline (e.g. the daily-trade-floor's own
# quality bars).
_SCORE_THRESHOLD = 60.0

# Item #161, 28/07 -- organic decline (staleness): a bonding-curve token is a
# race against a thin, easily-exhausted pool of early buyer attention -- one
# that hasn't graduated after weeks is statistically less likely to ever take
# off (real interest/momentum naturally decays on a market this shallow,
# distinct from the "no candles yet" case at the very start of a token's
# life). Never a hard reject -- shaves points off the ALREADY-COMPUTED
# composite score, same "score decides, never a second veto" doctrine as
# every other pillar in this module. Starting values, to recalibrate once
# real bonding outcomes accumulate (same doctrine as every other starting
# constant here).
_STALENESS_DAYS_THRESHOLD = 30.0  # decay starts here
_STALENESS_MAX_DAYS = 45.0  # decay reaches its full extent here
_STALENESS_MAX_PENALTY_PCT = 0.5  # up to 50% of the composite score shaved off

# Item #162, 28/07 -- "dated catalyst required" guardrail: staleness (#161)
# measures ORGANIC decline, not every long-lived bonding token -- a project
# still visibly, ACTIVELY posting/shipping despite a long bonding phase is a
# real, dated signal of continued life, not noise. conviction_research.py's
# own ``posting_cadence`` is reused as-is (never a second, separately-fetched
# signal) -- "active" is its freshest, most recently-verified activity tier,
# which waives the decay entirely; any other value ("low"/"dormant"/
# "unknown") lets the age-based decay above apply in full.
_STALENESS_WAIVER_POSTING_CADENCE = "active"


def _staleness_penalty_multiplier(launched_at: str | None, *, posting_cadence: str | None = None) -> float:
    """Returns a multiplier in ``[1 - _STALENESS_MAX_PENALTY_PCT, 1.0]`` applied
    to the composite bonding score -- 1.0 (no penalty) if the launch date is
    unknown (fail-open, never an invented age), the token hasn't yet crossed
    ``_STALENESS_DAYS_THRESHOLD``, or a genuine dated catalyst was found
    (``_STALENESS_WAIVER_POSTING_CADENCE``, Item #162). Decays LINEARLY
    between the threshold and ``_STALENESS_MAX_DAYS``, capped at
    ``_STALENESS_MAX_PENALTY_PCT`` beyond that -- never harsher than the cap,
    regardless of how old the token gets."""
    if not launched_at or posting_cadence == _STALENESS_WAIVER_POSTING_CADENCE:
        return 1.0
    try:
        launched_dt = datetime.fromisoformat(str(launched_at).replace("Z", "+00:00"))
        if launched_dt.tzinfo is None:
            launched_dt = launched_dt.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - launched_dt).total_seconds() / 86400.0
    except (ValueError, TypeError):
        return 1.0  # unparsable date -- fail-open, never a fabricated age
    if age_days <= _STALENESS_DAYS_THRESHOLD:
        return 1.0
    progress = min(
        1.0, (age_days - _STALENESS_DAYS_THRESHOLD) / (_STALENESS_MAX_DAYS - _STALENESS_DAYS_THRESHOLD)
    )
    return 1.0 - progress * _STALENESS_MAX_PENALTY_PCT

# Item #156, 28/07 -- supply-proportion cap: on a bonding curve the total
# supply is fixed and often thin (see the liquidity-bimodality finding
# above) -- an allocation representing too large a fraction of it is an
# execution-plausibility problem (an unrealistically large slice of the
# entire float for a single paper position) distinct from the $-risk and
# price-impact caps already applied generically to every analyzer,
# including this one, by ``paper_trader.open_position``
# (``risk_guard.size_position_by_risk``/``cap_alloc_to_price_impact``, both
# fed ``sig.get("liquidity_usd")`` already returned here) -- this is an
# ADDITIONAL plausibility check on top of those, not a replacement. Tiered
# by conviction, same tier labels as ``risk_guard.conviction_tier_label``
# ("strong"/"moderate"/"weak") -- a stronger conviction can justify a larger
# share of a thin float, never the reverse. Starting values (like every
# other constant in this module), to recalibrate once real outcomes
# accumulate on this path.
_MAX_SUPPLY_PCT_BY_TIER = {"strong": 0.05, "moderate": 0.025, "weak": 0.01}
# Fail-CLOSED default for an unknown/missing conviction tier -- the most
# conservative of the three, same doctrine as every other "unknown is not
# proof of a negative signal, but unknown is also never a free pass" gate
# in this module.
_MAX_SUPPLY_PCT_DEFAULT = 0.01

# Item #165, 28/07 -- a tighten-only sizing lever from the LONG-term BTC
# halving-cycle lens (``skills/btc_cycles.py``), distinct from and
# complementary to the SHORT-term Regime Switch (``skills/market_sentiment.
# py``) already applied generically to sizing. "distribution"/"baisse
# (markdown)" are the two late-cycle phase labels that module produces
# (French, matches its own CyclePhase.label values verbatim) -- historically
# the highest-risk backdrop for a small, thin, illiquid bet like a
# bonding-curve token. Early-cycle phases (accumulation/markup) stay at 1.0x
# -- never a bonus, same "tighten only, never loosen" doctrine as every
# other macro lever already in this pipeline.
_BTC_LATE_CYCLE_PHASES = frozenset({"distribution", "baisse (markdown)"})
_BTC_LATE_CYCLE_SIZE_MULTIPLIER = 0.7


def late_cycle_size_multiplier(btc_cycle_phase_label: str | None) -> float:
    """1.0 (no change) unless ``btc_cycle_phase_label`` is one of the two
    late-cycle labels ``btc_cycles.current_phase_summary``/``fetch_current_
    macro_phase`` produce -- ``None``/unknown/early-cycle all fail-open to
    1.0, never an invented penalty."""
    if btc_cycle_phase_label in _BTC_LATE_CYCLE_PHASES:
        return _BTC_LATE_CYCLE_SIZE_MULTIPLIER
    return 1.0


def cap_alloc_to_supply_pct(
    alloc_usd: float, entry_price: float, total_supply: float | None, conviction_tier: str | None,
) -> float:
    """Reduces ``alloc_usd`` so the quantity it would buy never exceeds a
    tiered % of the token's total supply -- never an increase beyond the
    entry value (same doctrine as ``risk_guard.size_position_by_risk``).
    Fail-OPEN (unchanged) when ``total_supply``/``entry_price`` aren't known
    -- the dollar-risk and price-impact caps already applied by
    ``paper_trader.open_position`` remain the real guardrails in that case,
    this function is an additional plausibility check on top, not the sole
    line of defense."""
    if alloc_usd <= 0 or entry_price <= 0 or not total_supply or total_supply <= 0:
        return alloc_usd
    max_pct = _MAX_SUPPLY_PCT_BY_TIER.get(conviction_tier or "", _MAX_SUPPLY_PCT_DEFAULT)
    cap_usd = total_supply * max_pct * entry_price
    return max(0.0, min(alloc_usd, cap_usd))


def _hold(reason: str, hold_reason: str, *, symbol: str | None = None, price: float | None = None) -> dict:
    return {
        "action": "HOLD", "chain": CHAIN_MARKER, "symbol": symbol, "price": price,
        "reasons": [reason], "hold_reason": hold_reason,
    }


# Virtuals-native label -> the exact labels conviction_research.py looks for
# (its known_links parsing was written against dexscreener.py's own labels).
# "Site officiel"/"X (Twitter)" pick the primary website/X handle; "GitHub"/
# "Farcaster"/"Telegram" each trigger a REAL substance check in
# conviction_research._describe_other_known_link (repo age/activity via
# services/project_activity.py, Warpcast follower/anti-spam label, channel
# activity) -- not just "a link exists". Getting this mapping right is the
# whole point of this chantier (operator, 24/07): on a token this young, the
# only way to judge "philosophie du produit, comment ça a été construit" is
# by actually reading the GitHub repo, not by counting holders. Any other
# label passes through unmapped -- still weighed by the LLM synthesis as a
# declared link, just without a dedicated verification client.
_SOCIAL_LABEL_REMAP = {
    "website": "Site officiel",
    "twitter": "X (Twitter)",
    "x": "X (Twitter)",
    "github": "GitHub",
    "telegram": "Telegram",
    "farcaster": "Farcaster",
    "warpcast": "Farcaster",
}


def _socials_to_known_links(socials: list[dict]) -> list[dict]:
    """``VirtualToken.socials`` (real label seen live: "TWITTER", "WEBSITE")
    -> the label shape ``conviction_research.research_project_potential``
    already parses (built against ``dexscreener.PairSnapshot.project_links``)
    -- so a bonding token's own declared site/X link is used directly rather
    than re-discovered by heuristic, same shortcut the standard momentum
    pipeline already gets from DexScreener."""
    out: list[dict] = []
    for link in socials or []:
        if not isinstance(link, dict):
            continue
        url = link.get("url")
        if not url:
            continue
        label = str(link.get("label") or "").strip().lower()
        out.append({"label": _SOCIAL_LABEL_REMAP.get(label, link.get("label") or ""), "url": url})
    return out


async def evaluate_bonding_entry(
    token_address: str, *, weekly_context: dict | None = None, current_regime: str | None = None,
) -> dict | None:
    """Entry decision for a Virtuals bonding-curve candidate. Returns a dict
    compatible with ``paper_trader.run_paper_cycle``'s analyzer contract
    (``action``/``symbol``/``price``/``target``/``invalidation``/``chain``),
    or ``None`` if the token can't be resolved at all (never a fabricated
    signal -- same semantics as ``momentum_entry.evaluate_momentum_entry``).

    ``chain`` is always the literal string ``"virtuals-bonding"`` on the
    returned dict -- a marker, not a real EVM chain, so
    ``paper_trader``/position records can distinguish this path from a
    standard Base momentum entry without a separate boolean field."""
    from aria_core.services.virtuals import (
        aggregate_trades_to_candles, is_in_bonding, virtual_usd_rate, virtuals_client,
    )
    from aria_core.skills.indicators import atr_series
    from aria_core.momentum_entry import _technical_alignment

    token = await virtuals_client.fetch_by_address(token_address, chain="BASE")
    if token is None or (not token.token_address and not token.pre_token_address):
        return None
    if not is_in_bonding(token):
        # Already graduated (or status unknown) -- out of this module's
        # scope; the standard momentum pipeline takes over once a real DEX
        # pool exists, never duplicated here.
        return None

    symbol = token.symbol or "?"

    # Fail-CLOSED on an unknown value, same doctrine as the VC crible's own
    # dev-rug/concentration gates ("une donnée manquante bloque aussi -- jamais
    # OK par défaut") -- this gate exists specifically to replace dev_wallet.py/
    # GoPlus's concentration check for this path, so it inherits the same
    # seriousness, not a looser one.
    if token.dev_holding_pct is None or token.dev_holding_pct > _MAX_DEV_HOLDING_PCT:
        return _hold(
            f"détention équipe inconnue ou trop élevée ({token.dev_holding_pct}, seuil "
            f"{_MAX_DEV_HOLDING_PCT:.0f}%) -- risque de rug d'équipe",
            "dev_holding_too_high", symbol=symbol,
        )
    # Score pilier 1/4 -- always computed from a value already <= _MAX_DEV_
    # HOLDING_PCT at this point (the gate above rejected anything higher),
    # so this never goes negative.
    score_dev = _WEIGHT_DEV_SECURITY * (1.0 - token.dev_holding_pct / _MAX_DEV_HOLDING_PCT)

    # #167, 28/07 -- the hard reject that used to sit here (top10 > 80% once
    # holder_count >= 50) was removed: a live empirical pass found it rejected
    # EVERY real candidate that ever reached the sample-size floor (never
    # observed below ~93.8% even at 1000+ holders), making it a de facto
    # "always reject once active" trap. Downgraded to a pure score component,
    # same #152 doctrine already applied to the technical-setup pillar --
    # real concentration still costs points (see the scale below), it just
    # never vetoes outright on its own anymore.
    enough_holders_to_judge = (
        token.holder_count is not None and token.holder_count >= _MIN_HOLDERS_FOR_CONCENTRATION_CHECK
    )
    # Score pilier 4/4 -- below the minimum sample size, the ratio is
    # uninformative (see _MIN_HOLDERS_FOR_CONCENTRATION_CHECK's own comment)
    # -> a low, near-zero score rather than a neutral half (#152, 28/07 --
    # research finding, not just "unknown", genuinely too thin a sample to
    # carry real signal either way). Above the sample-size floor, the score
    # scales linearly between _TOP10_HOLDER_PCT_SCORE_FLOOR (full 15 points,
    # the best realistic case observed) and _MAX_TOP10_HOLDER_PCT (0 points,
    # the worst case) -- an unknown ratio at this point scores as the worst
    # case (fail-closed on the SCORE, never a hard veto since #167).
    if enough_holders_to_judge:
        top10_pct = token.top10_holder_pct if token.top10_holder_pct is not None else _MAX_TOP10_HOLDER_PCT
        score_range = _MAX_TOP10_HOLDER_PCT - _TOP10_HOLDER_PCT_SCORE_FLOOR
        score_holders = _WEIGHT_HOLDER_CONCENTRATION * max(
            0.0, min(1.0, (_MAX_TOP10_HOLDER_PCT - top10_pct) / score_range)
        )
    else:
        score_holders = _WEIGHT_HOLDER_CONCENTRATION * _HOLDER_CONCENTRATION_UNINFORMATIVE_SCORE_FRACTION

    if token.liquidity_usd is None or token.liquidity_usd < _MIN_LIQUIDITY_USD:
        return _hold(
            f"liquidité de la bonding pool insuffisante "
            f"({(token.liquidity_usd or 0.0):,.0f}$ < {_MIN_LIQUIDITY_USD:,.0f}$)",
            "insufficient_liquidity", symbol=symbol,
        )

    # #167, 28/07 -- real gap found empirically: this used to reject on
    # `not candles` (fewer than _TRADES_PER_CANDLE=5 trades -- not enough to
    # form even ONE complete candle), which rejected 49/50 tokens in a live
    # sample of the freshest Base bonding launches -- almost perfectly
    # correlated with the liquidity gate above (both really just measure
    # "has anyone genuinely traded this yet"). The only thing actually
    # required to proceed is a real ENTRY PRICE (trades[0].price) -- a token
    # with 1-4 real trades has one, even with zero complete candles.
    # detect_entry() already degrades gracefully on a short/empty candle list
    # (returns present=False, never raises) -- exactly the #152 "no signal ->
    # score 0 on the technical pillar" path, never a second hard reject here.
    # Only a token with LITERALLY ZERO real trades has no price at all -- that
    # is the one case that still genuinely blocks.
    trades = await virtuals_client.fetch_recent_trades(token_address, limit=_TRADES_FETCH_LIMIT)
    if not trades:
        return _hold(
            "aucun trade réel -- pas de prix d'entrée disponible, pas d'entrée",
            "no_trades_available", symbol=symbol,
        )
    candles: list[Candle] = aggregate_trades_to_candles(trades, trades_per_candle=_TRADES_PER_CANDLE)

    # All candle/signal levels below are in $VIRTUAL per token (the trades'
    # native unit) -- converted to USD only at the very end, once, right
    # before being returned. Unconverted here so entry_atr_pct's ratio stays
    # computed within a single consistent unit (see module docstring).
    execution_price_virtual = trades[0].price
    signal = detect_entry(candles, execution_price=execution_price_virtual)
    reasons: list[str] = list(signal.reasons)

    # #152, 28/07 -- real gap found and fixed: a missing/weak technical setup
    # used to hard-reject HERE, before the composite score (dev/product/
    # holders) was ever computed -- a real bonding candidate (HOLO) was
    # rejected on 24/07 purely for lacking a golden-pocket setup, its
    # team/product potential never even evaluated. Research backing (28/07
    # workflow, `docs/HANDOFF_PIPELINE_MOMENTUM.md`): a token this young
    # structurally lacks enough trade history for a Fibonacci/RSI setup to
    # mean anything, and real edge on a bonding curve concentrates in team/
    # product signals, not chart patterns this early -- operator's explicit
    # instruction, "les tokens en bonding ... sont tradés par potentiel,
    # grâce au facteur équipe, produit, fondamentaux". A missing/weak signal
    # NO LONGER hard-rejects -- it scores 0 on the technical-setup pillar
    # (still 15% of the composite below) and the composite decides alone,
    # same doctrine already applied to holder concentration/dev security.
    align_score = 0
    score_setup = 0.0
    has_technical_signal = signal.present and signal.rr is not None and signal.rr > 0
    if has_technical_signal:
        reasons.append(f"setup golden pocket + divergence RSI, R/R {signal.rr:.1f}")
        align_score, align_reasons, _align_detail = _technical_alignment(candles)
        reasons.extend(align_reasons)
        # Score pilier 3/4 -- proportional margin above the floors that used
        # to hard-reject (now just inputs to a continuous score, never a
        # second pass/fail).
        score_setup = (
            min(_RR_SCORE_COMPONENT_MAX, (signal.rr / _RR_SCORE_REFERENCE) * _RR_SCORE_COMPONENT_MAX)
            + (align_score / 3.0) * _ALIGN_SCORE_COMPONENT_MAX
        )
    else:
        reasons.append(
            "pas de setup golden pocket + divergence RSI (bonding) -- pilier technique noté 0/"
            f"{_WEIGHT_TECHNICAL_SETUP:.0f}, le jugement porte sur le potentiel (dev/produit/holders)"
        )

    # Ratio -- unit-independent (ATR and price both in $VIRTUAL, from the same
    # candles), never converted (see module docstring).
    entry_atr_pct = None
    atr_values = atr_series(candles)
    last_atr = atr_values[-1] if atr_values else None
    if last_atr is not None and execution_price_virtual:
        entry_atr_pct = last_atr / execution_price_virtual

    usd_rate = await virtual_usd_rate()
    if usd_rate is None:
        reasons.append("taux $VIRTUAL/USD indisponible -- prix non convertible, pas d'entrée (jamais un prix inventé)")
        return {
            "action": "HOLD", "chain": CHAIN_MARKER, "symbol": symbol,
            "price": None, "reasons": reasons, "hold_reason": "usd_rate_unavailable",
        }

    price_usd = execution_price_virtual * usd_rate
    if has_technical_signal:
        target_usd = signal.target * usd_rate if signal.target is not None else None
        invalidation_usd = signal.invalidation * usd_rate if signal.invalidation is not None else None
        # #155, 28/07 (bonding research finding): a real, verified bonding-
        # phase volatility data point (HOLO, an active non-ghost project)
        # showed -55% to +122% swings within a single sampled trade window
        # as NORMAL noise, not failure. A technical invalidation TIGHTER
        # than the -65% total-drawdown floor below would exit on ordinary
        # bonding-curve noise, exactly the failure mode the research warned
        # against (a -50% VC-style stop would already have kicked ARIA out
        # of a legitimately alive project). Widen (never tighten) to at
        # least this floor -- never overrides a technical invalidation that
        # was ALREADY wider.
        if invalidation_usd is not None:
            invalidation_usd = min(invalidation_usd, price_usd * _FALLBACK_INVALIDATION_MULTIPLE)
    else:
        # #152, 28/07 -- no technical setup means no Fibonacci target/
        # invalidation to convert, but `paper_trader._fresh_rr`/
        # `_execution_rr_still_valid` (the price-freshness re-check right
        # before execution) structurally requires non-None values to compute
        # anything at all -- a bare None here would silently block EVERY
        # potential-based buy at that later gate, defeating this whole
        # change. Anchored on the SAME multiples as the new bonding exit
        # design (#154 Take-Seed tier / #155 total-drawdown stop) rather
        # than inventing a separate number: R/R stays internally consistent
        # with what the position will actually be managed against.
        target_usd = price_usd * _FALLBACK_TARGET_MULTIPLE
        invalidation_usd = price_usd * _FALLBACK_INVALIDATION_MULTIPLE

    # 24/07 -- operator's own reasoning, right after seeing the concentration
    # gate fail on the entire real bonding market: on a token this young,
    # on-chain metrics (holders, concentration) are structurally too thin to
    # mean anything -- the real edge is a bet on the PRODUCT/TEAM/adoption
    # potential, same conviction diligence already live on the standard
    # momentum pipeline (conviction_research.py, ARIA_CONVICTION_RESEARCH_
    # ENABLED). Reused as-is here, never duplicated -- AFTER everything else,
    # only on a candidate already about to be bought (same doctrine as
    # momentum_entry.py, preserves this path's speed). Feeds the composite
    # score below (never an individual gate on its own -- a token is never
    # rejected on potential_score alone, only through the weighted total).
    #
    # Operator's explicit nuance (24/07, same segment): a discreet/quiet team
    # is not the same as a worthless one -- some legitimate builders are quiet
    # right up until real traction makes them visible. A low posting cadence
    # or few sources found must NOT read as a negative signal -- it stays
    # `potential_score=None` (fail-open, neutral half-score, neither a bonus
    # nor a malus) rather than being scored down for lack of noise.
    potential_score = None
    conviction_process_trail: str | None = None
    conviction_website_corroborated: bool | None = None
    conviction_posting_cadence: str | None = None
    research = await research_project_potential(
        # "base" (not CHAIN_MARKER): a cleaner Tavily search query (Virtuals
        # bonding tokens are Base contracts) and shares the SAME cache entry
        # with the standard momentum pipeline's own diligence on this same
        # contract once it graduates -- never a redundant re-search.
        token_address, symbol, "base", known_links=_socials_to_known_links(token.socials),
    )
    if research.available:
        if research.process_trail:
            reasons.append("diligence de conviction : " + " -> ".join(research.process_trail))
            conviction_process_trail = " -> ".join(research.process_trail)
        conviction_website_corroborated = research.contract_corroborated
        conviction_posting_cadence = research.posting_cadence
        if research.potential_score is not None:
            potential_score = research.potential_score
            reasons.append(
                f"potentiel fondamental {potential_score:.1f}/10 "
                f"(site {'trouvé' if research.website_url else 'introuvable'}, "
                f"cadence X {research.posting_cadence})"
                + (f" : {research.rationale}" if research.rationale else "")
            )

    # Score pilier 2/4.
    if potential_score is None:
        score_product = _WEIGHT_PRODUCT_CONVICTION / 2.0
    else:
        score_product = potential_score * (_WEIGHT_PRODUCT_CONVICTION / 10.0)

    # 24/07 -- composite score, the operator's own scoring table (poids
    # 35/35/15/15, seuil 60/100 pour démarrer -- voir les constantes _WEIGHT_*/
    # _SCORE_THRESHOLD ci-dessus pour le détail et la justification de chaque
    # chiffre). Everything above this point that's STILL a hard gate
    # (dev_holding/top10-when-enough-holders/liquidity/no-tradeable-price)
    # already returned -- this score decides the FINAL call on what's left.
    # #152, 28/07: the technical setup (rr/align) is NO LONGER one of those
    # hard gates (see score_setup's own comment) -- it only ever reaches this
    # score as a 0-15 continuous contribution, same standing as the other 3
    # pillars, never a veto of its own.
    bonding_score = score_dev + score_product + score_setup + score_holders
    reasons.append(
        f"score composite bonding {bonding_score:.1f}/100 "
        f"(dev {score_dev:.1f}/{_WEIGHT_DEV_SECURITY:.0f}, "
        f"produit {score_product:.1f}/{_WEIGHT_PRODUCT_CONVICTION:.0f}, "
        f"setup {score_setup:.1f}/{_WEIGHT_TECHNICAL_SETUP:.0f}, "
        f"holders {score_holders:.1f}/{_WEIGHT_HOLDER_CONCENTRATION:.0f})"
    )

    # Item #161/#162, 28/07: organic-decline penalty, applied to the ALREADY-
    # COMPUTED score above (never a separate gate) -- waived entirely by a
    # genuine dated catalyst (#162, posting_cadence == "active"), otherwise
    # decaying linearly once the token has aged past _STALENESS_DAYS_
    # THRESHOLD without graduating (still UNDERGRAD at this point in the
    # function, see the is_in_bonding() check earlier).
    staleness_multiplier = _staleness_penalty_multiplier(
        token.launched_at, posting_cadence=conviction_posting_cadence,
    )
    if staleness_multiplier < 1.0:
        bonding_score *= staleness_multiplier
        reasons.append(
            f"déclin organique (bonding non-gradué depuis longtemps) -- score réduit "
            f"de {(1.0 - staleness_multiplier) * 100:.0f}% à {bonding_score:.1f}/100"
        )

    if bonding_score < _SCORE_THRESHOLD:
        reasons.append(f"score composite sous le seuil ({_SCORE_THRESHOLD:.0f}/100)")
        return {
            "action": "HOLD", "chain": CHAIN_MARKER, "symbol": symbol,
            "price": price_usd, "reasons": reasons, "hold_reason": "score_below_threshold",
            "bonding_score": bonding_score,
        }

    return {
        "action": "BUY",
        "chain": CHAIN_MARKER,
        "symbol": symbol,
        "price": price_usd,
        "target": target_usd,
        "invalidation": invalidation_usd,
        "bonding_score": bonding_score,
        "potential_score": potential_score,
        "conviction_process_trail": conviction_process_trail,
        "conviction_website_corroborated": conviction_website_corroborated,
        "conviction_posting_cadence": conviction_posting_cadence,
        "rr": signal.rr,
        "align_score": align_score,
        "liquidity_usd": token.liquidity_usd,
        "total_supply": token.total_supply,  # #156, 28/07 -- paper_trader's supply-proportion cap
        "entry_atr_pct": entry_atr_pct,
        "strategy": "momentum",
        "reasons": reasons,
        "hold_reason": None,
        "regime": current_regime or "neutre",
        "these": (
            f"Bonding Virtuals -- score composite {bonding_score:.1f}/100, "
            f"dev holding {token.dev_holding_pct:.2f}%, "
            # #152, 28/07 -- top10_holder_pct can legitimately be None (API
            # has no data) even when a numeric holder_count exists -- more
            # likely now that _MIN_HOLDERS_FOR_CONCENTRATION_CHECK is 50, not
            # 15 (more tokens fall below the "enough to judge" floor). A bare
            # `.1f` on None would crash a BUY dict construction outright.
            + (f"top10 holders {token.top10_holder_pct:.1f}%, " if token.top10_holder_pct is not None else "top10 holders inconnu, ")
            + f"prix converti au taux $VIRTUAL/USD {usd_rate:.4f}."
        ),
    }
