"""Sizing du montant de trade pour le pilote agent-wallet (#203) -- calcule
combien engager sur le solde réel, jamais une valeur inventée. N'exécute
rien : le résultat est passé à ``agent_wallet_pilot.attempt_swap()``, qui
revérifie indépendamment contre le solde réel ET ``MAX_TRANSACTION_USD`` avant
toute exécution (double vérification volontaire, pas une redondance
accidentelle -- protège contre un solde qui change entre le sizing et
l'exécution).

Décision opérateur (16/07, #203) : 3% du solde réel par défaut, plafonné à
``MAX_TRANSACTION_USD``. Sur le solde cible du pilote (10-15$), ça produit un
montant de l'ordre de 30-45 centimes par trade -- voulu, pas un problème :
l'objectif de ce pilote précis est un montant sans conséquence en cas
d'erreur (cf. docs/pilote-agent-wallet-10usd.md). Pas de variable
d'environnement pour surcharger le pourcentage dans cette V1 (scope minimal,
décision opérateur explicite) -- ``pct`` reste un paramètre d'appel Python.
"""
from __future__ import annotations

from aria_core.agent_wallet_pilot import MAX_TRANSACTION_USD, BalanceFn

DEFAULT_SIZING_PCT = 0.03  # 3% -- décision opérateur explicite (16/07, #203)


async def size_trade_usd(
    *,
    balance_fn: BalanceFn,
    pct: float = DEFAULT_SIZING_PCT,
    max_usd: float = MAX_TRANSACTION_USD,
) -> float | None:
    """Montant à engager = ``min(solde réel * pct, max_usd)``. ``None`` si le
    solde est indisponible, nul ou négatif, ou si ``pct`` n'est pas positif
    (fail-closed, même doctrine que le reste du module) -- jamais un montant
    de repli inventé."""
    if pct <= 0:
        return None
    try:
        balance_usd = await balance_fn()
    except Exception:
        return None
    if balance_usd is None or balance_usd <= 0:
        return None
    return min(balance_usd * pct, max_usd)
