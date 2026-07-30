"""ARIA's own probability judgment on a Polymarket market (Item #108, 26/07).

Paper-only for now (explicit operator decision) -- this module never places a
real order, it only estimates whether ARIA's own probability of an event
differs enough from the market's implied probability (the "edge") to be worth
a simulated bet. Same doctrine as `/vc`'s LLM judgment: real research (Tavily)
feeds a real LLM call, never a fabricated number -- missing data degrades to
SKIP, never a guessed probability.

Structurally distinct from the momentum/VC-thesis pipelines: a prediction
market has no chart, no liquidity pool, no honeypot risk -- the only thing to
judge is "does ARIA know something the market's current price doesn't
reflect yet." The market's own price IS the baseline to beat, not a
technical setup.

26/07 -- "quality probability system", explicit operator decisions (3 messages,
same exchange):
1. "aria doit miser la ou c'est recherche lui permettent d'avoir un taux de
   reussite importante" -- a raw probability gap alone isn't enough (can be
   LLM miscalibration noise, not a real edge).
2. "ses recherches avant de parier doivent lui permettre de dire oui je suis
   sur a 85% que le parie mise va reussir" -- quantitative floor: ARIA only
   bets when her OWN estimated probability of winning the side she actually
   bets on (not just the raw "Yes" price) clears MIN_WIN_PROBABILITY.
3. "tu dois lui creer un systeme de probabilite de qualite" -- a single LLM
   call reporting its own "confidence" is not a quality system (a model can
   say "fort" with the same wording regardless of how sound the estimate
   actually is). Real quality signal implemented instead: N=3 INDEPENDENT
   probability votes (same question/context, no vote sees another's answer,
   avoiding anchoring) must CONVERGE (spread <= MAX_VOTE_SPREAD) before the
   averaged probability is trusted at all -- same "adversarial
   verify"/panel-of-votes doctrine already used elsewhere in this codebase
   (e.g. wallet-scoring's Sybil convergence, or a judge panel), applied here
   to a single LLM judgment rather than to a delegated multi-agent workflow.

26/07 (same day, follow-up) -- operator insight on trading/prediction
correlation: "imaginon que un parie soit a 20% de chance que btc soit a 80k
dici 3 mois et que dans 1 semaine la chance passe a 70 c'est que quelque
chose se passe et peut etre en profiter". A fast-moving market price is
itself informative -- NOT as a momentum signal to follow blindly (that would
just be copying the crowd, the opposite of ARIA's own-judgment doctrine),
but as a cue that something recent likely shifted the market's read on the
underlying event, worth surfacing to the LLM as extra context alongside the
static research snippets. `services.polymarket.get_price_history`/
`compute_probability_velocity` (real CLOB history, verified live) feed
`probability_velocity_7d` into the judgment -- purely informational, never a
gate or a side-decider on its own (same "the signal wakes up attention, real
judgment still arbitrates" doctrine as `radar_x.py`).

28/07 (Item #146) -- the "N=3 independent votes" design above turned out not
to be independent in practice: all 3 calls fired the SAME prompt (including
the market's own price, shown up front) at temperature=0.3 -- low-temperature
resampling of one prompt clusters tightly by construction, so convergence
(spread <= MAX_VOTE_SPREAD) was close to guaranteed regardless of whether the
underlying reasoning was actually sound, and showing the market price before
any vote risked anchoring every vote toward the crowd's own number (exactly
what the system prompt's "don't just copy the market price" instruction was
fighting against, the hard way). Two changes: (1) `market.yes_price` no longer
appears in the vote prompt at all -- it is used only afterward, in code, to
compute `edge` against ARIA's independently-formed estimate; (2) the 3 votes
now each argue from a genuinely distinct analytical lens (`_VOTE_LENSES`: base
rate / recent catalysts / devil's advocate) instead of firing the identical
prompt 3 times -- convergence is now a real signal about the estimate's
robustness, not a low-temperature sampling artifact.

28/07 (Item #148) -- a 7h-to-resolution market and a 29-day one were being
judged with the exact same bar (`MAX_VOTE_SPREAD`/`MIN_EDGE_PROBABILITY`).
Research finding (27/07 workflow audit): real edge concentrates at LONG
horizons (macro/political questions ARIA's research can genuinely get ahead
of); a SHORT-horizon market is already saturated with live information --
an apparent edge there is closer to estimation noise than a real advantage.
`_horizon_thresholds()` now returns a STRICTER pair of thresholds
(`MAX_VOTE_SPREAD_SHORT_HORIZON`/`MIN_EDGE_PROBABILITY_SHORT_HORIZON`) for
any market with `days_left < SHORT_HORIZON_DAYS` -- graduated, never a blunt
exclusion (a real edge on a short-horizon market, while rarer, isn't
impossible, it just needs stronger evidence).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass

from aria_core.llm import chat_with_context
from aria_core.services.polymarket import PolymarketCandidateMarket, compute_probability_velocity, polymarket_client

logger = logging.getLogger(__name__)

# A market resolving near 0 or 1 already reflects near-certainty -- even a
# real edge there is either noise (the LLM overriding a market that's
# probably right) or a payout too thin to matter. Excluded before any LLM
# call is even made (saves a real Tavily/LLM cost on markets that can't
# qualify anyway).
EXTREME_PRICE_FLOOR = 0.05
EXTREME_PRICE_CEIL = 0.95

# 27/07 -- Item #133 diagnosis, real bug found: skip_reason values reached
# WITHOUT ever calling research/LLM (a free, synchronous check on data
# already in hand) -- the caller (polymarket_paper_trader.run_polymarket_
# paper_cycle) must NOT count these against CANDIDATES_PER_CYCLE, or a
# market that's always first in the volume-sorted list (a live prod case:
# a Fed-decision market pinned at yes_price=0.0015) permanently starves
# every other candidate every single cycle, forever, with CANDIDATES_PER_
# CYCLE=1 -- confirmed live: 0 Tavily calls from this module in the ~25h
# since the gate's activation despite the heartbeat cycle running on
# schedule. The other skip_reason values (insufficient_votes/no_consensus/
# win_probability_too_low/no_edge) only happen AFTER research+VOTE_COUNT
# LLM calls already ran, so those DO count against the budget.
FREE_SKIP_REASONS = frozenset({"market_price_unavailable", "market_price_already_extreme"})

# Minimum probability-point gap between ARIA's own estimate and the market's
# price to act -- below this, the gap is more likely estimation noise (LLM
# calibration is not perfect) than a real edge. Secondary guard on top of
# MIN_WIN_PROBABILITY (a market already priced close to ARIA's own estimate
# can technically clear the win-probability floor while offering no real
# value, since the market already agrees). Operator-adjustable via actual
# paper-trading results once enough resolved bets accumulate (same "measure
# before tightening" doctrine as `liquidity_rotation.py`).
MIN_EDGE_PROBABILITY = 0.12

# #148, 28/07 -- research finding (27/07 workflow audit): a market with 7h
# left to resolve and one with 29 days left were being judged with EXACTLY
# the same bar. Real edge concentrates at LONG horizons (macro/political
# questions ARIA's research can genuinely get ahead of, before the crowd
# catches up); a SHORT-horizon market is already saturated with live
# information -- an apparent edge there is more likely estimation noise than
# a real information advantage, closer to a coin flip. Rather than excluding
# short-horizon markets outright (a real edge there, while rarer, isn't
# impossible), the bar gets STRICTER as the horizon shortens -- graduated,
# same doctrine as the momentum pipeline's Regime Switch (adaptive
# thresholds, never a blunt on/off cutoff).
SHORT_HORIZON_DAYS = 3.0
MIN_EDGE_PROBABILITY_SHORT_HORIZON = 0.20  # vs 0.12 baseline
MAX_VOTE_SPREAD_SHORT_HORIZON = 0.10  # vs 0.15 baseline -- demand tighter vote convergence


def _horizon_thresholds(market: PolymarketCandidateMarket) -> tuple[float, float]:
    """Returns ``(max_vote_spread, min_edge_probability)`` for this market's
    time-to-resolution. Unknown horizon (``days_left`` missing/unparseable
    upstream) falls back to the baseline thresholds -- fail-open on missing
    data, never assumes the stricter short-horizon bar without real evidence
    the market actually IS short-horizon."""
    if market.days_left is not None and market.days_left < SHORT_HORIZON_DAYS:
        return MAX_VOTE_SPREAD_SHORT_HORIZON, MIN_EDGE_PROBABILITY_SHORT_HORIZON
    return MAX_VOTE_SPREAD, MIN_EDGE_PROBABILITY

# Explicit operator decision (original, 26/07), precise wording: "ses
# recherches avant de parier doivent lui permettre de dire oui je suis sur a
# 85% que le parie mise va reussir" -- floor on ARIA's own estimated
# probability of winning the SIDE SHE ACTUALLY BETS ON (not just the raw
# "Yes" probability -- betting NO wins when the "Yes" probability she
# estimated is LOW, so the relevant number there is 1 - probability).
# Lowered to 0.78 (30/07, explicit operator decision) -- the bar was found too
# strict in practice (near-zero bets cleared it), 0.78 was chosen as the new
# floor.
MIN_WIN_PROBABILITY = 0.78

# "Quality probability system" (operator decision #3 above): N independent
# votes, same question/context, must agree within this spread (probability
# points) before the averaged estimate is trusted. 3 is the minimum that
# lets a lone outlier be out-voted while still being cheap enough to run per
# candidate market (LLM calls are near-free compared to Tavily's metered
# budget, which is spent ONCE per market regardless of vote count -- see
# `_research_context`).
_SYSTEM_PROMPT = (
    "Tu es une analyste de marchés de prédiction, rigoureuse et bien calibrée. "
    "On te donne une question d'événement réel et un contexte de recherche "
    "récent -- JAMAIS le prix de marché actuel, volontairement : ton rôle est "
    "de former TA PROPRE estimation avant qu'elle soit comparée au prix du "
    "marché, jamais influencée par lui. Sois honnête sur l'incertitude : une "
    "vraie probabilité calibrée est presque toujours entre 0.02 et 0.98, "
    "jamais exactement 0 ou 1 sauf certitude totale et vérifiable. "
    "Réponds STRICTEMENT en JSON avec ce schéma : "
    '{"probability": 0.0-1.0, "reasoning": "2-3 phrases expliquant ton estimation"}'
)

# #146, 28/07 -- real bug found: VOTE_COUNT identical prompts fired at
# temperature=0.3 sample from nearly the same distribution -- convergence
# (spread <= MAX_VOTE_SPREAD) was almost guaranteed by construction (low-temp
# resampling of one prompt), not the genuine quality signal the operator asked
# for ("systeme de probabilite de qualite", 26/07). Each vote now argues from a
# distinct analytical lens -- same "perspective-diverse verify" doctrine already
# used elsewhere in this codebase (wallet-scoring Sybil convergence, judge
# panels): convergence across genuinely different reasoning paths is real
# evidence of a robust estimate, divergence is real evidence of uncertainty --
# neither is sampling noise dressed up as consensus.
_LENS_BASE_RATE = _SYSTEM_PROMPT + (
    "\nAngle imposé pour CETTE estimation : ancre-toi D'ABORD sur le taux de "
    "base historique de ce type d'événement (fréquence typique, précédents "
    "connus) avant d'ajuster avec le contexte de recherche -- ne laisse pas "
    "une seule actualité récente dominer si elle contredit la fréquence de base."
)
_LENS_CATALYST = _SYSTEM_PROMPT + (
    "\nAngle imposé pour CETTE estimation : concentre-toi sur les éléments les "
    "PLUS RÉCENTS du contexte de recherche, les plus susceptibles de changer "
    "l'issue avant l'échéance -- le taux de base historique compte moins ici "
    "que ce qui est en train de se passer maintenant."
)
_LENS_SKEPTIC = _SYSTEM_PROMPT + (
    "\nAngle imposé pour CETTE estimation : joue l'avocat du diable -- cherche "
    "activement les raisons pour lesquelles la lecture la plus évidente du "
    "contexte de recherche pourrait être fausse ou incomplète, avant de donner "
    "ton estimation finale."
)
_VOTE_LENSES = (_LENS_BASE_RATE, _LENS_CATALYST, _LENS_SKEPTIC)

# Kept as an explicit constant (rather than inlining len(_VOTE_LENSES)) since
# the convergence math below (spread across "VOTE_COUNT" votes) reads more
# clearly against a named constant -- must stay equal to len(_VOTE_LENSES).
VOTE_COUNT = len(_VOTE_LENSES)
MAX_VOTE_SPREAD = 0.15


def _extract_json(raw: str) -> dict | None:
    """Same defensive JSON extraction as `vc_analysis.py`/`vc_judge.py` (kept
    local rather than imported -- each LLM-judgment module already owns its
    own copy in this codebase, not a new duplication pattern)."""
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


@dataclass
class PolymarketJudgment:
    market_question: str
    market_probability: float | None
    aria_probability: float | None = None  # averaged across VOTE_COUNT independent votes
    vote_spread: float | None = None  # max-min across votes -- the measured quality signal
    edge: float | None = None
    side: str | None = None  # "YES" or "NO" -- which outcome the edge favors
    win_probability: float | None = None  # ARIA's own estimated P(the side she bets on wins)
    reasoning: str | None = None
    action: str = "SKIP"
    skip_reason: str | None = None
    probability_velocity_7d: float | None = None  # purely informational, never a gate


# Threshold (probability points over ~7 days) above which a price move is
# surfaced to the LLM as an explicit cue -- below this, day-to-day noise on a
# thin book isn't worth calling out. Same order of magnitude as MIN_EDGE_
# PROBABILITY (both represent "a move big enough to mean something"), kept
# as a separate constant since they answer different questions (edge = ARIA
# vs market NOW, velocity = market vs itself over time).
VELOCITY_HIGHLIGHT_THRESHOLD = 0.15


async def _recent_velocity(market: PolymarketCandidateMarket) -> float | None:
    """Best-effort 7-day probability delta for this market's "Yes" token --
    ``None`` on any failure (thin/new market, network outage), never blocks
    the judgment (same fail-open posture as `_research_context`)."""
    if not market.yes_token_id:
        return None
    try:
        history = await polymarket_client.get_price_history(market.yes_token_id, interval="1w", fidelity=1440)
    except Exception as exc:  # noqa: BLE001 -- a network outage must never block judgment
        logger.info("polymarket_thesis: price-history fetch failed (%s)", exc)
        return None
    return compute_probability_velocity(history)


async def _research_context(question: str) -> str | None:
    """Best-effort factual context via Tavily -- same client/budget as the
    rest of the codebase (`tavily_budget.py`), never a duplicated client.
    Called ONCE per market regardless of vote count (the metered resource is
    the search, not the LLM call). Missing/unavailable -> None (the LLM call
    still proceeds on its own training knowledge, degrades gracefully, never
    blocks)."""
    try:
        from aria_core.services.tavily import tavily_client

        result = await tavily_client.search(
            question, max_results=4, search_depth="basic", caller="polymarket_thesis"
        )
    except Exception as exc:  # noqa: BLE001 -- research failure must never block judgment
        logger.info("polymarket_thesis: research failed (%s)", exc)
        return None
    if not result.available:
        return None
    lines: list[str] = []
    if result.answer:
        lines.append(f"Résumé : {result.answer}")
    for text, url, published in result.snippets[:4]:
        date_part = f" ({published})" if published else ""
        lines.append(f"- {text}{date_part}")
    return "\n".join(lines) if lines else None


async def _single_probability_vote(
    user_message: str, system_prompt: str = _SYSTEM_PROMPT
) -> tuple[float | None, str | None]:
    """One independent LLM estimate -- ``(probability, reasoning)``, ``(None,
    None)`` on any failure (LLM outage, unparseable JSON, missing/non-numeric
    probability). Never raises.

    ``system_prompt`` defaults to the bare ``_SYSTEM_PROMPT`` for direct/unit-
    test callers, but the real pipeline (``_estimate_market_probability_
    uncached``) always passes one of ``_VOTE_LENSES`` (#146, 28/07) -- see the
    comment above ``_VOTE_LENSES`` for why."""
    try:
        raw = await chat_with_context(
            user_message,
            system_prompt,
            max_tokens=400,
            temperature=0.3,
            depth="develop",
        )
    except Exception as exc:  # noqa: BLE001 -- an LLM outage must never block the cycle
        logger.warning("polymarket_thesis: vote failed (%s)", exc)
        return None, None
    if not raw:
        return None, None
    parsed = _extract_json(raw)
    if parsed is None:
        return None, None
    try:
        probability = float(parsed.get("probability"))
    except (TypeError, ValueError):
        return None, None
    probability = max(0.01, min(0.99, probability))
    reasoning = parsed.get("reasoning")
    reasoning = str(reasoning).strip()[:600] if reasoning else None
    return probability, reasoning


def _extreme_price_skip(market: PolymarketCandidateMarket) -> PolymarketJudgment | None:
    if market.yes_price is None:
        return PolymarketJudgment(
            market_question=market.question,
            market_probability=None,
            action="SKIP",
            skip_reason="market_price_unavailable",
        )
    if market.yes_price <= EXTREME_PRICE_FLOOR or market.yes_price >= EXTREME_PRICE_CEIL:
        return PolymarketJudgment(
            market_question=market.question,
            market_probability=market.yes_price,
            action="SKIP",
            skip_reason="market_price_already_extreme",
        )
    return None


# 26/07 -- process-local TTL cache (full-pipeline audit, Item #115):
# estimate_market_probability had NO dedup at all, unlike conviction_
# research.py's LanceDB cache -- a real manual test the same day showed the
# same ~6 evergreen questions (Fed decisions, novelty bets) re-researched
# (Tavily + VOTE_COUNT=3 LLM calls) 4 times in 35 minutes. At the real 12h
# cycle cadence (polymarket_paper_cycle), this would burn through the shared
# Tavily budget re-judging markets whose meaning hasn't changed, without ever
# reaching later candidates. Keyed by yes_token_id (stable per market, same
# identifier _recent_velocity already relies on) -- falls back to the
# question text if somehow absent, never skips caching. TTL 6h: shorter than
# the 12h cycle cadence on purpose (a genuinely fast-moving market -- see
# probability_velocity_7d above -- still gets re-judged well within a day),
# long enough to kill same-session repeats and same-cycle duplicate
# evaluations.
_JUDGMENT_CACHE_TTL_SECONDS = 6.0 * 3600.0
_judgment_cache: dict[str, tuple[float, PolymarketJudgment]] = {}


def _judgment_cache_key(market: PolymarketCandidateMarket) -> str:
    return market.yes_token_id or f"question:{market.question}"


def _purge_expired_judgment_cache(now: float) -> None:
    """Opportunistic eviction, run on every cache write -- bounds the dict to
    roughly the markets seen within the last TTL window rather than growing
    forever on a long-running process (same doctrine as momentum_entry.py's
    ``_purge_expired_holders_state``)."""
    expired = [k for k, (ts, _j) in _judgment_cache.items() if (now - ts) >= _JUDGMENT_CACHE_TTL_SECONDS]
    for k in expired:
        del _judgment_cache[k]


async def estimate_market_probability(market: PolymarketCandidateMarket) -> PolymarketJudgment:
    """TTL cache in front of ``_estimate_market_probability_uncached`` -- see
    the module comment above ``_JUDGMENT_CACHE_TTL_SECONDS``."""
    key = _judgment_cache_key(market)
    now = time.monotonic()
    cached = _judgment_cache.get(key)
    if cached is not None and (now - cached[0]) < _JUDGMENT_CACHE_TTL_SECONDS:
        return cached[1]
    judgment = await _estimate_market_probability_uncached(market)
    _judgment_cache[key] = (now, judgment)
    _purge_expired_judgment_cache(now)
    return judgment


async def _estimate_market_probability_uncached(market: PolymarketCandidateMarket) -> PolymarketJudgment:
    """Full judgment pipeline for one candidate market: research -> VOTE_COUNT
    independent LLM probability votes -> convergence check -> win-probability
    floor -> edge vs market price -> BET/SKIP decision.

    Never raises -- any failure (research, LLM calls, no consensus) degrades
    to a SKIP with an explicit ``skip_reason``, same fail-open posture as the
    rest of ARIA's judgment layers on missing/unreliable data."""
    extreme_skip = _extreme_price_skip(market)
    if extreme_skip is not None:
        return extreme_skip

    velocity = await _recent_velocity(market)

    def _judgment(**kwargs) -> PolymarketJudgment:
        return PolymarketJudgment(
            market_question=market.question,
            market_probability=market.yes_price,
            probability_velocity_7d=velocity,
            **kwargs,
        )

    context = await _research_context(market.question)
    velocity_line = ""
    if velocity is not None and abs(velocity) >= VELOCITY_HIGHLIGHT_THRESHOLD:
        direction = "augmenté" if velocity > 0 else "diminué"
        velocity_line = (
            f"\nSignal de marché : la probabilité implicite du \"Oui\" a {direction} de "
            f"{abs(velocity):.0%} sur les ~7 derniers jours -- un mouvement de cette ampleur "
            "signale souvent qu'un événement récent a changé la lecture du marché. "
            "Vérifie si le contexte de recherche explique ce mouvement avant de conclure.\n"
        )
    # #146, 28/07 -- market.yes_price deliberately NEVER enters this prompt.
    # It is used ONLY below, after the vote, to compute `edge` against ARIA's
    # own independently-formed estimate (`edge = aria_probability - market.
    # yes_price`, further down). Showing it here would anchor all VOTE_COUNT
    # votes on the very number they exist to be tested against.
    user_message = (
        f"Question : {market.question}\n"
        + velocity_line
        + (
            f"\nContexte de recherche récent :\n{context}\n"
            if context
            else "\nAucun contexte de recherche disponible pour cette question.\n"
        )
    )

    votes = await asyncio.gather(*(_single_probability_vote(user_message, lens) for lens in _VOTE_LENSES))
    valid = [(p, r) for p, r in votes if p is not None]

    # #148, 28/07: stricter bar on a short-horizon market (see _horizon_
    # thresholds) -- baseline MAX_VOTE_SPREAD/MIN_EDGE_PROBABILITY otherwise.
    max_vote_spread, min_edge_probability = _horizon_thresholds(market)

    # Convergence requires a real majority (>= 2 of VOTE_COUNT=3), not merely
    # "at least 2 calls didn't error" -- same threshold shape as the
    # `Workflow` tool's own adversarial-verify pattern (>= 2/3 agreement).
    if len(valid) < 2:
        return _judgment(action="SKIP", skip_reason="insufficient_votes")

    probabilities = [p for p, _ in valid]
    spread = max(probabilities) - min(probabilities)
    if spread > max_vote_spread:
        return _judgment(vote_spread=spread, action="SKIP", skip_reason="no_consensus")

    aria_probability = sum(probabilities) / len(probabilities)
    reasoning = next((r for _, r in valid if r), None)
    edge = aria_probability - market.yes_price
    side = "YES" if edge > 0 else "NO"
    win_probability = aria_probability if side == "YES" else 1.0 - aria_probability

    common_fields = dict(
        aria_probability=aria_probability,
        vote_spread=spread,
        edge=edge,
        side=side,
        win_probability=win_probability,
        reasoning=reasoning,
    )

    if win_probability < MIN_WIN_PROBABILITY:
        return _judgment(**common_fields, action="SKIP", skip_reason="win_probability_too_low")
    if abs(edge) < min_edge_probability:
        return _judgment(**common_fields, action="SKIP", skip_reason="no_edge")

    return _judgment(**common_fields, action="BET")
