"""Fournisseurs RAPIDES de transferts de wallet (Alchemy + Moralis) -- 22/07,
décision opérateur explicite ("soulageons au maximum Blockscout") après avoir
trouvé que le wallet-scoring (`smart_money.py`) consomme 73,6% du budget de
crédits Blockscout Pro (endpoint `token-transfers`, 30 crédits/appel réel,
cf. docs/HANDOFF_BLOCKSCOUT.md) et que ce budget tombe régulièrement à sec,
forçant un repli vers l'endpoint gratuit Blockscout -- lent/instable sur les
wallets les plus actifs (34s puis erreur 500 constatés en conditions réelles
sur un vrai wallet, avant ce correctif).

Vérifié PAR DE VRAIS APPELS AUTHENTIFIÉS (22/07, jamais la seule doc) : Alchemy
`alchemy_getAssetTransfers` et Moralis `erc20/transfers` répondent tous deux en
moins de 4s sur EXACTEMENT le wallet qui avait fait planter l'endpoint gratuit
Covalent/GoldRush (candidat écarté séparément, cf. docs/HANDOFF_WALLET_SCORING.md)
-- confirmés fonctionnels sur Base, structure de réponse relue en direct, pas
supposée depuis un exemple externe.

Cascade : Alchemy (principal, 120 CU/appel, 30M CU/mois gratuit confirmé sur
la doc officielle) -> Moralis (second recours si Alchemy indisponible/en
échec, 50 CU/appel, 40 000 CU/jour gratuit confirmé par capture d'écran réelle
du dashboard opérateur) -> indisponible (``available=False``). Le repli final
vers Blockscout (comportement historique, jamais retiré) est géré par
L'APPELANT (`smart_money.py`), jamais ici -- ce module ne connaît pas
Blockscout, responsabilité strictement séparée.

Scopé chaîne "base" UNIQUEMENT -- seule chaîne vérifiée sur les deux
fournisseurs à ce jour (Ethereum et les autres chaînes de
`DEFAULT_SCAN_CHAINS()` continuent d'utiliser Blockscout sans y toucher).

Gate ``ARIA_WALLET_TRANSFERS_FAST_PROVIDER_ENABLED`` (OFF par défaut). Sans
clé (``ALCHEMY_API_KEY``/``MORALIS_API_KEY`` absentes) ou gate OFF,
``available=False`` immédiat -- l'appelant retombe alors sur Blockscout,
comportement strictement inchangé pour toute session qui n'active pas ce gate.

Doctrine dôme identique au reste du projet : 429 -- backoff exponentiel, 3
tentatives max ; timeout/5xx -- 1 retry après 5s puis dégradation explicite ;
aucune donnée manquante n'est jamais remplacée par une supposition."""
from __future__ import annotations

import asyncio
import logging
import os

import httpx

from aria_core.services.blockscout import TokenTransfer, TokenTransfersResult

logger = logging.getLogger(__name__)

UNAVAILABLE = "donnée indisponible"

ALCHEMY_BASE_URL = "https://base-mainnet.g.alchemy.com/v2"
MORALIS_BASE_URL = "https://deep-index.moralis.io/api/v2.2"

# Alchemy plafonne à 1000 résultats/appel (`pageKey` pour continuer) -- même
# plafond total que Blockscout aujourd'hui (2000 transferts/10 pages, cf.
# smart_money.py) pour ne jamais changer silencieusement le volume de données
# consommé en aval (FIFO/Sortino/wash-trading -- tous calibrés sur cette borne).
_ALCHEMY_PAGE_SIZE = "0x3e8"  # 1000 en hexadécimal (format natif de l'API)
_MAX_RETRIES_429 = 3
_TIMEOUT_RETRY_DELAY_S = 5.0


def wallet_transfers_fast_provider_enabled() -> bool:
    return os.environ.get("ARIA_WALLET_TRANSFERS_FAST_PROVIDER_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _alchemy_api_key() -> str:
    return os.environ.get("ALCHEMY_API_KEY", "").strip()


def _moralis_api_key() -> str:
    return os.environ.get("MORALIS_API_KEY", "").strip()


async def _post_with_dome(client: httpx.AsyncClient, url: str, *, json_body: dict) -> tuple[dict | None, str | None]:
    """POST générique avec la même politique d'erreurs que le reste du projet."""
    attempt_429 = 0
    timeout_retried = False
    while True:
        try:
            resp = await client.post(url, json=json_body, timeout=30)
        except httpx.TimeoutException:
            if timeout_retried:
                return None, f"{UNAVAILABLE} (timeout répété)"
            timeout_retried = True
            await asyncio.sleep(_TIMEOUT_RETRY_DELAY_S)
            continue
        except httpx.HTTPError as exc:
            return None, f"{UNAVAILABLE} ({exc})"

        if resp.status_code == 429:
            attempt_429 += 1
            if attempt_429 > _MAX_RETRIES_429:
                return None, f"{UNAVAILABLE} (429 persistant)"
            await asyncio.sleep(2.0 ** attempt_429)
            continue
        if resp.status_code >= 500:
            if timeout_retried:
                return None, f"{UNAVAILABLE} (HTTP {resp.status_code})"
            timeout_retried = True
            await asyncio.sleep(_TIMEOUT_RETRY_DELAY_S)
            continue
        if resp.status_code != 200:
            return None, f"{UNAVAILABLE} (HTTP {resp.status_code})"
        try:
            return resp.json(), None
        except ValueError:
            return None, f"{UNAVAILABLE} (réponse non-JSON)"


async def _get_with_dome(client: httpx.AsyncClient, url: str, *, params: dict) -> tuple[dict | None, str | None]:
    """GET générique avec la même politique d'erreurs que le reste du projet."""
    attempt_429 = 0
    timeout_retried = False
    while True:
        try:
            resp = await client.get(url, params=params, timeout=30)
        except httpx.TimeoutException:
            if timeout_retried:
                return None, f"{UNAVAILABLE} (timeout répété)"
            timeout_retried = True
            await asyncio.sleep(_TIMEOUT_RETRY_DELAY_S)
            continue
        except httpx.HTTPError as exc:
            return None, f"{UNAVAILABLE} ({exc})"

        if resp.status_code == 429:
            attempt_429 += 1
            if attempt_429 > _MAX_RETRIES_429:
                return None, f"{UNAVAILABLE} (429 persistant)"
            await asyncio.sleep(2.0 ** attempt_429)
            continue
        if resp.status_code >= 500:
            if timeout_retried:
                return None, f"{UNAVAILABLE} (HTTP {resp.status_code})"
            timeout_retried = True
            await asyncio.sleep(_TIMEOUT_RETRY_DELAY_S)
            continue
        if resp.status_code != 200:
            return None, f"{UNAVAILABLE} (HTTP {resp.status_code})"
        try:
            return resp.json(), None
        except ValueError:
            return None, f"{UNAVAILABLE} (réponse non-JSON)"


def _alchemy_transfer_to_token_transfer(item: dict) -> TokenTransfer | None:
    """Convertit UN transfert Alchemy (`alchemy_getAssetTransfers`) vers le
    type `TokenTransfer` commun -- même schéma que Blockscout, pour que
    smart_money.py ne voie AUCUNE différence en aval (FIFO/Sortino/wash-
    trading inchangés). Champs vérifiés par un vrai appel authentifié le
    22/07 (hash/from/to/rawContract.address/asset/value/metadata.
    blockTimestamp) -- pas une supposition depuis la doc."""
    tx_hash = item.get("hash")
    from_address = item.get("from")
    to_address = item.get("to")
    raw_contract = item.get("rawContract") or {}
    token_address = raw_contract.get("address")
    if not tx_hash or not from_address or not to_address:
        return None
    metadata = item.get("metadata") or {}
    value = item.get("value")
    return TokenTransfer(
        tx_hash=tx_hash,
        from_address=from_address,
        to_address=to_address,
        token_address=token_address,
        token_symbol=item.get("asset"),
        token_name=None,  # non fourni par cet endpoint Alchemy -- jamais inventé
        amount=float(value) if isinstance(value, (int, float)) else None,
        timestamp=metadata.get("blockTimestamp"),
    )


def _moralis_transfer_to_token_transfer(item: dict) -> TokenTransfer | None:
    """Convertit UN transfert Moralis (`erc20/transfers`) vers le type
    `TokenTransfer` commun. Champs vérifiés par un vrai appel authentifié le
    22/07 (transaction_hash/from_address/to_address/address/token_symbol/
    token_name/value_decimal/block_timestamp)."""
    tx_hash = item.get("transaction_hash")
    from_address = item.get("from_address")
    to_address = item.get("to_address")
    if not tx_hash or not from_address or not to_address:
        return None
    value_decimal = item.get("value_decimal")
    amount = None
    if value_decimal is not None:
        try:
            amount = float(value_decimal)
        except (TypeError, ValueError):
            amount = None
    return TokenTransfer(
        tx_hash=tx_hash,
        from_address=from_address,
        to_address=to_address,
        token_address=item.get("address"),
        token_symbol=item.get("token_symbol"),
        token_name=item.get("token_name"),
        amount=amount,
        timestamp=item.get("block_timestamp"),
    )


async def _alchemy_get_token_transfers(address: str, *, limit: int, max_pages: int) -> TokenTransfersResult:
    key = _alchemy_api_key()
    if not key:
        return TokenTransfersResult(available=False, error=f"{UNAVAILABLE} (ALCHEMY_API_KEY absente)")

    url = f"{ALCHEMY_BASE_URL}/{key}"
    transfers: list[TokenTransfer] = []
    page_key: str | None = None
    truncated = False

    async with httpx.AsyncClient() as client:
        for page in range(max_pages):
            params: dict = {
                "toAddress": address,
                "category": ["erc20"],
                "maxCount": _ALCHEMY_PAGE_SIZE,
                "withMetadata": True,
            }
            if page_key:
                params["pageKey"] = page_key
            data, error = await _post_with_dome(
                client, url,
                json_body={
                    "jsonrpc": "2.0", "id": 1, "method": "alchemy_getAssetTransfers",
                    "params": [params],
                },
            )
            if error is not None:
                if page == 0:
                    return TokenTransfersResult(available=False, error=error)
                truncated = True
                break
            result = (data or {}).get("result") or {}
            items = result.get("transfers") or []
            for item in items:
                if not isinstance(item, dict):
                    continue
                converted = _alchemy_transfer_to_token_transfer(item)
                if converted is not None:
                    transfers.append(converted)
                if len(transfers) >= limit:
                    break
            if len(transfers) >= limit:
                truncated = bool(result.get("pageKey"))
                break
            page_key = result.get("pageKey")
            if not page_key:
                break
            if page == max_pages - 1:
                truncated = True

    return TokenTransfersResult(transfers=transfers[:limit], available=True, error=None, truncated=truncated)


async def _moralis_get_token_transfers(address: str, *, limit: int, max_pages: int) -> TokenTransfersResult:
    key = _moralis_api_key()
    if not key:
        return TokenTransfersResult(available=False, error=f"{UNAVAILABLE} (MORALIS_API_KEY absente)")

    url = f"{MORALIS_BASE_URL}/{address}/erc20/transfers"
    transfers: list[TokenTransfer] = []
    cursor: str | None = None
    truncated = False

    async with httpx.AsyncClient(headers={"X-API-Key": key}) as client:
        for page in range(max_pages):
            params: dict = {"chain": "base", "limit": min(100, limit)}
            if cursor:
                params["cursor"] = cursor
            data, error = await _get_with_dome(client, url, params=params)
            if error is not None:
                if page == 0:
                    return TokenTransfersResult(available=False, error=error)
                truncated = True
                break
            items = (data or {}).get("result") or []
            for item in items:
                if not isinstance(item, dict):
                    continue
                converted = _moralis_transfer_to_token_transfer(item)
                if converted is not None:
                    transfers.append(converted)
                if len(transfers) >= limit:
                    break
            if len(transfers) >= limit:
                truncated = bool((data or {}).get("cursor"))
                break
            cursor = (data or {}).get("cursor")
            if not cursor:
                break
            if page == max_pages - 1:
                truncated = True

    return TokenTransfersResult(transfers=transfers[:limit], available=True, error=None, truncated=truncated)


async def get_fast_token_transfers(
    address: str, chain: str, *, limit: int = 2000, max_pages: int = 10,
) -> TokenTransfersResult:
    """Point d'entrée public -- Alchemy en principal, Moralis en second
    recours. Scopé "base" uniquement (cf. docstring du module) : toute autre
    chaîne renvoie ``available=False`` immédiatement, l'appelant retombe sur
    Blockscout comme avant ce chantier, jamais un comportement inventé pour
    une chaîne non vérifiée."""
    if chain != "base" or not wallet_transfers_fast_provider_enabled():
        return TokenTransfersResult(available=False, error=f"{UNAVAILABLE} (fournisseur rapide non applicable)")

    alchemy_result = await _alchemy_get_token_transfers(address, limit=limit, max_pages=max_pages)
    if alchemy_result.available:
        return alchemy_result

    logger.info("wallet_transfers_fast: Alchemy indisponible (%s) -- repli Moralis", alchemy_result.error)
    moralis_result = await _moralis_get_token_transfers(address, limit=limit, max_pages=max_pages)
    if moralis_result.available:
        return moralis_result

    logger.info("wallet_transfers_fast: Moralis indisponible (%s) -- repli Blockscout (appelant)", moralis_result.error)
    return TokenTransfersResult(available=False, error=moralis_result.error)
