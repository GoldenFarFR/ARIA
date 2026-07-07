"""Profondeur de liquidité — un marché trop mince par rapport à la valorisation est fragile.

Intuition opérateur : « 100k de market cap pour 20k de liquidité, c'est pas ouf ;
il faut au moins 30-40k ». Autrement dit, la liquidité doit représenter une **part
saine** de la capitalisation (ratio liquidité / market cap). Un ratio faible = marché
mince = slippage élevé, facile à dumper, facile à manipuler.

Ce n'est PAS un seuil absolu : il se module **au cas par cas selon le launchpad**
(une courbe de bonding démarre volontairement mince ; Bankr met énormément de
liquidité). Pur et déterministe. Data-gated : sans market cap connu, pas de verdict.
"""
from __future__ import annotations

from dataclasses import dataclass

# Plancher par défaut : la liquidité doit valoir >= 30 % de la market cap (intuition
# opérateur : 100k mcap -> 30-40k liq mini). Surchargeable selon le launchpad.
DEFAULT_MIN_RATIO = 0.30


@dataclass(frozen=True)
class LiquidityDepth:
    """Le marché est-il assez profond pour la valorisation ?"""

    ratio: float | None            # liquidité / market cap
    healthy: bool | None           # None si indéterminable (mcap inconnu)
    min_ratio: float
    note: str = ""


def assess_liquidity_depth(
    liquidity_usd: float | None,
    market_cap_usd: float | None,
    *,
    min_ratio: float = DEFAULT_MIN_RATIO,
    bonding_curve: bool = False,
) -> LiquidityDepth:
    """Ratio liquidité/mcap et verdict de profondeur. ``healthy=None`` si non calculable.

    ``bonding_curve=True`` : sur une courbe de bonding (Virtuals...), la liquidité
    croît EXPONENTIELLEMENT avec la progression — elle démarre volontairement mince.
    Le ratio n'est donc PAS un signal de fragilité ici : on renvoie le ratio pour
    info mais ``healthy=None`` (ne jamais pénaliser un token en bonding pour ça).
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
