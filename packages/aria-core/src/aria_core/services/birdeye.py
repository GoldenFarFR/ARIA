"""Client Birdeye (lecture seule) -- découverte en masse de tokens Base (21/07),
réponse au vrai goulot d'étranglement trouvé le même jour : ``discover_momentum_
candidates()`` ne trouve que ~18 candidats bruts par cycle (sources DexScreener
boosts/profils + GeckoTerminal pools, jamais une recherche filtrée exhaustive),
alors qu'un filtre manuel équivalent sur DexScreener (liquidité>=50k$, volume>=500$)
en trouve ~380-520 sur Base. DexScreener n'a AUCUNE API de recherche filtrée en
masse (confirmé plusieurs fois ce mois-ci) -- Birdeye si : ``/defi/v3/token/list``
(vérifié en direct 21/07, palier gratuit).

Vérifié en conditions réelles (21/07) : 520 tokens Base récupérés en 6 appels
paginés (liquidité>=50k$, volume 24h>=500$, mêmes seuils que le pipeline momentum
-- ``momentum_entry._MIN_LIQUIDITY_USD``/``_MIN_VOLUME_24H_USD``, jamais dupliqués
en dur ici, transmis par l'appelant).

Coût CU vérifié (docs.birdeye.so/docs/compute-unit-cost) : 75 CU/appel sur cet
endpoint. Palier gratuit "Standard" (30 000 CU/mois, 1 req/s = 60/min, confirmé sur
le dashboard réel) -- un scan complet (~6 appels) coûte ~450 CU, soit ~66 scans
complets/mois soutenables GRATUITEMENT (~2/jour) sans jamais toucher au palier
payant. Cache process-local 12h (2x/jour) dans ``momentum_entry.py`` pour ne
jamais rappeler cet endpoint à chaque cycle heartbeat (15 min, 96x/jour --
dépasserait le budget gratuit de plusieurs ordres de grandeur sans ce cache).

Doctrine « dôme » (identique à goplus.py/webacy.py/mobula.py) :
- Clé absente -- ``available=False`` immédiat, aucun appel réseau, jamais un
  blocage du pipeline (même famille de dégradation que le reste des sources de
  découverte, ``discover_momentum_candidates`` continue avec les autres sources).
- 429/5xx/timeout -- dégradation explicite, jamais une exception qui remonte.
- Pagination plafonnée (``_MAX_PAGES``) -- protection anti-boucle-infinie
  indépendante de ce que l'API renvoie, même patron que
  ``blockscout_x402._MAX_PAGES_PER_EXTRACTION``.

Throttle calibré à 90% du palier gratuit confirmé (1 req/s, dashboard réel
21/07) -- ``_MIN_INTERVAL_S = 1.11`` (même doctrine « 90% de la capacité réelle,
jamais devinée » que le reste du projet, cf. CLAUDE.md)."""
from __future__ import annotations

import asyncio
import logging
import os
import time

import httpx

logger = logging.getLogger(__name__)

UNAVAILABLE = "donnée Birdeye indisponible"

BASE_URL = "https://public-api.birdeye.so"

_MIN_INTERVAL_S = 1.11
_MAX_PAGES = 10
_PAGE_LIMIT = 100

_last_call_at = 0.0
_lock = asyncio.Lock()


def birdeye_api_key() -> str | None:
    return os.environ.get("BIRDEYE_API_KEY", "").strip() or None


def birdeye_available() -> bool:
    return bool(birdeye_api_key())


async def _throttle() -> None:
    global _last_call_at
    async with _lock:
        now = time.monotonic()
        wait = _MIN_INTERVAL_S - (now - _last_call_at)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_call_at = time.monotonic()


async def discover_base_tokens_bulk(
    *, min_liquidity_usd: float = 50_000.0, min_volume_24h_usd: float = 500.0,
) -> list[str]:
    """Liste paginée des adresses de tokens Base dont la liquidité ET le volume 24h
    dépassent les seuils fournis (mêmes seuils que le pipeline momentum, jamais
    dupliqués en dur -- l'appelant transmet les vraies constantes). Dégrade en
    liste vide sur toute panne -- jamais une exception, jamais un candidat inventé.
    Plafonné à ``_MAX_PAGES`` * ``_PAGE_LIMIT`` = 1000 tokens max par appel (garde-
    fou anti-boucle-infinie, indépendant de ce que Birdeye renvoie réellement)."""
    api_key = birdeye_api_key()
    if not api_key:
        return []

    headers = {"X-API-KEY": api_key, "x-chain": "base", "accept": "application/json"}
    url = f"{BASE_URL}/defi/v3/token/list"
    contracts: list[str] = []

    async with httpx.AsyncClient(timeout=20.0) as client:
        for page in range(_MAX_PAGES):
            await _throttle()
            params = {
                "sort_by": "liquidity", "sort_type": "desc",
                "min_liquidity": min_liquidity_usd, "min_volume_24h_usd": min_volume_24h_usd,
                "limit": _PAGE_LIMIT, "offset": page * _PAGE_LIMIT,
            }
            try:
                resp = await client.get(url, params=params, headers=headers)
            except Exception as exc:  # noqa: BLE001 -- panne réseau, jamais bloquant
                logger.info("birdeye: token/list échoué à la page %s (%s)", page, exc)
                break
            if resp.status_code != 200:
                logger.info(
                    "birdeye: token/list HTTP %s à la page %s -- %s",
                    resp.status_code, page, resp.text[:200],
                )
                break
            try:
                body = resp.json()
            except Exception:  # noqa: BLE001 -- réponse illisible, jamais une exception qui remonte
                break
            data = body.get("data") if isinstance(body, dict) else None
            items = (data or {}).get("tokens") if isinstance(data, dict) else None
            if not isinstance(items, list) or not items:
                break
            for item in items:
                if not isinstance(item, dict):
                    continue
                addr = item.get("address")
                if addr:
                    contracts.append(str(addr))
            if len(items) < _PAGE_LIMIT:
                break

    return contracts
