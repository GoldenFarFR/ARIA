"""Safety filter for the 15% niche — tokens STILL on a bonding curve.

The counterpart to ``safety_screen.py`` for pre-graduation candidates. The
standard filter ALWAYS requires a DEX pair (``ctx.best_pair is not None``): a
bonding token, with no DEX liquidity **by construction** (liquidity lives in
the curve, not a Uniswap pool, until graduation), would wrongly fail this
filter — not a legitimate rejection, a guaranteed false negative.

This module reuses EXACTLY the existing scan (``acp_onchain_scan.scan_base_token``,
which already resolves ``ctx.bonding_phase`` / ``ctx.bonding_progress`` /
``ctx.mint_authority`` / ``ctx.dev_signal`` — see task #10 delivered 07/09) and
only adds an adapted THRESHOLD, never requiring DEX liquidity or a GoPlus
honeypot check (signals that don't exist before graduation).

## Guardrails (same principles as ``safety_screen``: "does the dev keep power?")

- **confirmed** (final rejection, ``hard_fail=True``): mint in the hands of a
  dev wallet (``eoa``), blacklist, transfer disabling, ``DANGER`` scan
  verdict, dev behavior judged ``concern``.
- **unavailable data** (soft failure, ``hard_fail=False`` — retry later):
  contract not yet verified/unknown, undeterminable mint authority
  (``unknown``), ``CAUTION`` verdict (graduation progress still low or few
  holders — not a confirmed negative signal, just not mature enough yet).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from aria_core.skills.acp_onchain_scan import TokenScanContext

# Same neutralized authorities as safety_screen.py (mint driven by the
# protocol, renounced, or a locked/multisig contract — never a plain dev wallet).
_MINT_AUTHORITY_OK = frozenset({"renounced", "launchpad", "contract"})

DEFAULT_MIN_SCORE = 55  # below the scan's SAFE threshold (70) but above pure CAUTION


@dataclass(frozen=True)
class BondingScreenResult:
    """Bonding filter verdict for a candidate, with its factual reasons."""

    contract: str
    passed: bool
    security_score: int
    verdict: str  # lite_verdict du scan : SAFE / CAUTION / DANGER
    bonding_progress: float | None
    reasons: list[str] = field(default_factory=list)
    hard_fail: bool = False


def bonding_safety_screen(
    ctx: TokenScanContext, *, min_score: int = DEFAULT_MIN_SCORE
) -> BondingScreenResult:
    """Decides whether a bonding candidate enters the 15% niche (``base-bonding``).

    Assumes ``ctx`` comes from ``scan_base_token(contract, include_dev_behavior=True)``
    on a contract WITHOUT a DEX pair (otherwise ``ctx.bonding_phase`` stays
    ``False`` and this filter rejects — use the standard ``safety_screen`` instead).
    """
    reasons: list[str] = []
    hard_reasons: list[str] = []
    soft_reasons: list[str] = []

    if not ctx.valid_address:
        hard_reasons.append("invalid contract address")
    if not ctx.bonding_phase:
        # Not applicable: either graduated (has a DEX pair -> standard
        # safety_screen), or Virtuals status unresolved (best-effort
        # unavailability -> retry).
        soft_reasons.append("bonding status unconfirmed (ctx.bonding_phase=False)")

    if ctx.contract_verified is None:
        soft_reasons.append("contract verification unavailable")
    elif ctx.contract_verified is False:
        # An investment consideration (can be fixed by the dev later), NOT a
        # malicious mechanism -- soft failure, never a final ban (operator
        # decision 07/10, same principle as safety_screen.py).
        soft_reasons.append("contract not verified (opaque code) -- to be re-checked")

    mint_authority = ctx.mint_authority or "unknown"
    if ctx.has_mint is True:
        if mint_authority == "unknown":
            soft_reasons.append("mint authority undeterminable")
        elif mint_authority not in _MINT_AUTHORITY_OK:
            detail = ctx.mint_authority_detail or "the dev can create tokens"
            hard_reasons.append(f"mint function controlled by a dev ({detail})")

    if ctx.has_blacklist is True:
        hard_reasons.append("blacklist function present (the dev can block sells)")
    if ctx.has_disable_transfers is True:
        hard_reasons.append("transfer disabling possible (honeypot lever)")

    if ctx.lite_verdict == "DANGER":
        hard_reasons.append(f"scan verdict 'DANGER' (score {ctx.security_score})")
    elif ctx.lite_verdict == "CAUTION":
        soft_reasons.append(f"scan verdict 'CAUTION' (score {ctx.security_score} < {min_score})")
    elif ctx.security_score < min_score:
        soft_reasons.append(f"security score {ctx.security_score} < {min_score}")

    if ctx.dev_signal == "concern":
        hard_reasons.append("dev behavior judged 'concern' (see dev_wallet)")
    elif ctx.dev_signal in (None, "unknown"):
        soft_reasons.append("dev behavior unresolved")

    reasons = [*hard_reasons, *soft_reasons]
    passed = not hard_reasons and not soft_reasons

    return BondingScreenResult(
        contract=ctx.contract,
        passed=passed,
        security_score=ctx.security_score,
        verdict=ctx.lite_verdict,
        bonding_progress=ctx.bonding_progress,
        reasons=reasons,
        hard_fail=bool(hard_reasons),
    )
