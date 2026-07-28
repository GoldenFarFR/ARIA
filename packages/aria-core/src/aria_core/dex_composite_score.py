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
     extra GoPlus call.
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
half of its own weight, never a penalty and never a crash. ``score`` is
``None`` only if EVERY pillar is unresolved (e.g. security is entirely
absent) -- the caller (``momentum_entry.py``) must treat ``None`` exactly
like ``fundamental_score=None`` today: no effect on sizing, never a reject.

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

# Pillar weights, sum = 100 (28/07 design workflow, first pass).
_WEIGHT_CONTRACT_RISK = 35.0
_WEIGHT_DEV_BEHAVIOR = 20.0
_WEIGHT_SMART_MONEY = 25.0
_WEIGHT_LIQUIDITY_DEPTH = 20.0

# Pillar 1 -- confirmed-bad-only penalties (never penalize None/unknown,
# fail-open doctrine already used throughout this codebase).
_TAX_PENALTY_MAX = 8.0
_TAX_PENALTY_REFERENCE_PCT = 0.25  # combined buy+sell tax at which the tax penalty maxes out
_HIDDEN_OWNER_PENALTY = 7.0
_CAN_TAKE_BACK_OWNERSHIP_PENALTY = 7.0
_SLIPPAGE_MODIFIABLE_PENALTY = 6.0
_IS_BLACKLISTED_PENALTY = 4.0
_NOT_OPEN_SOURCE_PENALTY = 6.0
_MINT_EOA_PENALTY = 6.0
_MINT_UNKNOWN_PENALTY = 2.0

# Pillar 2 -- dev_wallet.judge_dev_wallet's signal -> score mapping.
_DEV_BEHAVIOR_SCORE_BY_SIGNAL = {
    "aligned": _WEIGHT_DEV_BEHAVIOR,
    "neutral": _WEIGHT_DEV_BEHAVIOR / 2.0,
    "concern": 0.0,
    "unknown": _WEIGHT_DEV_BEHAVIOR / 2.0,
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


def _score_contract_risk(security) -> tuple[float | None, str]:
    """Pillar 1/4 -- residual GoPlus/mint-authority risk beyond the honeypot
    class already hard-gated by ``momentum_entry._check_honeypot``. ``None``
    if ``security`` itself is unavailable (never a penalty on missing data)."""
    if security is None or not security.available:
        return None, "risque contrat résiduel : GoPlus indisponible (neutre)"

    penalty = 0.0
    details: list[str] = []

    buy_tax = security.buy_tax or 0.0
    sell_tax = security.sell_tax or 0.0
    if security.buy_tax is not None or security.sell_tax is not None:
        combined = buy_tax + sell_tax
        tax_penalty = min(_TAX_PENALTY_MAX, (combined / _TAX_PENALTY_REFERENCE_PCT) * _TAX_PENALTY_MAX)
        if tax_penalty > 0:
            penalty += tax_penalty
            details.append(f"taxe combinée {combined * 100:.1f}%")

    if security.hidden_owner:
        penalty += _HIDDEN_OWNER_PENALTY
        details.append("owner caché")
    if security.can_take_back_ownership:
        penalty += _CAN_TAKE_BACK_OWNERSHIP_PENALTY
        details.append("reprise de propriété possible")
    if security.slippage_modifiable:
        penalty += _SLIPPAGE_MODIFIABLE_PENALTY
        details.append("slippage/taxe modifiable après coup")
    if security.is_blacklisted:
        penalty += _IS_BLACKLISTED_PENALTY
        details.append("contrat peut blacklister des adresses")
    if security.is_open_source is False:
        penalty += _NOT_OPEN_SOURCE_PENALTY
        details.append("code source non vérifié")

    score = max(0.0, _WEIGHT_CONTRACT_RISK - penalty)
    if details:
        return score, f"risque contrat résiduel {score:.1f}/{_WEIGHT_CONTRACT_RISK:.0f} ({', '.join(details)})"
    return score, f"risque contrat résiduel {score:.1f}/{_WEIGHT_CONTRACT_RISK:.0f} (rien de confirmé)"


async def _resolve_mint_penalty(contract: str, security) -> tuple[float, str]:
    """Mint-authority component of pillar 1 -- best-effort, never blocking.
    Reuses ``skills.mint_authority.classify_authority`` (VC-pocket only
    today), fed by up to 2 NEW Blockscout calls (creator, then owner if not
    a recognized launchpad) -- same resolution pattern already proven in
    ``skills.acp_onchain_scan._resolve_mint_authority``."""
    has_mint = security.is_mintable if security is not None and security.available else None
    if not has_mint:
        return 0.0, "pas de fonction mint externe"

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
        return _MINT_EOA_PENALTY, f"mint contrôlé par un wallet externe ({verdict.detail})"
    if verdict.kind == "unknown":
        return _MINT_UNKNOWN_PENALTY, "autorité du mint indéterminable"
    return 0.0, f"mint neutralisé ({verdict.detail})"


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
    score = _DEV_BEHAVIOR_SCORE_BY_SIGNAL.get(verdict.signal, _WEIGHT_DEV_BEHAVIOR / 2.0)
    detail = "; ".join(verdict.points) if verdict.points else verdict.signal
    return score, f"comportement déployeur {score:.1f}/{_WEIGHT_DEV_BEHAVIOR:.0f} ({detail})"


async def _score_smart_money(contract: str, holders, pair) -> tuple[float | None, str]:
    """Pillar 3/4 -- generalizes ``momentum_entry._check_parabolic_smart_
    money_rescue``'s narrow usage of ``analyze_smart_money`` to every BUY
    candidate. Highest network cost of the 4 pillars -- capped at
    ``_MAX_SMART_MONEY_WALLETS`` (lower than the VC pocket's own default)."""
    from aria_core.services.blockscout import blockscout_client
    from aria_core.services.smart_money import analyze_smart_money

    if holders is None or not holders.available:
        return _WEIGHT_SMART_MONEY / 2.0, "smart money : holders indisponibles (neutre)"

    signal = await analyze_smart_money(
        contract, holders, client=blockscout_client,
        lp_address=getattr(pair, "pair_address", None),
        max_wallets=_MAX_SMART_MONEY_WALLETS,
    )
    if not signal.available or signal.quality_signal is None:
        return _WEIGHT_SMART_MONEY / 2.0, "smart money : signal indisponible (neutre)"
    score = _WEIGHT_SMART_MONEY * (signal.quality_signal / 100.0)
    return score, f"smart money {score:.1f}/{_WEIGHT_SMART_MONEY:.0f} ({len(signal.smart_wallets)} wallet(s) convergent(s))"


def _score_liquidity_depth(liquidity_usd: float | None, market_cap_usd: float | None) -> tuple[float | None, str]:
    """Pillar 4/4 -- liquidity as a fraction of the token's OWN valuation, a
    distinct dimension from the absolute liquidity/volume gates already hard-
    checked upstream (those measure activity relative to the pool; this
    measures the pool's depth relative to what the token claims to be
    worth). Zero extra network cost -- both fields already on the
    ``PairSnapshot`` fetched for the hard gates."""
    from aria_core.skills.liquidity_depth import DEFAULT_MIN_RATIO, assess_liquidity_depth

    depth = assess_liquidity_depth(liquidity_usd, market_cap_usd, bonding_curve=False)
    if depth.ratio is None:
        return _WEIGHT_LIQUIDITY_DEPTH / 2.0, "profondeur liquidité/mcap : market cap inconnue (neutre)"
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

    score_contract_risk, reason1 = _score_contract_risk(security)
    reasons.append(reason1)
    if score_contract_risk is not None and security is not None and security.available:
        try:
            mint_penalty, mint_reason = await _resolve_mint_penalty(contract, security)
            score_contract_risk = max(0.0, score_contract_risk - mint_penalty)
            reasons.append(f"autorité du mint : {mint_reason}")
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
        score_dev_behavior, reason2 = _WEIGHT_DEV_BEHAVIOR / 2.0, "comportement déployeur : indisponible (neutre)"
    reasons.append(reason2)

    try:
        score_smart_money, reason3 = await _score_smart_money(contract, holders, pair)
    except Exception as exc:  # noqa: BLE001
        logger.info("dex_composite_score: smart-money pillar failed for %s (%s)", contract, exc)
        score_smart_money, reason3 = _WEIGHT_SMART_MONEY / 2.0, "smart money : indisponible (neutre)"
    reasons.append(reason3)

    score_liquidity_depth, reason4 = _score_liquidity_depth(
        getattr(pair, "liquidity_usd", None), getattr(pair, "market_cap_usd", None),
    )
    reasons.append(reason4)

    parts = [score_contract_risk, score_dev_behavior, score_smart_money, score_liquidity_depth]
    resolved = [p for p in parts if p is not None]
    if not resolved:
        return DexSecurityScore(reasons=reasons)

    total = sum(resolved)
    reasons.append(f"score composite DEX {total:.1f}/100")
    return DexSecurityScore(
        score=total,
        score_contract_risk=score_contract_risk,
        score_dev_behavior=score_dev_behavior,
        score_smart_money=score_smart_money,
        score_liquidity_depth=score_liquidity_depth,
        reasons=reasons,
    )
