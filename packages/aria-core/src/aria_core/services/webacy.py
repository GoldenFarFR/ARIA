"""Client Webacy (lecture seule) -- 2e avis de sécurité contrat, repli/complément à
GoPlus (#194, 21/07).

Contexte : GoPlus reste le SEUL garde-fou honeypot dur du pipeline momentum, mais son
palier gratuit est très serré (10 tokens/min réels, 10 000/mois -- calibré le 21/07
après découverte de la vraie structure de facturation par token). Webacy vérifié en
direct ce jour-là (docs.webacy.com/api-reference) : Contract Risk API, Base en
"Full Support", palier Demo gratuit 2 req/s (rafale 5), 2 000 requêtes/mois -- profil
COMPLÉMENTAIRE à GoPlus (débit par seconde bien plus généreux, plafond mensuel plus
bas), pas un remplacement.

Doctrine « dôme » (identique à goplus.py/mobula.py) :
- 429 : backoff exponentiel, 3 tentatives max, puis abandon sans bloquer le pipeline.
- Timeout / 5xx : 1 retry après 5s, puis dégradation explicite (``available=False``).
- Aucune donnée manquante n'est jamais remplacée par une supposition.

Clé API : ``WEBACY_API_KEY`` -- REQUISE dès le premier appel (palier Demo, pas de
chemin public documenté). Client neutralisé (``available=False`` immédiat, aucun
appel réseau) si la clé est absente -- jamais un blocage du pipeline. Header
``x-api-key`` confirmé via docs.webacy.com/api-reference/contract-risk (vérifié en
direct le 21/07 -- jamais deviné, leçon du bug de header GoPlus du même jour).

**PAS ENCORE branché dans ``momentum_entry.py``** -- ce module est un client
autonome, testé isolément. Le brancher comme repli/complément au garde honeypot
GoPlus (``_check_honeypot``) reste une décision séparée, pas prise ici.

**Schéma de réponse NON VÉRIFIÉ EN CONDITIONS RÉELLES** -- aucune clé API disponible
au moment de l'écriture (21/07). Basé sur la doc officielle uniquement
(``score``/``tags``/``categories``, ex. ``contract_possible_drainer``). À
reconfirmer avec un vrai appel dès qu'une clé Demo est configurée -- ne jamais
déployer ce module sans ce test, même discipline que le reste du projet."""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)

UNAVAILABLE = "donnée Webacy indisponible"

BASE_URL = "https://api.webacy.com"

# Vocabulaire chaîne Webacy (docs.webacy.com/essentials/supported-blockchains) --
# PAS le même vocabulaire que GoPlus (ids numériques) ni GeckoTerminal (slugs
# spécifiques) -- table de traduction dédiée, comme pour coinmarketcap.py.
WEBACY_CHAIN_SLUGS: dict[str, str] = {
    "base": "base",
    "ethereum": "eth",
    "polygon": "pol",
    "optimism": "opt",
    "arbitrum": "arb",
    "bsc": "bsc",
    "solana": "sol",
}

# 21/07 -- calibré à 90% du palier Demo confirmé (2 req/s, docs.webacy.com/
# essentials/rate-limits), doctrine CLAUDE.md "Débit calibré à 90%" :
# 1.8 req/s = 0.556s. Plafond mensuel (2 000 requêtes) NON géré ici (pas de
# compteur persistant) -- à ajouter si ce client est un jour branché en prod,
# même famille de garde-fou que celui proposé (pas encore construit) pour
# GoPlus.
_MIN_INTERVAL = 0.556
_last_call_at = 0.0
_throttle_lock = asyncio.Lock()


def webacy_api_key() -> str | None:
    return os.environ.get("WEBACY_API_KEY", "").strip() or None


def webacy_configured() -> bool:
    return bool(webacy_api_key())


async def _throttle() -> None:
    global _last_call_at
    async with _throttle_lock:
        now = asyncio.get_event_loop().time()
        wait = _MIN_INTERVAL - (now - _last_call_at)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_call_at = asyncio.get_event_loop().time()


@dataclass
class ContractRiskResult:
    contract: str
    score: float | None = None
    tags: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    is_drainer: bool | None = None
    available: bool = True
    error: str | None = None


async def _get_json(path: str, *, params: dict | None = None) -> tuple[object | None, str | None]:
    """GET avec la politique d'erreurs du dôme -- même patron que goplus.py."""
    api_key = webacy_api_key()
    if not api_key:
        return None, f"{UNAVAILABLE} (WEBACY_API_KEY absente)"

    url = f"{BASE_URL}{path}"
    headers = {"x-api-key": api_key, "Accept": "application/json"}
    attempt_429 = 0
    retried = False

    while True:
        await _throttle()
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(url, params=params, headers=headers)
        except httpx.TransportError as exc:
            if not retried:
                retried = True
                await asyncio.sleep(5.0)
                continue
            logger.warning("webacy: timeout sur %s -> %s", url, exc)
            return None, f"{UNAVAILABLE} (timeout Webacy)"

        if response.status_code == 429:
            attempt_429 += 1
            if attempt_429 >= 3:
                logger.warning("webacy: HTTP 429 sur %s apres %s tentatives", url, attempt_429)
                return None, f"{UNAVAILABLE} (rate limit Webacy)"
            await asyncio.sleep(0.5 * (2**attempt_429))
            continue

        if response.status_code >= 500:
            if not retried:
                retried = True
                await asyncio.sleep(5.0)
                continue
            logger.warning("webacy: HTTP %s sur %s", response.status_code, url)
            return None, f"{UNAVAILABLE} (erreur serveur Webacy)"

        if response.status_code in (400, 401, 404):
            return None, f"{UNAVAILABLE} (HTTP {response.status_code})"
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.warning("webacy: %s", exc)
            return None, f"{UNAVAILABLE} ({exc})"

        return response.json(), None


async def get_contract_risk(contract: str, *, chain: str = "base") -> ContractRiskResult:
    """Analyse de risque contrat Webacy -- ``GET /api/v1/risk-score/contract/{address}``.
    Chemin CORRIGÉ le 21/07 : la 1ère version de ce module (basée sur une page doc
    ambiguë, docs.webacy.com/api-reference/contract-risk) utilisait ``/contracts/
    {contractAddress}`` -- faux, confirmé en confrontant l'OpenAPI officiel
    (docs.webacy.com/openapi.json, source la plus autoritaire) ET le guide Quickstart
    réel (qui montre un 3e chemin différent, ``/addresses/{address}``, pour un usage
    générique distinct -- pas celui-ci). Schéma de réponse (``score``/``tags``/
    ``categories``) confirmé correct dans l'OpenAPI. ``chain`` (vocabulaire ARIA, ex.
    "base") traduit via ``WEBACY_CHAIN_SLUGS`` -- chaîne non couverte ->
    ``available=False`` explicite, jamais une URL devinée."""
    webacy_chain = WEBACY_CHAIN_SLUGS.get(chain)
    if not webacy_chain:
        return ContractRiskResult(
            contract=contract, available=False,
            error=f"chaîne {chain} non couverte par Webacy",
        )

    data, error = await _get_json(f"/api/v1/risk-score/contract/{contract}", params={"chain": webacy_chain})
    if error is not None:
        return ContractRiskResult(contract=contract, available=False, error=error)
    if not isinstance(data, dict):
        return ContractRiskResult(contract=contract, available=False, error=UNAVAILABLE)

    categories = list((data.get("categories") or {}).keys()) if isinstance(data.get("categories"), dict) else list(data.get("categories") or [])
    return ContractRiskResult(
        contract=contract,
        score=data.get("score"),
        tags=list(data.get("tags") or []),
        categories=categories,
        is_drainer="contract_possible_drainer" in categories,
        available=True,
        error=None,
    )


webacy_client_configured = webacy_configured  # alias explicite pour les appelants
