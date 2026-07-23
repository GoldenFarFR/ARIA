"""Safety filter ("screen") — the guardian of the trainable contract pool.

Turns a scan's rich result (`TokenScanContext`) into a **binary verdict**
"pass / fail", with its factual reasons. This is the entry gate of the token
pool from which the training loop draws its 20 candidates.

## Honesty (guardrail extension)

- We NEVER say "100% reliable". Passing the filter = **"no scam marker
  detected + sufficient liquidity + SAFE scan verdict"**, not a guarantee. A
  technically clean contract can still rug later (team, off-chain):
  undetectable on-chain, we don't claim otherwise.
- The filter relies on the scan's EXISTING scoring (`_score_and_verdict`),
  which already penalizes honeypot / mint / concentration / low liquidity via
  the `security_score`. Here we only impose a **strict threshold** on top
  (defense in depth).
- Deterministic: same scan facts -> same filter verdict.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from aria_core.momentum_entry import MAX_VOLUME_TO_LIQUIDITY_RATIO, _wash_trading_ratio_confirmed
from aria_core.skills.acp_onchain_scan import TokenScanContext
from aria_core.skills.mint_authority import SAFE_AUTHORITIES


def _mint_is_dev_controlled(ctx: TokenScanContext) -> bool:
    """True if an external mint exists AND remains in a dev's hands (or is
    undeterminable).

    Neutralized (returns False) if the authority is renounced, a known
    launchpad, or a contract (timelock/multisig/issuance). Fail-closed: a
    mint whose authority couldn't be resolved ('unknown' or unset) stays blocking.
    """
    if ctx.has_mint is not True:
        return False
    return (ctx.mint_authority or "unknown") not in SAFE_AUTHORITIES


def _wash_trading_confirmed(ctx: TokenScanContext) -> bool:
    """07/22 -- item #1 (post-stress-test hardening plan): reuses AS-IS the
    momentum pipeline's wash-trading detector (`MAX_VOLUME_TO_LIQUIDITY_RATIO`
    + `_wash_trading_ratio_confirmed`, shared sustained-confirmation window)
    -- never a second constant/logic that could diverge. The VC screen until
    now had NO anti-volume-manipulation guardrail at all, while
    `_score_and_verdict`'s scoring only looks at security/liquidity/
    concentration. Chain hardcoded to 'base' -- this module (like
    `acp_onchain_scan.py`) is scoped to Base only, so the shared key
    (contract, chain) with the momentum pipeline is consistent (same contract
    = same market reality, regardless of who scans it)."""
    if ctx.best_pair is None or not ctx.best_pair.liquidity_usd:
        return False
    volume_to_liq = (ctx.best_pair.volume_24h_usd or 0.0) / ctx.best_pair.liquidity_usd
    return _wash_trading_ratio_confirmed(ctx.contract, "base", volume_to_liq)

# Trainable-pool thresholds (strict by default: we want REALLY tradeable,
# not just "not a scam"). Adjustable per call.
DEFAULT_MIN_LIQUIDITY_USD = 30_000.0
DEFAULT_MIN_SCORE = 70
# Beyond this, a single wallet (outside LP/burn) can collapse the token by selling.
DEFAULT_MAX_TOP_HOLDER_PCT = 30.0
# Beyond this, the sell tax makes exiting too costly for a trainable pool
# (often a sign of an extractive / semi-honeypot token).
DEFAULT_MAX_SELL_TAX = 0.15


@dataclass(frozen=True)
class ScreenResult:
    """Safety filter verdict for a contract, with its factual reasons."""

    contract: str
    passed: bool
    security_score: int
    liquidity_usd: float
    verdict: str  # scan's lite_verdict: SAFE / CAUTION / DANGER
    reasons: list[str] = field(default_factory=list)
    # True if the failure comes from a CONFIRMED NEGATIVE signal (dev mint,
    # blacklist, proven concentration, real liquidity too low...). False if
    # the failure is ONLY due to unavailable data (429, timeout, holders not
    # returned): in that case it's not a "rejected forever" — retry later.
    hard_fail: bool = False


def safety_screen(
    ctx: TokenScanContext,
    *,
    min_liquidity_usd: float = DEFAULT_MIN_LIQUIDITY_USD,
    min_score: int = DEFAULT_MIN_SCORE,
    max_top_holder_pct: float = DEFAULT_MAX_TOP_HOLDER_PCT,
    require_verified: bool = True,
    liquidity_stability_confirmed: bool | None = None,
) -> ScreenResult:
    """Decides whether a contract enters the "screened" pool.

    PRIORITY guardrails (does the dev keep the power to harm?), even before
    looking at the market:
      - **verified** contract (public code, otherwise opaque);
      - no **mint** / **blacklist** / **transfer disabling** (rug/honeypot
        levers the dev would retain);
      - **concentration**: no wallet (outside LP/burn) above the threshold
        (otherwise a single whale can dump).
    Then the market guardrails: valid address, DEX pair, liquidity, score,
    SAFE verdict. Passes only if EVERYTHING is met. ``reasons`` lists every
    blocker (never an opaque rejection). An **unknown** security data point
    also blocks: for a training pool, we only include what we can confirm
    (fail-closed).

    ``liquidity_stability_confirmed`` (07/22, item #19 -- temporal stability
    confirmation, `skills/liquidity_stability.py`): ``False`` if a suspicious
    liquidity drop has been detected since the last scan of this same
    contract (recent window) -- soft-fail (market behavior, not a confirmed
    mechanism in the contract, same family as `wash_trading`). ``None``
    (default, or first scan ever seen) -> no effect, never a rejection due to
    a missing comparison.
    """
    liq = ctx.best_pair.liquidity_usd if ctx.best_pair else 0.0
    reasons: list[str] = []

    # Computed first (reused by several guardrails below, never recomputed
    # twice with a risk of divergence).
    mint_blocks = _mint_is_dev_controlled(ctx)
    wash_trading = _wash_trading_confirmed(ctx)

    # 07/22 -- item #5 (hardening plan): the single liquidity floor was
    # wrongly penalizing a token whose SCORE and VERDICT are already clean --
    # the scam/rug risk is then already ruled out by the scoring itself.
    # Relaxed ONLY if everything else (score, verdict, mint) is spotless,
    # never a generic blank check on liquidity (which stays the default everywhere else).
    liquidity_low = ctx.best_pair is not None and liq < min_liquidity_usd
    liquidity_bypass = (
        liquidity_low
        and ctx.security_score >= min_score
        and ctx.lite_verdict == "SAFE"
        and not mint_blocks
    )

    # Market guardrails
    if not ctx.valid_address:
        reasons.append("adresse de contrat invalide")
    if ctx.best_pair is None:
        reasons.append("aucune paire DEX trouvée (illiquide ou inexistant)")
    if liquidity_low:
        if liquidity_bypass:
            reasons.append(
                f"liquidité faible ${liq:,.0f} < ${min_liquidity_usd:,.0f} -- tolérée "
                f"(score {ctx.security_score}/95, verdict SAFE, mint propre)"
            )
        else:
            reasons.append(f"liquidité ${liq:,.0f} < minimum ${min_liquidity_usd:,.0f}")
    if ctx.security_score < min_score:
        reasons.append(f"score de sécurité {ctx.security_score} < {min_score}")
    if ctx.lite_verdict != "SAFE":
        reasons.append(f"verdict de scan '{ctx.lite_verdict}' (SAFE requis)")
    if wash_trading:
        reasons.append(
            f"volume 24h/liquidité extrême et SOUTENU (> {MAX_VOLUME_TO_LIQUIDITY_RATIO:.0f}x) "
            "-- signal de wash-trading"
        )
    if liquidity_stability_confirmed is False:
        reasons.append(
            "chute de liquidité suspecte depuis le dernier scan de ce contrat "
            "-- possible manipulation synchronisée sur la fenêtre de scan"
        )

    # "Dev keeps the power" guardrails (priority)
    if require_verified and ctx.contract_verified is not True:
        reasons.append("contrat non vérifié (code opaque)")
    # Mint: blocking only if a DEV retains control. A renounced mint, driven
    # by a known launchpad (Virtuals/Flaunch...) or by a contract
    # (timelock/multisig/issuance) is legitimate -> neutralized (see mint_authority).
    if mint_blocks:
        detail = ctx.mint_authority_detail or "le dev peut créer des tokens"
        reasons.append(f"fonction mint contrôlée par un dev ({detail})")
    if ctx.has_blacklist is True:
        reasons.append("fonction blacklist présente (le dev peut bloquer des ventes)")
    if ctx.has_disable_transfers is True:
        reasons.append("désactivation des transferts possible (levier honeypot)")

    # Dynamic honeypot guardrails (GoPlus, data-gated). None (not scanned /
    # unavailable) -> no effect: strictly unchanged behavior on scans that don't call it.
    if ctx.is_honeypot is True:
        reasons.append("honeypot confirmé (GoPlus) — revente bloquée")
    if ctx.cannot_sell is True:
        reasons.append("vente totale impossible (GoPlus)")
    if ctx.sell_tax is not None and ctx.sell_tax > DEFAULT_MAX_SELL_TAX:
        reasons.append(
            f"taxe de vente {ctx.sell_tax * 100:.0f}% > {DEFAULT_MAX_SELL_TAX * 100:.0f}% (extractif)"
        )
    if ctx.hidden_owner is True:
        reasons.append("owner caché (GoPlus)")
    if ctx.can_take_back_ownership is True:
        reasons.append("reprise de propriété possible (GoPlus)")
    # 07/22 -- item #2 (hardening plan): same family as hidden_owner/
    # can_take_back_ownership -- a HIDDEN power the dev keeps over the
    # contract, never fixed by time or a better liquidity score -> hard failure.
    if ctx.slippage_modifiable is True:
        reasons.append("taxe/slippage modifiable après coup (GoPlus) — pouvoir dissimulé")
    # 07/22 -- gap found while observing a REALLY open momentum position
    # (CNX, owner_change_balance never consulted anywhere): a power DISTINCT
    # from the classic honeypot -- the owner can directly modify a wallet's
    # balance, a total-loss vector, never fixed by time -> hard failure.
    if ctx.owner_change_balance is True:
        reasons.append("owner peut modifier le solde d'un wallet (GoPlus) — vecteur de perte totale")

    # Concentration (whale) guardrail
    if ctx.top_holder_pct is None:
        reasons.append("distribution des holders inconnue (non confirmable)")
    elif ctx.top_holder_pct > max_top_holder_pct:
        reasons.append(
            f"holder dominant {ctx.top_holder_pct:.0f}% > {max_top_holder_pct:.0f}% (risque de dump)"
        )

    passed = (
        ctx.valid_address
        and ctx.best_pair is not None
        and (liq >= min_liquidity_usd or liquidity_bypass)
        and ctx.security_score >= min_score
        and ctx.lite_verdict == "SAFE"
        and not wash_trading
        and liquidity_stability_confirmed is not False
        and (ctx.contract_verified is True or not require_verified)
        and not mint_blocks
        and ctx.has_blacklist is not True
        and ctx.has_disable_transfers is not True
        and ctx.top_holder_pct is not None
        and ctx.top_holder_pct <= max_top_holder_pct
        and ctx.is_honeypot is not True
        and ctx.cannot_sell is not True
        and (ctx.sell_tax is None or ctx.sell_tax <= DEFAULT_MAX_SELL_TAX)
        and ctx.hidden_owner is not True
        and ctx.can_take_back_ownership is not True
        and ctx.slippage_modifiable is not True
        and ctx.owner_change_balance is not True
    )
    if passed:
        reasons = [
            f"screené : score {ctx.security_score}/95, liquidité ${liq:,.0f}, "
            f"vérifié, holder max {ctx.top_holder_pct:.0f}%, verdict SAFE"
        ]
        # Never silent (item #5): crossing the liquidity floor stays visible
        # even on a successful pass, not just on a rejection.
        if liquidity_bypass:
            reasons.append(
                f"liquidité faible ${liq:,.0f} < ${min_liquidity_usd:,.0f} tolérée "
                "(score/verdict/mint propres)"
            )

    # HARD failure = a MALICIOUS mechanism confirmed in the contract itself
    # (the code never "heals" with time -- explicit operator decision, 07/10:
    # "if there's amazing technology but flaws in the contract we drop it, no
    # risk taken, there are plenty of other projects out there"). NOT a hard
    # failure: liquidity, DEX pair, contract verification, holder
    # concentration -- these are INVESTMENT ASPECTS that evolve with the
    # project's maturity (same principle applies "like any other token," not
    # just bonding). These candidates stay 'pending' (retry): never banned
    # forever over a mere current market state.
    hard_fail = (not passed) and (
        (not ctx.valid_address)
        or mint_blocks_confirmed(ctx)
        or (ctx.has_blacklist is True)
        or (ctx.has_disable_transfers is True)
        or (ctx.is_honeypot is True)
        or (ctx.cannot_sell is True)
        or (ctx.sell_tax is not None and ctx.sell_tax > DEFAULT_MAX_SELL_TAX)
        or (ctx.hidden_owner is True)
        or (ctx.can_take_back_ownership is True)
        or (ctx.slippage_modifiable is True)
        or (ctx.owner_change_balance is True)
    )

    return ScreenResult(
        contract=ctx.contract,
        passed=passed,
        security_score=ctx.security_score,
        liquidity_usd=liq,
        verdict=ctx.lite_verdict,
        reasons=reasons,
        hard_fail=hard_fail,
    )


def mint_blocks_confirmed(ctx: TokenScanContext) -> bool:
    """True if the mint is CONFIRMED controlled by a dev (EOA owner) — not 'unknown'.

    'unknown' (authority unresolved due to unavailability) stays a SOFT
    failure: we don't ban permanently, we'll retry.
    """
    return ctx.has_mint is True and ctx.mint_authority == "eoa"
