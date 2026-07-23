"""Client CabalSpy -- wallets KOL/Smart Money/Whale labellisés multi-chain
(23/07, décision opérateur explicite : source de candidats pour le
wallet-scoring, en complément de `wallet_candidate_sourcing.py`).

Changement de politique ASSUMÉ : `wallet_candidate_sourcing.py` avait une
doctrine "zéro nouvelle dépendance externe" (Nansen/Zerion déjà écartés pour
cette raison). CabalSpy est retenu ici sur décision opérateur explicite après
vérification réelle -- palier Free confirmé (0$/mois, 10 000 crédits, 5 req/s,
sans CB, `cabalspy.xyz/pricing`, capture opérateur 23/07).

Vérifié en conditions réelles (23/07, curl direct par l'opérateur, clé jamais
manipulée par cette session) :
- `GET /v1/wallets?blockchain=base&type=kol` -- 200 wallets Base avec identité
  COMPLÈTE (name/twitter/telegram/image_url/copytrade_link) -- la vraie valeur
  ajoutée (pont wallet <-> identité que ni Moni ni Zerion ne fournissent).
- `GET /v1/wallets?blockchain=base&type=smart` -- 38 wallets ANONYMES (tous les
  champs identité vides) -- recoupe probablement ce que `smart_money.py`
  détecte déjà par comportement, gratuitement. Peu de valeur ajoutée pour ce
  type précis, câblé ici quand même (au cas où un futur usage en ait besoin)
  mais jamais recommandé comme source prioritaire.
- `GET /v1/wallets/lookup?address=...` -- cherche une adresse sur TOUTES les
  chaînes en un appel, `found:false` si absente de leur base restreinte
  (~quelques centaines de KOL connus, PAS une base exhaustive).

Débit : AUCUNE limite officielle documentée trouvée au-delà du palier
souscrit (5 req/s Free) -- throttle prudent par défaut (1 req/s, 90% de marge
large), backoff réactif standard sur 429/5xx. Authentification : `api_key` en
query param (confirmé réel) -- l'en-tête `Authorization: Bearer` alternatif
mentionné dans la doc n'a pas été testé, non utilisé ici."""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.cabalspy.xyz/v1"
_TIMEOUT_SECONDS = 10.0
_MIN_INTERVAL_SECONDS = 1.0
_MAX_PAGES = 20  # garde-fou anti-boucle infinie, largement au-dessus de 200/limit=100

_last_call_at = 0.0
_throttle_lock = asyncio.Lock()

VALID_BLOCKCHAINS = ("base", "bnb", "solana", "eth")
VALID_TYPES = ("kol", "smart", "whale")


@dataclass
class CabalSpyWallet:
    wallet_address: str
    blockchain: str
    type: str
    name: str = ""
    twitter: str = ""
    telegram: str = ""
    copytrade_link: str = ""


@dataclass
class CabalSpyLookupResult:
    found: bool
    wallet_address: str
    blockchain: str | None = None
    type: str | None = None
    name: str = ""
    twitter: str = ""
    telegram: str = ""


def is_cabalspy_configured() -> bool:
    return bool(os.environ.get("CABALSPY_API_KEY", "").strip())


async def _throttle() -> None:
    global _last_call_at
    async with _throttle_lock:
        now = asyncio.get_event_loop().time()
        wait = _MIN_INTERVAL_SECONDS - (now - _last_call_at)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_call_at = asyncio.get_event_loop().time()


async def _get(params: dict, *, path: str = "/wallets") -> dict | None:
    api_key = os.environ.get("CABALSPY_API_KEY", "").strip()
    if not api_key:
        return None

    await _throttle()
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            r = await client.get(f"{_BASE_URL}{path}", params={**params, "api_key": api_key})
    except httpx.TransportError as exc:
        logger.info("cabalspy: panne réseau (%s)", exc)
        return None

    if r.status_code != 200:
        logger.info("cabalspy: HTTP %s pour %s", r.status_code, path)
        return None

    try:
        payload = r.json()
    except Exception:  # noqa: BLE001 -- corps illisible, jamais une exception qui remonte
        return None

    if not isinstance(payload, dict) or not payload.get("success"):
        return None
    return payload


def _parse_wallet(item: dict, *, blockchain: str, wallet_type: str) -> CabalSpyWallet | None:
    address = item.get("wallet_address")
    if not address or not isinstance(address, str):
        return None
    return CabalSpyWallet(
        wallet_address=address,
        blockchain=blockchain,
        type=wallet_type,
        name=str(item.get("name") or ""),
        twitter=str(item.get("twitter") or ""),
        telegram=str(item.get("telegram") or ""),
        copytrade_link=str(item.get("copytrade_link") or ""),
    )


async def list_wallets(
    blockchain: str, *, wallet_type: str = "kol", page_limit: int = 100,
) -> list[CabalSpyWallet] | None:
    """Liste PAGINÉE complète des wallets labellisés pour une chaîne/type
    donnés. ``None`` si la clé est absente ou toute panne sur le PREMIER appel
    (jamais une exception qui remonte) ; une panne sur une page ULTÉRIEURE
    renvoie ce qui a déjà été collecté (dégradation partielle honnête, jamais
    tout perdre pour une panne tardive)."""
    if blockchain not in VALID_BLOCKCHAINS or wallet_type not in VALID_TYPES:
        return None

    wallets: list[CabalSpyWallet] = []
    cursor: str | None = None
    for page in range(_MAX_PAGES):
        params = {"blockchain": blockchain, "type": wallet_type, "limit": max(1, min(int(page_limit), 100))}
        if cursor:
            params["cursor"] = cursor

        payload = await _get(params)
        if payload is None:
            return wallets or None if page > 0 else None

        data = payload.get("data")
        if not isinstance(data, dict):
            break
        raw_wallets = data.get("wallets")
        if not isinstance(raw_wallets, list):
            break

        for item in raw_wallets:
            if isinstance(item, dict):
                parsed = _parse_wallet(item, blockchain=blockchain, wallet_type=wallet_type)
                if parsed:
                    wallets.append(parsed)

        pagination = data.get("pagination") or {}
        if not pagination.get("has_more"):
            break
        cursor = pagination.get("next_cursor")
        if not cursor:
            break

    return wallets


async def lookup_wallet(address: str) -> CabalSpyLookupResult | None:
    """Recherche une adresse sur TOUTES les chaînes en un appel. ``None`` si
    la clé est absente ou toute panne réseau. Renvoie un résultat avec
    ``found=False`` si l'adresse n'est simplement pas dans leur base (jamais
    confondu avec une panne)."""
    addr = (address or "").strip()
    if not addr:
        return None

    payload = await _get({"address": addr}, path="/wallets/lookup")
    if payload is None:
        return None

    data = payload.get("data")
    if not isinstance(data, dict):
        return None

    found = bool(data.get("found"))
    if not found:
        return CabalSpyLookupResult(found=False, wallet_address=addr)

    return CabalSpyLookupResult(
        found=True,
        wallet_address=str(data.get("wallet_address") or addr),
        blockchain=data.get("blockchain"),
        type=data.get("type"),
        name=str(data.get("name") or ""),
        twitter=str(data.get("twitter") or ""),
        telegram=str(data.get("telegram") or ""),
    )
