"""Sourcing automatique de wallets candidats depuis l'historique propre d'ARIA
(15/07, suite #157/#181 -- réponse à « qui va trouver les wallets ? »).

Zéro nouvelle dépendance externe, zéro coût récurrent (contrairement à
Nansen/Dune, écartés ou mis en secours pour cette raison) : repère les tokens
qu'ARIA a déjà jugés gagnants et liste qui les détient ENCORE aujourd'hui
(`blockscout.get_token_holders`, déjà construit) -- signal de conviction (pas
revendu au premier soubresaut), pas une découverte de marché large comme un
service tiers. Enfile ces adresses dans `wallet_scan_queue.py` -- une source de
CANDIDATS À SCORER, jamais un signal de trading en lui-même : le score obtenu
via `/walletscore`/le cycle de fond reste le seul signal qui compte, même
doctrine que l'ajout manuel via `/walletqueue`.

DEUX sources de "token gagnant" combinées (15/07, constat opérateur -- une
seule source serait restée vide des semaines) : `vc_predictions` clôturées
(horizon 30j, résolution lente -- 0 pronostic clôturé au 11/07 dans le dernier
audit connu) ET `paper_trader` positions clôturées (déjà actif en prod,
résout bien plus vite via stop suiveur/prise de profit sur prix réel). Cf.
`list_strong_performers` pour le détail.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import aiosqlite

from aria_core.paths import aria_db_path

DB_PATH = str(aria_db_path())

# Seuil de départ (+100%, soit x2) -- ajustable comme tout seuil ARIA, pas une
# vérité gravée. Un token qui n'a "que" doublé reste un signal honnête, pas un
# jugement sur ce qui compte comme "gagnant" pour la thèse VC elle-même.
MIN_OUTCOME_PCT_STRONG_PERFORMER = 100.0

# Ne pas noyer la file avec un seul token très détenu.
MAX_HOLDERS_PER_TOKEN = 15

_DEAD_ADDRESSES = {
    "0x0000000000000000000000000000000000000000",
    "0x000000000000000000000000000000000000dead",
}


def wallet_candidate_sourcing_enabled() -> bool:
    return os.environ.get("ARIA_WALLET_CANDIDATE_SOURCING_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "CREATE TABLE IF NOT EXISTS wallet_candidate_sourcing_processed ("
            "contract TEXT PRIMARY KEY, sourced_at TEXT NOT NULL)"
        )
        await db.commit()


async def _already_sourced(contract: str) -> bool:
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (
            await db.execute(
                "SELECT 1 FROM wallet_candidate_sourcing_processed WHERE contract = ?",
                (contract.lower(),),
            )
        ).fetchone()
    return row is not None


async def _mark_sourced(contract: str) -> None:
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO wallet_candidate_sourcing_processed (contract, sourced_at) VALUES (?, ?)",
            (contract.lower(), datetime.now(timezone.utc).isoformat()),
        )
        await db.commit()


async def list_strong_performers(min_outcome_pct: float = MIN_OUTCOME_PCT_STRONG_PERFORMER) -> list[dict]:
    """Tokens qu'ARIA a déjà jugés gagnants, DEUX sources combinées (15/07,
    constat opérateur) :

    1. `vc_predictions` clôturées (horizon 30j, résolution manuelle/lente --
       0 pronostic clôturé au 11/07 dans le dernier audit connu, donc source
       quasi vide seule pendant encore des semaines) ;
    2. `paper_trader` positions clôturées (déjà actif en prod, résout bien
       plus vite -- stop suiveur/prise de profit sur prix réel, pas un
       horizon calendaire fixe) -- pas de colonne réseau dédiée, `network`
       vaut "base" par défaut (pool VC/trading dominant confirmé Base).

    Réutilise les deux modules tels quels, aucune table dupliquée."""
    from aria_core.paper_trader import get_closed_positions
    from aria_core.vc_predictions import list_all_predictions

    predictions = await list_all_predictions()
    from_predictions = [
        {"contract": p["contract"], "network": p.get("network") or "", "outcome_pct": p["outcome_pct"]}
        for p in predictions
        if p.get("status") == "closed"
        and p.get("outcome_pct") is not None
        and p["outcome_pct"] >= min_outcome_pct
        and p.get("contract")
    ]

    closed_positions = await get_closed_positions()
    from_paper_trading = [
        {"contract": pos["contract"], "network": "base", "outcome_pct": pos["pnl_pct"]}
        for pos in closed_positions
        if pos.get("pnl_pct") is not None
        and pos["pnl_pct"] >= min_outcome_pct
        and pos.get("contract")
    ]

    seen: set[str] = set()
    merged: list[dict] = []
    for entry in from_predictions + from_paper_trading:
        contract_l = entry["contract"].lower()
        if contract_l in seen:
            continue
        seen.add(contract_l)
        merged.append(entry)
    return merged


async def _holders_for_token(contract: str, network: str) -> list[str]:
    from aria_core.services.blockscout import get_blockscout_client

    chain = network or "base"
    client = get_blockscout_client(chain)
    result = await client.get_token_holders(contract)
    if not result.available:
        return []
    # Le plus gros détenteur est presque toujours le pool DEX/routeur ou une
    # allocation équipe verrouillée -- jamais un "smart wallet" individuel.
    # Heuristique volontairement simple (aucun appel API supplémentaire par
    # détenteur pour vérifier is_contract -- sobriété) : documentée comme
    # imparfaite, pas un filtre de sécurité -- le pire cas d'un faux négatif
    # ici est un scan /walletscore bruyant sur une adresse de contrat, jamais
    # un risque.
    holders = [
        h for h in result.holders
        if h.address and h.address.lower() not in _DEAD_ADDRESSES
    ][1:]
    return [h.address for h in holders[:MAX_HOLDERS_PER_TOKEN]]


async def run_wallet_candidate_sourcing_cycle(notifier=None) -> dict:
    """Un tour : traite TOUS les tokens gagnants jamais encore sourcés en une
    passe (15/07, constat opérateur -- un plafond d'un seul token/cycle aurait
    créé un goulot artificiel indépendant du vrai débit de données ; si
    plusieurs gagnants sont déjà en attente, les traiter tous MAINTENANT plutôt
    que d'étaler sur des cycles de 3h chacun). Pour chacun : enfile ses
    détenteurs actuels (hors plus gros détenteur/adresses mortes) dans
    `wallet_scan_queue.py`. Triple gate -- `ARIA_WALLET_CANDIDATE_SOURCING_ENABLED`
    en plus de `ARIA_WALLET_SCAN_QUEUE_ENABLED`/`ARIA_WALLET_SCORING_ENABLED`
    (tous OFF par défaut) -- fail-closed, respecte le kill-switch.

    Limite honnête : ceci ne garantit AUCUN débit minimum (ex. "5 tokens/semaine")
    -- ça dépend du nombre réel de trades gagnants d'ARIA sur la période, pas
    d'un réglage de code. Si le débit réel reste insuffisant une fois déployé,
    le seul levier honnête est d'abaisser `MIN_OUTCOME_PCT_STRONG_PERFORMER`
    (moins de conviction exigée par token) -- décision opérateur, pas faite
    silencieusement ici."""
    if not wallet_candidate_sourcing_enabled():
        return {"outcome": "skipped", "reason": "gate_off"}

    from aria_core.services.smart_money import wallet_scoring_enabled
    from aria_core.services.wallet_scan_queue import enqueue_wallets, wallet_scan_queue_enabled

    if not wallet_scan_queue_enabled() or not wallet_scoring_enabled():
        return {"outcome": "skipped", "reason": "downstream_disabled"}

    from aria_core import outgoing_pause

    if outgoing_pause.is_paused():
        return {"outcome": "skipped", "reason": "paused"}

    performers = await list_strong_performers()
    new_candidates = [p for p in performers if not await _already_sourced(p["contract"])]
    if not new_candidates:
        return {"outcome": "no_new_performer"}

    processed: list[dict] = []
    total_sourced = 0
    for candidate in new_candidates:
        holders = await _holders_for_token(candidate["contract"], candidate.get("network") or "")
        await _mark_sourced(candidate["contract"])
        added = await enqueue_wallets(holders) if holders else []
        total_sourced += len(added)
        processed.append({"contract": candidate["contract"], "sourced": len(added)})

    if total_sourced and notifier is not None:
        detail = ", ".join(f"{p['contract'][:10]} ({p['sourced']})" for p in processed if p["sourced"])
        await notifier(
            f"🔍 Sourcing automatique -- {total_sourced} wallet(s) ajouté(s) à la file "
            f"depuis {len(processed)} token(s) gagnant(s) de l'historique ARIA : {detail}."
        )

    return {
        "outcome": "ok",
        "contract": processed[0]["contract"],
        "sourced": processed[0]["sourced"],
        "tokens_processed": processed,
        "total_sourced": total_sourced,
    }
