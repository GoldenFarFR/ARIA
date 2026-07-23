"""Liquidity depth — a market too thin relative to its valuation is fragile.

Operator intuition: "100k market cap for 20k liquidity isn't great; you need
at least 30-40k." In other words, liquidity should represent a **healthy
share** of the capitalization (liquidity / market cap ratio). A low ratio = thin
market = high slippage, easy to dump, easy to manipulate.

This is NOT an absolute threshold: it's modulated **case by case depending on the
launchpad** (a bonding curve deliberately starts thin; Bankr puts a lot of
liquidity in). Pure and deterministic. Data-gated: with no known market cap, no verdict.
"""
from __future__ import annotations

from dataclasses import dataclass

# Default floor: liquidity must be worth >= 30% of market cap (operator
# intuition: 100k mcap -> 30-40k liq minimum). Overridable per launchpad.
#
# 07/23 -- structural tension identified by the stress-test (Codex Part 11):
# this threshold mechanically excludes a small honest team with a low treasury
# (it can't afford to lock 30% of its market cap in DEX liquidity),
# in direct tension with the "sub-$1M builders" thesis. Explicitly
# escalated to the operator -- confirmed decision: KEEP THE THRESHOLD AS-IS,
# priority to anti-rug security over a relaxation that would weaken an
# anti-manipulation protection. Not an oversight, a settled tradeoff.
DEFAULT_MIN_RATIO = 0.30


@dataclass(frozen=True)
class LiquidityDepth:
    """Is the market deep enough for the valuation?"""

    ratio: float | None            # liquidity / market cap
    healthy: bool | None           # None if undeterminable (unknown mcap)
    min_ratio: float
    note: str = ""


def assess_liquidity_depth(
    liquidity_usd: float | None,
    market_cap_usd: float | None,
    *,
    min_ratio: float = DEFAULT_MIN_RATIO,
    bonding_curve: bool = False,
) -> LiquidityDepth:
    """Liquidity/mcap ratio and depth verdict. ``healthy=None`` if not computable.

    ``bonding_curve=True``: on a bonding curve (Virtuals...), liquidity
    grows EXPONENTIALLY with progression — it deliberately starts thin.
    The ratio is therefore NOT a fragility signal here: the ratio is returned for
    info but ``healthy=None`` (never penalize a bonding token for this).
    """
    if not market_cap_usd or market_cap_usd <= 0 or liquidity_usd is None:
        return LiquidityDepth(ratio=None, healthy=None, min_ratio=min_ratio,
                              note="market cap ou liquidité indisponible")
    ratio = liquidity_usd / market_cap_usd
    if bonding_curve:
        return LiquidityDepth(
            ratio=round(ratio, 3), healthy=None, min_ratio=min_ratio,
            note=(
                f"liquidité {ratio * 100:.0f}% de la market cap — courbe de bonding "
                "(liquidité exponentielle, mince au départ : ratio non pertinent ici)"
            ),
        )
    healthy = ratio >= min_ratio
    pct = ratio * 100
    if healthy:
        note = f"liquidité {pct:.0f}% de la market cap (marché correctement profond)"
    else:
        note = (
            f"liquidité seulement {pct:.0f}% de la market cap "
            f"(< {min_ratio * 100:.0f}% attendu — marché mince, slippage/dump faciles)"
        )
    return LiquidityDepth(ratio=round(ratio, 3), healthy=healthy, min_ratio=min_ratio, note=note)
