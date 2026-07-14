"""Client DefiLlama (lecture seule, public, sans clé) -- classement TVL des
chaînes EVM pour le scan dynamique de /walletscore (#157, 14/07).

Doctrine « dôme » (identique à blockscout.py/geckoterminal.py/dexscreener.py/
coinmarketcap.py) :
- 429 : backoff exponentiel, 3 tentatives max, puis abandon sans bloquer le pipeline.
- Timeout / 5xx : 1 retry après 5s, puis dégradation explicite (`None`).
- Aucune donnée manquante n'est jamais remplacée par une supposition.

Ce module ne connaît RIEN de SQLite -- pur client HTTP, comme les autres
clients de ce dossier. La mise en cache (table `wallet_scoring_chain_ranking`)
vit dans `smart_money.py`, qui orchestre l'appel réseau + l'écriture DB.

Le filtre aux chaînes confirmées se fait via `blockscout.CHAIN_IDS` -- SEULE
source de vérité, jamais un registre dupliqué ici (une deuxième copie aurait
pu diverger silencieusement, comme le "bnb" oublié dans `DEFAULT_SCAN_CHAINS`
avant sa correction ce soir)."""

from __future__ import annotations

import asyncio
import logging

import httpx

logger = logging.getLogger(__name__)

UNAVAILABLE = "donnée DefiLlama indisponible"

BASE_URL = "https://api.llama.fi"


async def _get_json(path: str) -> tuple[object | None, str | None]:
    """GET avec retry sur 429/5xx/timeout -- même politique que les autres
    clients de ce dossier."""
    url = f"{BASE_URL}{path}"
    attempt_429 = 0
    timeout_retried = False

    while True:
        try:
            async with httpx.AsyncClient(timeout=18.0) as client:
                response = await client.get(url, headers={"Accept": "application/json"})
        except httpx.TransportError as exc:
            if not timeout_retried:
                timeout_retried = True
                await asyncio.sleep(5.0)
                continue
            logger.warning("defillama: timeout sur %s -> %s", url, exc)
            return None, f"{UNAVAILABLE} (timeout DefiLlama)"

        if response.status_code == 429:
            attempt_429 += 1
            if attempt_429 >= 3:
                logger.warning("defillama: HTTP 429 sur %s apres %s tentatives", url, attempt_429)
                return None, f"{UNAVAILABLE} (rate limit DefiLlama)"
            await asyncio.sleep(0.5 * (2**attempt_429))
            continue

        if response.status_code >= 500:
            if not timeout_retried:
                timeout_retried = True
                await asyncio.sleep(5.0)
                continue
            logger.warning("defillama: HTTP %s sur %s", response.status_code, url)
            return None, f"{UNAVAILABLE} (erreur serveur DefiLlama {response.status_code})"

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.warning("defillama: %s", exc)
            return None, f"{UNAVAILABLE} ({exc})"

        return response.json(), None


async def fetch_chain_tvl_ranking() -> list[tuple[str, float]] | None:
    """Classement TVL des chaînes ARIA confirmées, trié décroissant.

    GET ``/v2/chains`` (public, sans clé), filtre par ``chainId`` numérique
    (jamais par ``name`` -- les libellés DefiLlama ne suivent pas toujours le
    vocabulaire ARIA, ex. "ZKsync Era" vs notre "zksync") contre
    ``blockscout.CHAIN_IDS``, la seule source de vérité des chaînes
    confirmées interrogeables (Blockscout × GeckoTerminal, établi le 14/07).

    Retourne ``None`` sur tout échec réseau ou forme de réponse inattendue --
    jamais une liste vide confondue silencieusement avec "TVL nulle partout"."""
    from aria_core.services.blockscout import CHAIN_IDS

    data, error = await _get_json("/v2/chains")
    if error is not None:
        logger.warning("defillama: classement TVL indisponible -> %s", error)
        return None
    if not isinstance(data, list):
        logger.warning("defillama: réponse /v2/chains de forme inattendue")
        return None

    chain_id_to_name = {cid: name for name, cid in CHAIN_IDS.items()}
    ranked: dict[str, float] = {}
    for entry in data:
        if not isinstance(entry, dict):
            continue
        chain_id = entry.get("chainId")
        name = chain_id_to_name.get(chain_id)
        if name is None:
            continue
        try:
            tvl = float(entry.get("tvl") or 0.0)
        except (TypeError, ValueError):
            tvl = 0.0
        ranked[name] = tvl

    return sorted(ranked.items(), key=lambda item: item[1], reverse=True)
