"""Boucle de décision autonome du pilote agent-wallet réel (18/07, décision
opérateur explicite "option 2" -- ARIA décide ET exécute SEULE, aucune
commande Telegram nécessaire par transaction). Design complet et historique
des décisions : `docs/pilote-agent-wallet-10usd.md` §8.

Réutilise entièrement le pipeline momentum déjà construit et testé pour le
paper-trading (honeypot GoPlus + R/R golden pocket/RSI + garde de sécurité
LLM, `momentum_entry.py`) -- aucune nouvelle logique de décision inventée
ici, seulement le câblage vers l'exécution réelle bornée
(`agent_wallet_pilot.attempt_swap`). Ce module ne connaît, lui, que
l'ORCHESTRATION : lire le solde réel, vérifier qu'aucune position n'est déjà
ouverte, dimensionner (règle déjà décidée le 16/07, #203), sourcer un
candidat, tenter le swap si BUY confirmé.

Base uniquement -- `agent_wallet_cdp_adapter.py` est structurellement
Base-only (`USDC_BASE_ADDRESS` codé en dur), cohérent aussi avec la décision
opérateur du 17/07 de garder Solana au même standard de sécurité (plus
restrictif en pratique, donc moins de candidats -- pas un problème ici, ce
pilote n'a de toute façon besoin que d'UN candidat par cycle).

v1 (18/07) : une seule entrée à la fois, AUCUNE sortie automatique -- une
position déjà ouverte (n'importe quel token autre qu'USDC détenu) bloque
toute nouvelle tentative jusqu'à une décision future (manuelle ou une v2 avec
logique de sortie, pas construite ici). Le volet "x402 débloque une décision
bloquée par manque de données" (demandé par l'opérateur le 18/07) est
DIFFÉRÉ -- `ethereum-token-verification` (le seul endpoint qui aurait pu
aider) reste confirmé cassé depuis le 17/07, cf. doc §8.7.
"""
from __future__ import annotations

import logging

from aria_core import agent_wallet_cdp_adapter, agent_wallet_log, agent_wallet_pilot, agent_wallet_sizing
from aria_core.agent_wallet_monitor import get_wallet_balance_summary

logger = logging.getLogger(__name__)

CHAIN = "base"
SWAP_FAILURE_COOLDOWN_MINUTES = 60
MAX_CANDIDATES_PER_CYCLE = 5

# Raisons de HOLD qui signalent un manque de DONNÉES plutôt qu'un rejet dur --
# référence unique pour un futur volet x402-débloque (différé, doc §8.7), pas
# encore exploitée dans cette v1. Gardée ici pour ne pas redéfinir cette liste
# ailleurs le jour où ce volet sera construit.
DATA_GAP_HOLD_REASONS = frozenset({"ohlcv_unavailable"})


async def run_agent_wallet_pilot_cycle() -> dict:
    """Un tour de décision. Ne lève jamais d'exception (dégradation douce,
    même doctrine que le reste du heartbeat) -- toute panne se traduit par un
    ``outcome`` explicite, jamais un crash silencieux du tick heartbeat."""
    if not agent_wallet_pilot.agent_wallet_pilot_enabled():
        return {"outcome": "disabled"}

    try:
        summary = await get_wallet_balance_summary()
    except Exception as exc:  # noqa: BLE001 -- fail-closed, jamais un solde inventé
        logger.warning("agent_wallet_pilot_cycle: solde indisponible (%s)", exc)
        return {"outcome": "balance_unavailable", "reason": str(exc)}

    other_tokens = summary.get("other_tokens")
    if other_tokens is None:
        return {"outcome": "balance_unavailable"}
    if other_tokens:
        return {
            "outcome": "position_open",
            "held": [t.get("symbol") for t in other_tokens],
        }

    sized_usd = await agent_wallet_sizing.size_trade_usd(
        balance_fn=agent_wallet_cdp_adapter.usdc_balance_usd,
    )
    if sized_usd is None:
        return {"outcome": "no_balance"}

    from aria_core import momentum_entry

    try:
        found = await momentum_entry.discover_momentum_candidates(chains=(CHAIN,))
    except Exception as exc:  # noqa: BLE001 -- une panne de sourcing n'est pas fatale
        logger.info("agent_wallet_pilot_cycle: sourcing échoué (%s)", exc)
        return {"outcome": "sourcing_failed", "reason": str(exc)}

    checked = 0
    for candidate in found[:MAX_CANDIDATES_PER_CYCLE]:
        contract = (candidate.get("contract") or "").strip().lower()
        if not contract:
            continue
        checked += 1

        if await agent_wallet_log.recent_failed_swap(
            contract, within_minutes=SWAP_FAILURE_COOLDOWN_MINUTES,
        ):
            continue

        try:
            sig = await momentum_entry.evaluate_momentum_entry(contract, CHAIN)
        except Exception as exc:  # noqa: BLE001 -- une évaluation cassée ne bloque pas le cycle
            logger.info("agent_wallet_pilot_cycle: évaluation %s échouée (%s)", contract, exc)
            continue
        if not sig or sig.get("action") != "BUY":
            continue

        result = await agent_wallet_pilot.attempt_swap(
            chain=CHAIN,
            token_in=agent_wallet_cdp_adapter.USDC_BASE_ADDRESS,
            token_out=contract,
            amount_in_usd=sized_usd,
            wallet_address=summary.get("wallet_address") or "",
            balance_fn=agent_wallet_cdp_adapter.usdc_balance_usd,
            swap_fn=agent_wallet_cdp_adapter.execute_swap,
        )
        return {
            "outcome": result.status,
            "contract": contract,
            "symbol": sig.get("symbol", ""),
            "amount_usd": sized_usd,
            "reason": result.reason,
            "tx_hash": result.tx_hash,
        }

    return {"outcome": "no_candidate", "checked": checked}


def format_agent_wallet_swap_alert(result: dict) -> str:
    """Alerte Telegram -- CAPITAL RÉEL, jamais confondue avec les alertes
    "🧪 SIMULATION" du paper-trading (préfixe et libellé délibérément
    différents). ``""`` si rien d'assez notable pour notifier (ex. aucun
    candidat trouvé ce cycle -- éviter le bruit sur du "rien ne s'est passé")."""
    outcome = result.get("outcome")
    if outcome in ("disabled", "no_candidate", "position_open"):
        return ""
    symbol = result.get("symbol") or (result.get("contract") or "")[:10]
    if outcome == "ok":
        return (
            "🔴 ARGENT RÉEL — pilote agent-wallet\n"
            f"SWAP RÉUSSI {symbol}\n"
            f"Contrat {result.get('contract', '')}\n"
            f"Montant {result.get('amount_usd', 0):.2f} $ · tx {result.get('tx_hash', '')}"
        )
    if outcome == "failed":
        return (
            "🔴 ARGENT RÉEL — pilote agent-wallet\n"
            f"SWAP ÉCHOUÉ {symbol}\n"
            f"Contrat {result.get('contract', '')}\n"
            f"Raison : {result.get('reason', '')}"
        )
    if outcome == "blocked":
        return (
            "🔴 ARGENT RÉEL — pilote agent-wallet\n"
            f"Swap bloqué : {result.get('reason', '')}"
        )
    return ""
