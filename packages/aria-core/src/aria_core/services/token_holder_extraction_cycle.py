"""Extraction récurrente des holders Blockscout Pro (x402) -- coordonne la
croissance de ``token_holder_intel`` (147 tokens au 21/07, extraits en
one-shot manuel) avec la découverte de candidats smart-money
(``smart_money_leaderboard.discover_and_enqueue_candidates``, qui lit la MÊME
table). Réponse à la demande opérateur (21/07) : « il faut coordonner les
scans de token / quantité de holders absorbés et à traiter vers les smart ».

Profondeur par palier de capitalisation (décision opérateur explicite, 21/07) :
top 500 holders pour >=1000M$ de mcap, top 300 pour >=500M$, top 200 pour
>=100M$, top 100 pour tout le reste (y compris mcap inconnu -- jamais un tier
supérieur inventé faute de donnée). Réutilise ``coingecko.coingecko_client``
(déjà construit) pour la capitalisation, ``blockscout_x402.
get_token_holders_x402_paginated`` (déjà construit, un paiement par page de
50) pour l'extraction elle-même.

Coût réel borné : ``MAX_TOKENS_PER_CYCLE`` bas + le plafond hebdomadaire
PARTAGÉ (``x402_budget.py``, 5$/semaine, déjà fail-closed) bornent le pire
cas -- aucun plafond dédié supplémentaire ici, cohérent avec le reste des
consommateurs x402 (twit.sh/cybercentry/ottoai).

Sélection des tokens : uniquement ceux JAMAIS ENCORE extraits
(``token_holder_intel``), triés par liquidité connue décroissante
(``screened_token.liquidity_usd``, déjà calculée par ``safety_screen``,
aucun nouvel appel). LIMITE HONNÊTE : la ré-extraction des tokens déjà
couverts (staleness -- holders qui évoluent dans le temps) n'est PAS
construite ici -- avec >1300 tokens jamais encore touchés au 21/07, ça
laisse une large marge avant que ça devienne pertinent."""
from __future__ import annotations

import logging
import os

import aiosqlite

from aria_core.paths import aria_db_path

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())

# Sobriété -- extraction en masse coûte du vrai argent (x402), contrairement
# aux autres cycles de découverte/scoring smart-money. Bas par défaut, cf.
# MAX_WALLETS_PER_CYCLE=1 (wallet_scan_queue.py) comme précédent de prudence.
MAX_TOKENS_PER_CYCLE = 2

# Paliers de profondeur par capitalisation (décision opérateur, 21/07) --
# ordre décroissant, le premier seuil franchi l'emporte. Capitalisation
# inconnue (CoinGecko indisponible/token non listé) -> jamais un tier
# supérieur inventé, retombe sur le plancher (100).
_TIERS = (
    (1_000_000_000.0, 500),
    (500_000_000.0, 300),
    (100_000_000.0, 200),
)
_DEFAULT_TARGET_COUNT = 100


def token_holder_extraction_enabled() -> bool:
    return os.environ.get("ARIA_TOKEN_HOLDER_EXTRACTION_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def target_holder_count(market_cap_usd: float | None) -> int:
    if market_cap_usd is None:
        return _DEFAULT_TARGET_COUNT
    for threshold, count in _TIERS:
        if market_cap_usd >= threshold:
            return count
    return _DEFAULT_TARGET_COUNT


async def _select_next_tokens(limit: int) -> list[tuple[str, str]]:
    """Contrats Base jamais encore extraits (``token_holder_intel``), triés
    par liquidité déjà connue décroissante (aucun nouvel appel réseau).
    Filtré côté Python (pas un anti-join SQL) -- passe par l'accesseur public
    ``list_extracted_contracts`` plutôt que de dépendre du détail interne de
    la table ``token_holder_intel`` (qui peut ne pas encore exister du tout
    si aucune extraction n'a jamais eu lieu)."""
    from aria_core import token_holder_intel

    already_extracted = {
        c["contract"].lower() for c in await token_holder_intel.list_extracted_contracts("base")
    }
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            rows = await (
                await db.execute(
                    "SELECT contract, symbol FROM screened_token WHERE network = 'base' "
                    "ORDER BY liquidity_usd DESC"
                )
            ).fetchall()
    except Exception as exc:  # noqa: BLE001 -- ex. screened_token pas encore créée (aucun screening jamais fait)
        logger.info("token_holder_extraction: lecture screened_token indisponible (%s)", exc)
        return []

    selected: list[tuple[str, str]] = []
    for contract, symbol in rows:
        if (contract or "").lower() in already_extracted:
            continue
        selected.append((contract, symbol or ""))
        if len(selected) >= limit:
            break
    return selected


async def run_token_holder_extraction_cycle(notifier=None) -> dict:
    """Un tour : sélectionne jusqu'à ``MAX_TOKENS_PER_CYCLE`` tokens jamais
    encore extraits, détermine leur profondeur cible via la capitalisation
    (CoinGecko, gratuit), extrait leurs holders (Blockscout x402, payant,
    paginé) et les stocke -- la découverte smart-money (cycle séparé,
    ``smart_money_leaderboard_discovery_cycle``) lira ensuite cette même
    table à son prochain passage, aucune coordination explicite nécessaire
    au-delà de partager la même table."""
    if not token_holder_extraction_enabled():
        return {"outcome": "skipped", "reason": "gate_off"}

    from aria_core import outgoing_pause

    if outgoing_pause.is_paused():
        return {"outcome": "skipped", "reason": "paused"}

    candidates = await _select_next_tokens(MAX_TOKENS_PER_CYCLE)
    if not candidates:
        return {"outcome": "no_candidate"}

    from aria_core import token_holder_intel
    from aria_core.services import blockscout_x402
    from aria_core.services.coingecko import coingecko_client

    processed: list[dict] = []
    for contract, symbol in candidates:
        fundamentals = await coingecko_client.get_token_fundamentals(contract, platform_id="base")
        market_cap = fundamentals.market_cap_usd if fundamentals.available else None
        target = target_holder_count(market_cap)
        try:
            holders = await blockscout_x402.get_token_holders_x402_paginated(
                contract, chain="base", target_count=target, token_symbol=symbol,
            )
        except Exception as exc:  # noqa: BLE001 -- jamais bloquant, token suivant
            logger.warning("token_holder_extraction: échec pour %s (%s)", contract, exc)
            holders = []
        written = await token_holder_intel.store_holders(contract, "base", holders) if holders else 0
        processed.append({
            "contract": contract, "symbol": symbol,
            "market_cap_usd": market_cap, "target_count": target, "holders_stored": written,
        })

    total_stored = sum(p["holders_stored"] for p in processed)
    if notifier is not None and total_stored:
        detail = ", ".join(
            f"{p['symbol'] or p['contract'][:10]} ({p['holders_stored']}/{p['target_count']})"
            for p in processed
        )
        await notifier(
            f"🧬 Extraction holders -- {total_stored} holder(s) stocké(s) sur "
            f"{len(processed)} token(s) : {detail}."
        )

    return {"outcome": "ok", "tokens_processed": processed}
