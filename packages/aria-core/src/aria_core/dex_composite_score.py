"""DEX composite security/conviction score (0-100) -- an ADDITIVE signal for
an already-graduated momentum candidate, never a gate (28/07, operator go-
ahead: "ajouter comme signal supplémentaire", explicitly NOT a replacement
for ``evaluate_hard_gates``/the R/R decision already in ``momentum_entry.py``).

Designed against the SAME comparison exercise as ``bonding_entry.py``'s
35/35/15/15 composite (a 2-agent research+design workflow, 28/07) -- but for
the opposite situation: a graduated DEX token has real OHLCV/R-R (already the
deciding factor upstream, never re-scored here to avoid double-counting) and
a much richer set of GoPlus/Blockscout signals that ``momentum_entry.py``
either never reads at all, or only reads narrowly (smart money, today gated
to the rare 200-350% parabolic-rescue tier). Four pillars, each picked
specifically because it is NOT already a hard gate elsewhere in this
pipeline (verified pillar by pillar against ``momentum_entry.py``'s real
gates before this was coded -- see ``docs/HANDOFF_PIPELINE_MOMENTUM.md``):

  1. Contract/dev residual risk (35 pts) -- GoPlus fields ``_check_honeypot``
     never reads (buy/sell tax, hidden_owner, can_take_back_ownership,
     slippage_modifiable, is_blacklisted, is_open_source) + mint authority
     classification (``mint_authority.classify_authority``, VC-pocket-only
     today). Reuses the SAME ``TokenSecurity`` already fetched by
     ``momentum_entry._check_honeypot`` (passed in by the caller) -- zero
     extra GoPlus call. BINARY since the 28/07 (2nd pass) recalibration --
     see the dedicated comment block below.
  2. Dev wallet behavior (20 pts) -- ``dev_wallet.py``'s bought/allocated/sold
     behavioral read, never wired into the momentum path before (VC-pocket
     only). A graduated token has real transfer history a bonding-stage one
     structurally lacks (team allocations are typically 0%/locked pre-
     graduation).
  3. Smart-money wallet convergence, generalized (25 pts) -- same
     ``smart_money.analyze_smart_money`` engine ``momentum_entry.py`` already
     calls, but only for the rare parabolic-rescue case. Here it runs on
     every BUY candidate (standard mode only). Highest network cost of the
     four (up to ``_MAX_SMART_MONEY_WALLETS`` wallets x 2 Blockscout calls
     each) -- deliberately capped lower than the VC pocket's own default (8)
     for this higher-volume usage.
  4. Liquidity/market-cap depth ratio (20 pts) -- ``liquidity_depth.py``,
     already built, explicitly neutralized for bonding
     (``bonding_curve=True``) and never called by ``momentum_entry.py``
     today. Zero extra network cost: reuses ``liquidity_usd``/``market_cap``
     already on the ``PairSnapshot`` fetched for the hard gates.

Fail-open throughout, same doctrine as the rest of this codebase: a signal
that can't be resolved (network failure, missing data) contributes a NEUTRAL
share of its own weight, never a penalty and never a crash. ``score`` is
``None`` only if EVERY pillar is unresolved (e.g. security is entirely
absent) -- the caller (``momentum_entry.py``) must treat ``None`` exactly
like ``fundamental_score=None`` today: no effect on sizing, never a reject.

28/07 (2nd pass, operator decision) -- NEUTRAL BASE LOWERED FROM 50% TO 35%
OF EACH PILLAR'S WEIGHT, contract-risk pillar made BINARY. Explicit operator
goal: "favoriser les meilleurs et alimenter negativement les plus mauvais" --
a token with ZERO positively-confirmed signal ANYWHERE must fall BELOW
``risk_guard.DEX_SECURITY_WEAK_THRESHOLD`` (40/100) by default, and only real
CONFIRMED positive evidence (never the mere absence of a negative one) can
push it back above that floor. Concretely: with every pillar unresolved, the
new floor is exactly ``_NEUTRAL_BASE_FRACTION * 100 = 35.0/100`` (each pillar
independently contributes 35% of its own weight when neutral, and 35% of 100
is 35) -- deliberately just BELOW the 40 threshold, so a candidate with no
evidence at all is flagged weak by construction, never by accident. Applies
uniformly to pillars 2/3/4 (same scaling formula as before, only the neutral
anchor point moves); pillar 1 (contract risk) is redesigned as fully BINARY
per explicit operator instruction ("aucun malus, soit c'est bon soit c'est
mauvais") -- see ``_classify_contract_signals``/``_finalize_contract_risk_
score`` below for the exact mechanics.

First pass, weights/thresholds NOT YET calibrated against real outcomes --
same caveat already documented on bonding_entry.py's own composite (whose
top10_holder_pct floor had to be recalibrated from 80% to ~93.8% after
observing ~380 real candidates). Revisit via ``performance_breakdown.py``
once ``dex_score_log`` (see ``dex_score_log.py``) has accumulated enough
observations -- never gravé au premier passage.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Pillar weights, sum = 100 (28/07 design workflow, first pass, unchanged by
# the 28/07 2nd-pass neutral-base recalibration below).
_WEIGHT_CONTRACT_RISK = 35.0
_WEIGHT_DEV_BEHAVIOR = 20.0
_WEIGHT_SMART_MONEY = 25.0
_WEIGHT_LIQUIDITY_DEPTH = 20.0

# 28/07 (2nd pass, operator decision) -- the fraction of a pillar's weight
# awarded when NOTHING is confirmed either way (data missing, or genuinely
# ambiguous). Was 50% for pillars 2/3/4 (and effectively 100%, i.e. the pillar
# started at its own MAX, for pillar 1) -- lowered to 35% across the board so
# that a candidate with zero positively-confirmed signal ANYWHERE lands
# exactly at 35.0/100 (below DEX_SECURITY_WEAK_THRESHOLD=40), never at a
# comfortable default. Real positive evidence is required to climb back
# above this floor -- absence of proof of danger is no longer treated as
# proof of safety.
_NEUTRAL_BASE_FRACTION = 0.35

# --- Pillar 1 -- contract/dev residual risk, BINARY (28/07, 2nd pass) ------
# Operator instruction, verbatim: "aucun malus, soit c'est bon soit c'est
# mauvais". Replaces the old per-field graduated penalties (tax proportional
# up to -8, hidden_owner -7, can_take_back_ownership -7, slippage_modifiable
# -6, is_blacklisted -4, not-open-source -6, mint EOA -6, mint unknown -2)
# with a binary verdict:
#   - Base neutral score = _CONTRACT_RISK_BASE (35% of the pillar's weight).
#   - If AT LEAST ONE of the fields below is CONFIRMED bad (regardless of
#     which one, regardless of how many) -> the whole pillar crashes to
#     _CONTRACT_RISK_BAD_SCORE (0.0, chosen deliberately rather than "close to
#     zero": a single confirmed danger flag on a contract is disqualifying on
#     its own merits, no partial credit from unrelated fields looking clean).
#   - If NOTHING is confirmed bad, the score can rise ABOVE the base ONLY
#     from fields POSITIVELY confirmed good (never from a field simply being
#     None/unresolved, fail-open doctrine unchanged) -- scaled proportionally:
#     among the fields that were actually RESOLVED (classified good or bad --
#     an ambiguous/unknown field is excluded from both the numerator and the
#     denominator), the fraction that came back good determines how far
#     between the base and the full weight the final score sits. See
#     ``_finalize_contract_risk_score`` for the exact formula.
_CONTRACT_RISK_BASE = _WEIGHT_CONTRACT_RISK * _NEUTRAL_BASE_FRACTION  # 12.25/35
_CONTRACT_RISK_BAD_SCORE = 0.0

# Combined buy+sell tax AT OR ABOVE this threshold counts as a confirmed-bad
# signal for the binary verdict above; a combined tax of exactly 0% counts as
# confirmed-good; anything strictly between the two is a real, known value
# but deliberately treated as neither (a small legitimate tax shouldn't be
# punished as a danger flag, but isn't a positive signal either) -- excluded
# from both the good-count and the resolved-count, same fail-open spirit as
# an outright unknown field.
_TAX_BAD_THRESHOLD_PCT = 0.10

# Pillar 2 -- dev_wallet.judge_dev_wallet's signal -> score mapping. Neutral
# anchor (neutral/unknown) lowered from 50% to _NEUTRAL_BASE_FRACTION (35%) of
# the pillar's weight, 28/07 2nd pass -- "aligned" still the full weight,
# "concern" still zero, unchanged.
_DEV_BEHAVIOR_SCORE_BY_SIGNAL = {
    "aligned": _WEIGHT_DEV_BEHAVIOR,
    "neutral": _WEIGHT_DEV_BEHAVIOR * _NEUTRAL_BASE_FRACTION,
    "concern": 0.0,
    "unknown": _WEIGHT_DEV_BEHAVIOR * _NEUTRAL_BASE_FRACTION,
}

# Pillar 3 -- deliberately lower than smart_money.py's own VC-pocket default
# (_MAX_WALLETS_DEFAULT=8): this composite runs on every BUY candidate in the
# momentum path (much higher volume than the rare parabolic-rescue case that
# default was calibrated for), so the per-candidate Blockscout cost is capped
# tighter here (up to 4 wallets x 2 calls = 8, not 16).
_MAX_SMART_MONEY_WALLETS = 4

# Pillar 4 -- reuses liquidity_depth.DEFAULT_MIN_RATIO as the point of full
# credit (scaling linearly to 0 at ratio=0), never a separate constant that
# could silently diverge from the VC pocket's own threshold.


@dataclass
class DexSecurityScore:
    """Composite DEX security/conviction signal -- additive only, never a
    gate. ``score=None`` only if every pillar was unresolved."""

    score: float | None = None
    score_contract_risk: float | None = None
    score_dev_behavior: float | None = None
    score_smart_money: float | None = None
    score_liquidity_depth: float | None = None
    reasons: list[str] = field(default_factory=list)


def _classify_contract_signals(security) -> tuple[bool, int, int, list[str]] | None:
    """Classifies the 6 GoPlus boolean/tax fields (mint authority excluded --
    resolved separately, async, via ``_resolve_mint_signal``, combined
    afterward by the caller). Returns ``None`` if ``security`` itself is
    unavailable (whole pillar unresolved). Otherwise ``(has_confirmed_bad,
    good_count, resolved_count, details)`` -- ``resolved_count`` only counts
    fields unambiguously classified good or bad, never a field that is
    ``None``/unknown or (tax only) in the ambiguous non-zero-but-below-
    threshold band."""
    if security is None or not security.available:
        return None

    has_bad = False
    good = 0
    resolved = 0
    details: list[str] = []

    if security.buy_tax is not None or security.sell_tax is not None:
        combined = (security.buy_tax or 0.0) + (security.sell_tax or 0.0)
        if combined >= _TAX_BAD_THRESHOLD_PCT:
            has_bad = True
            resolved += 1
            details.append(f"taxe combinée {combined * 100:.1f}% (mauvais)")
        elif combined <= 0.0:
            good += 1
            resolved += 1
            details.append("taxe nulle confirmée (bon)")
        # else: known but ambiguous low non-zero tax -- not counted either way

    if security.hidden_owner is True:
        has_bad = True
        resolved += 1
        details.append("owner caché (mauvais)")
    elif security.hidden_owner is False:
        good += 1
        resolved += 1
        details.append("pas d'owner caché (bon)")

    if security.can_take_back_ownership is True:
        has_bad = True
        resolved += 1
        details.append("reprise de propriété possible (mauvais)")
    elif security.can_take_back_ownership is False:
        good += 1
        resolved += 1
        details.append("reprise de propriété impossible (bon)")

    if security.slippage_modifiable is True:
        has_bad = True
        resolved += 1
        details.append("slippage/taxe modifiable après coup (mauvais)")
    elif security.slippage_modifiable is False:
        good += 1
        resolved += 1
        details.append("slippage/taxe non modifiable (bon)")

    if security.is_blacklisted is True:
        has_bad = True
        resolved += 1
        details.append("contrat peut blacklister des adresses (mauvais)")
    elif security.is_blacklisted is False:
        good += 1
        resolved += 1
        details.append("pas de blacklist possible (bon)")

    if security.is_open_source is False:
        has_bad = True
        resolved += 1
        details.append("code source non vérifié (mauvais)")
    elif security.is_open_source is True:
        good += 1
        resolved += 1
        details.append("code source vérifié (bon)")

    return has_bad, good, resolved, details


def _finalize_contract_risk_score(has_bad: bool, good: int, resolved: int) -> tuple[float, str]:
    """Binary finalization (28/07, 2nd pass) -- see the module-level comment
    block above ``_CONTRACT_RISK_BASE`` for the full rationale. ``has_bad``
    takes priority over everything else (a single confirmed-bad signal, GoPlus
    field OR mint authority, crashes the pillar regardless of how many other
    fields looked clean)."""
    if has_bad:
        return (
            _CONTRACT_RISK_BAD_SCORE,
            f"risque contrat résiduel {_CONTRACT_RISK_BAD_SCORE:.1f}/{_WEIGHT_CONTRACT_RISK:.0f} "
            "(au moins un signal confirmé mauvais)",
        )
    if resolved == 0:
        return (
            _CONTRACT_RISK_BASE,
            f"risque contrat résiduel {_CONTRACT_RISK_BASE:.1f}/{_WEIGHT_CONTRACT_RISK:.0f} "
            "(base neutre, rien de confirmé)",
        )
    bonus_fraction = good / resolved
    score = _CONTRACT_RISK_BASE + (_WEIGHT_CONTRACT_RISK - _CONTRACT_RISK_BASE) * bonus_fraction
    return (
        score,
        f"risque contrat résiduel {score:.1f}/{_WEIGHT_CONTRACT_RISK:.0f} "
        f"({good}/{resolved} signaux positifs confirmés)",
    )


def _score_contract_risk(security) -> tuple[float | None, str]:
    """Pillar 1/4, mint authority EXCLUDED (see ``_resolve_mint_signal``,
    async, combined by the caller in ``compute_dex_composite_score``) --
    standalone-testable pure function. ``None`` if ``security`` itself is
    unavailable (never a penalty on missing data)."""
    classified = _classify_contract_signals(security)
    if classified is None:
        return None, "risque contrat résiduel : GoPlus indisponible (neutre)"

    has_bad, good, resolved, details = classified
    score, reason = _finalize_contract_risk_score(has_bad, good, resolved)
    if details:
        reason = f"{reason} -- {', '.join(details)}"
    return score, reason


async def _resolve_mint_signal(contract: str, security) -> tuple[str, str]:
    """Mint-authority component of pillar 1 -- best-effort, never blocking.
    Reuses ``skills.mint_authority.classify_authority`` (VC-pocket only
    today), fed by up to 2 NEW Blockscout calls (creator, then owner if not
    a recognized launchpad) -- same resolution pattern already proven in
    ``skills.acp_onchain_scan._resolve_mint_authority``.

    Returns ``("bad"|"good"|"unresolved", reason)`` -- 28/07 2nd pass, binary
    doctrine: ``"bad"`` ONLY for an EOA-controlled mint (the dev retains
    uncontrolled mint power, a real confirmed danger). An indeterminable
    authority (``"unknown"``) is explicitly NOT treated as confirmed-bad
    (fail-open doctrine: we couldn't verify it, that's not the same as
    verifying it's dangerous) -- stays ``"unresolved"``, never crashes the
    pillar and never earns credit either. ``is_mintable=False`` (confirmed,
    not merely absent) and a neutralized mint (renounced/launchpad/contract-
    controlled) both count as ``"good"``."""
    has_mint = security.is_mintable if security is not None and security.available else None
    if has_mint is False:
        return "good", "pas de fonction mint (confirmé)"
    if has_mint is None:
        return "unresolved", "capacité de mint inconnue"

    from aria_core.services.blockscout import blockscout_client
    from aria_core.skills.mint_authority import classify_authority, match_launchpad

    creator = None
    owner_addr = security.owner_address if security is not None else None
    owner_is_contract = None
    try:
        info = await blockscout_client.get_address_info(contract)
        creator = info.creator_address if info.available else None
    except Exception as exc:  # noqa: BLE001 -- never blocking
        logger.info("dex_composite_score: get_address_info(%s) failed (%s)", contract, exc)

    if not match_launchpad(creator) and owner_addr:
        try:
            oinfo = await blockscout_client.get_address_info(owner_addr)
            owner_is_contract = oinfo.is_contract if oinfo.available else None
        except Exception as exc:  # noqa: BLE001
            logger.info("dex_composite_score: owner info(%s) failed (%s)", owner_addr, exc)

    verdict = classify_authority(
        has_mint=has_mint, creator_address=creator,
        owner_address=owner_addr, owner_is_contract=owner_is_contract,
    )
    if verdict.kind == "eoa":
        return "bad", f"mint contrôlé par un wallet externe ({verdict.detail})"
    if verdict.kind == "unknown":
        return "unresolved", "autorité du mint indéterminable"
    return "good", f"mint neutralisé ({verdict.detail})"


async def _score_dev_behavior(contract: str, security, holders) -> tuple[float | None, str]:
    """Pillar 2/4 -- deployer behavior (bought vs. self-allocated, sold-to-
    fund vs. extracted). Never wired into the momentum path before (VC-pocket
    only, ``skills.dev_wallet``)."""
    from aria_core.skills.dev_wallet import gather_dev_wallet_facts, judge_dev_wallet

    creator = security.owner_address if security is not None and security.available else None
    if not creator:
        try:
            from aria_core.services.blockscout import blockscout_client

            info = await blockscout_client.get_address_info(contract)
            creator = info.creator_address if info.available else None
        except Exception as exc:  # noqa: BLE001
            logger.info("dex_composite_score: creator lookup failed for %s (%s)", contract, exc)

    facts = await gather_dev_wallet_facts(contract, creator, holders=holders)
    verdict = judge_dev_wallet(facts)
    score = _DEV_BEHAVIOR_SCORE_BY_SIGNAL.get(verdict.signal, _WEIGHT_DEV_BEHAVIOR * _NEUTRAL_BASE_FRACTION)
    detail = "; ".join(verdict.points) if verdict.points else verdict.signal
    return score, f"comportement déployeur {score:.1f}/{_WEIGHT_DEV_BEHAVIOR:.0f} ({detail})"


async def _score_smart_money(contract: str, holders, pair) -> tuple[float | None, str]:
    """Pillar 3/4 -- generalizes ``momentum_entry._check_parabolic_smart_
    money_rescue``'s narrow usage of ``analyze_smart_money`` to every BUY
    candidate. Highest network cost of the 4 pillars -- capped at
    ``_MAX_SMART_MONEY_WALLETS`` (lower than the VC pocket's own default).
    Neutral anchor lowered from 50% to 35% of the pillar's weight, 28/07 2nd
    pass -- the confirmed-convergence scaling itself (quality_signal/100 *
    weight) is unchanged, so a CONFIRMED but low-quality convergence can
    still score below this neutral floor (a real measured weak signal is not
    the same as "we don't know")."""
    from aria_core.services.blockscout import blockscout_client
    from aria_core.services.smart_money import analyze_smart_money

    if holders is None or not holders.available:
        return _WEIGHT_SMART_MONEY * _NEUTRAL_BASE_FRACTION, "smart money : holders indisponibles (neutre)"

    signal = await analyze_smart_money(
        contract, holders, client=blockscout_client,
        lp_address=getattr(pair, "pair_address", None),
        max_wallets=_MAX_SMART_MONEY_WALLETS,
    )
    if not signal.available:
        return _WEIGHT_SMART_MONEY * _NEUTRAL_BASE_FRACTION, "smart money : analyse indisponible (panne réseau, neutre)"
    if signal.quality_signal is None:
        # 28/07 audit finding: NOT a failure -- smart_money.py's own quality-
        # first doctrine ("1 wallet alone proves nothing", 22/07) only ever
        # sets quality_signal once >=2 CONVERGENT wallets are found, which is
        # the common/majority case on a real token (empirically ~86% of a
        # 14-token live sample). Labeling this "indisponible" (as before)
        # falsely implied a data outage identical to the two branches above --
        # corrected so dex_score_log.py's persisted reason (once wired, see
        # its own comment) can actually distinguish "couldn't check" from
        # "checked, nothing confirmed" during future calibration.
        return (
            _WEIGHT_SMART_MONEY * _NEUTRAL_BASE_FRACTION,
            "smart money : pas de convergence confirmée (<2 wallets qualifiés, neutre -- "
            "cas normal/majoritaire, pas une panne)",
        )
    score = _WEIGHT_SMART_MONEY * (signal.quality_signal / 100.0)
    return score, f"smart money {score:.1f}/{_WEIGHT_SMART_MONEY:.0f} ({len(signal.smart_wallets)} wallet(s) convergent(s))"


def _score_liquidity_depth(liquidity_usd: float | None, market_cap_usd: float | None) -> tuple[float | None, str]:
    """Pillar 4/4 -- liquidity as a fraction of the token's OWN valuation, a
    distinct dimension from the absolute liquidity/volume gates already hard-
    checked upstream (those measure activity relative to the pool; this
    measures the pool's depth relative to what the token claims to be
    worth). Zero extra network cost -- both fields already on the
    ``PairSnapshot`` fetched for the hard gates. Neutral anchor (market cap
    unknown) lowered from 50% to 35% of the pillar's weight, 28/07 2nd pass --
    the real-ratio scaling itself is unchanged, so a confirmed thin pool can
    still score below this neutral floor."""
    from aria_core.skills.liquidity_depth import DEFAULT_MIN_RATIO, assess_liquidity_depth

    depth = assess_liquidity_depth(liquidity_usd, market_cap_usd, bonding_curve=False)
    if depth.ratio is None:
        return (
            _WEIGHT_LIQUIDITY_DEPTH * _NEUTRAL_BASE_FRACTION,
            "profondeur liquidité/mcap : market cap inconnue (neutre)",
        )
    score = _WEIGHT_LIQUIDITY_DEPTH * min(1.0, depth.ratio / DEFAULT_MIN_RATIO)
    return score, f"profondeur liquidité/mcap {score:.1f}/{_WEIGHT_LIQUIDITY_DEPTH:.0f} ({depth.note})"


async def compute_dex_composite_score(
    contract: str, chain: str, *, pair, security, mode: str = "standard",
) -> DexSecurityScore:
    """Additive DEX security/conviction score (0-100) for an already-
    graduated momentum candidate -- NEVER a gate. Called from
    ``momentum_entry.evaluate_momentum_entry``'s BUY branch, after
    ``conviction_research``, same "after everything else, never on mass
    triage" placement, standard mode only (mirrors the conviction-research
    skip already in place for scalping -- the extra Blockscout calls aren't
    worth it on a 15-30min horizon).

    ``security``: the ``TokenSecurity`` ALREADY fetched by
    ``momentum_entry._check_honeypot`` earlier in the same evaluation (via
    its short-TTL cache) -- never a second GoPlus call for the same contract.
    ``None`` if unavailable (fail-open, pillar 1 falls back to neutral).

    Base-chain only for now (mint_authority/dev_wallet/smart_money all
    depend on Blockscout, Base-only in this codebase today) -- other chains
    get ``score=None`` immediately, same fail-open doctrine as
    ``conviction_research``'s own Base-only Virtuals lookup."""
    reasons: list[str] = []

    if chain != "base":
        return DexSecurityScore(reasons=["score DEX composite : non calculé (chaîne non-Base)"])

    classified = _classify_contract_signals(security)
    if classified is None:
        score_contract_risk = None
        reasons.append("risque contrat résiduel : GoPlus indisponible (neutre)")
    else:
        has_bad, good, resolved, details = classified
        pre_mint_score, pre_mint_reason = _finalize_contract_risk_score(has_bad, good, resolved)
        if details:
            pre_mint_reason = f"{pre_mint_reason} -- {', '.join(details)}"
        reasons.append(pre_mint_reason)
        score_contract_risk = pre_mint_score

        if security is not None and security.available:
            try:
                mint_state, mint_reason = await _resolve_mint_signal(contract, security)
                reasons.append(f"autorité du mint : {mint_reason}")
                if mint_state == "bad":
                    has_bad = True
                elif mint_state == "good":
                    good += 1
                    resolved += 1
                score_contract_risk, _ = _finalize_contract_risk_score(has_bad, good, resolved)
            except Exception as exc:  # noqa: BLE001 -- never blocking
                logger.info("dex_composite_score: mint authority resolution failed for %s (%s)", contract, exc)

    holders = None
    try:
        from aria_core.momentum_entry import _cached_get_token_holders
        from aria_core.services.blockscout import blockscout_client

        holders = await _cached_get_token_holders(blockscout_client, chain, contract)
    except Exception as exc:  # noqa: BLE001 -- never blocking
        logger.info("dex_composite_score: holders fetch failed for %s (%s)", contract, exc)

    try:
        score_dev_behavior, reason2 = await _score_dev_behavior(contract, security, holders)
    except Exception as exc:  # noqa: BLE001
        logger.info("dex_composite_score: dev-behavior pillar failed for %s (%s)", contract, exc)
        score_dev_behavior, reason2 = (
            _WEIGHT_DEV_BEHAVIOR * _NEUTRAL_BASE_FRACTION, "comportement déployeur : indisponible (neutre)",
        )
    reasons.append(reason2)

    try:
        score_smart_money, reason3 = await _score_smart_money(contract, holders, pair)
    except Exception as exc:  # noqa: BLE001
        logger.info("dex_composite_score: smart-money pillar failed for %s (%s)", contract, exc)
        score_smart_money, reason3 = (
            _WEIGHT_SMART_MONEY * _NEUTRAL_BASE_FRACTION, "smart money : indisponible (neutre)",
        )
    reasons.append(reason3)

    score_liquidity_depth, reason4 = _score_liquidity_depth(
        getattr(pair, "liquidity_usd", None), getattr(pair, "market_cap_usd", None),
    )
    reasons.append(reason4)

    parts = [score_contract_risk, score_dev_behavior, score_smart_money, score_liquidity_depth]
    resolved_parts = [p for p in parts if p is not None]
    if not resolved_parts:
        return DexSecurityScore(reasons=reasons)

    total = sum(resolved_parts)
    reasons.append(f"score composite DEX {total:.1f}/100")
    return DexSecurityScore(
        score=total,
        score_contract_risk=score_contract_risk,
        score_dev_behavior=score_dev_behavior,
        score_smart_money=score_smart_money,
        score_liquidity_depth=score_liquidity_depth,
        reasons=reasons,
    )
