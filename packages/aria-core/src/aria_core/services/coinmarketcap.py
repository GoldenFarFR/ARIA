"""Client CoinMarketCap DEX (lecture seule) -- 3e couche de pricing pour le
wallet-scoring (#157, 14/07), après GeckoTerminal et le diagnostic DexScreener.

Doctrine « dôme » (identique à blockscout.py/geckoterminal.py/dexscreener.py) :
- 429 : backoff exponentiel, 3 tentatives max, puis abandon sans bloquer le pipeline.
- Timeout / 5xx : 1 retry après 5s, puis dégradation explicite (``available=False``).
- Aucune donnée manquante n'est jamais remplacée par une supposition.

Clé API : ``COINMARKETCAP_API_KEY`` lue via ``os.environ.get`` À CHAQUE appel
(jamais mise en cache à l'import -- même patron que ``tavily.py``, plus simple
à tester avec ``monkeypatch.setenv``/``delenv``). Si présente : base URL sans
``/public-api`` + header ``X-CMC_PRO_API_KEY``, limites plus hautes. Si absente :
repli automatique sur le tier keyless public, aucun appel bloqué.

Réserve honnête (test live du 14/07, sans clé) : ``/v1/dex/token/pools`` et
``/v1/k-line/candles`` ont retourné HTTP 500 ("The system is busy...") sur 5
tentatives distinctes en keyless, jamais un succès -- ce tier semble ne PAS
débloquer réellement ces deux endpoints. Seul ``/v4/dex/pairs/quotes/latest``
a été confirmé fonctionnel en keyless (avec une adresse de pool/pair connue,
``network_slug`` -- pas ``network_id`` -- comme paramètre de chaîne, confirmé
en direct). En pratique, cette couche ne récupérera probablement des prix
qu'avec la vraie clé du VPS présente. Le schéma exact de réponse de
``/v1/k-line/candles`` n'a PAS pu être confirmé en direct (endpoint indisponible
pendant le test, doc officielle sans exemple de payload) -- le parsing ci-dessous
est une best-effort tolérante : toute forme inattendue dégrade en
``available=False``, jamais une exception, jamais une valeur devinée.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime

import httpx

from aria_core.skills.ta_levels import Candle

# 18/07 -- PoolMetadata/OHLCVResult étaient dupliquées à l'identique depuis
# geckoterminal.py (trouvé par audit VPS Secondaire), sauf PoolMetadata qui
# avait divergé : geckoterminal.py a reçu ``reserve_usd`` (15/07, défense
# anti-dust/scam-pool, #157) que cette copie n'a jamais reçue. Réutilisation
# directe au lieu d'une 2e copie à maintenir en synchro -- élimine la
# duplication ET la divergence en un seul geste, sans inventer de nouvelle
# logique (CMC ne peuple pas ``reserve_usd`` pour l'instant, il reste
# ``None`` -- comportement fail-open déjà documenté dans geckoterminal.py).
from aria_core.services.geckoterminal import OHLCVResult, PoolMetadata

logger = logging.getLogger(__name__)

UNAVAILABLE = "donnée CoinMarketCap indisponible"

BASE_URL_KEYLESS = "https://pro-api.coinmarketcap.com/public-api"
BASE_URL_KEYED = "https://pro-api.coinmarketcap.com"

# Même vocabulaire chaîne que blockscout.CHAIN_IDS / geckoterminal.GECKO_NETWORK_SLUGS
# (13 chaînes, #157 classement TVL dynamique, 14/07). "bnb" retiré -- Blockscout
# ne sert pas BNB Smart Chain (cf. blockscout.CHAIN_IDS), inutile de garder un
# slug CMC qu'aucune chaîne active n'atteindra jamais.
#
# Seule "base" a été vérifiée en direct ce soir : /v4/dex/pairs/quotes/latest
# a répondu avec succès en keyless (`network_slug=base`). Les 12 autres valeurs
# sont des SUPPOSITIONS raisonnables (mêmes noms que GeckoTerminal la plupart
# du temps, CMC n'a pas de registre "networks" public équivalent trouvé pour
# vérifier ligne à ligne) -- documentées comme NON vérifiées, jamais présentées
# comme confirmées. À corriger si un test en conditions réelles (avec la clé
# VPS) révèle une divergence, même doctrine que le reste de ce fichier.
CMC_NETWORK_SLUGS: dict[str, str] = {
    "base": "base",  # vérifié en direct
    "ethereum": "ethereum",  # non vérifié
    "arbitrum": "arbitrum",  # non vérifié
    "optimism": "optimism",  # non vérifié
    "polygon": "polygon",  # non vérifié -- GeckoTerminal dit "polygon_pos", supposition CMC différente (nom court usuel)
    "celo": "celo",  # non vérifié
    "gnosis": "gnosis",  # non vérifié -- GeckoTerminal dit "xdai", supposition CMC différente (nom usuel, pas de garantie)
    "scroll": "scroll",  # non vérifié
    "zksync": "zksync",  # non vérifié
    "rootstock": "rootstock",  # non vérifié
    "unichain": "unichain",  # non vérifié
    "soneium": "soneium",  # non vérifié
    "mode": "mode",  # non vérifié
}


def _api_key() -> str | None:
    return os.environ.get("COINMARKETCAP_API_KEY", "").strip() or None


async def _get_json(path: str, *, params: dict) -> tuple[object | None, str | None]:
    """GET avec retry sur 429/5xx/timeout -- même politique que
    blockscout.py/geckoterminal.py/dexscreener.py. Bascule automatiquement sur
    le tier keyé (base URL + header) si ``COINMARKETCAP_API_KEY`` est présente,
    sinon tier keyless -- jamais bloquant si la clé est absente."""
    api_key = _api_key()
    base_url = BASE_URL_KEYED if api_key else BASE_URL_KEYLESS
    headers = {"Accept": "application/json"}
    if api_key:
        headers["X-CMC_PRO_API_KEY"] = api_key
    url = f"{base_url}{path}"

    attempt_429 = 0
    timeout_retried = False

    while True:
        try:
            async with httpx.AsyncClient(timeout=18.0) as client:
                response = await client.get(url, params=params, headers=headers)
        except httpx.TransportError as exc:
            if not timeout_retried:
                timeout_retried = True
                await asyncio.sleep(5.0)
                continue
            logger.warning("coinmarketcap: timeout sur %s -> %s", url, exc)
            return None, f"{UNAVAILABLE} (timeout CoinMarketCap)"

        if response.status_code == 429:
            attempt_429 += 1
            if attempt_429 >= 3:
                logger.warning("coinmarketcap: HTTP 429 sur %s apres %s tentatives", url, attempt_429)
                return None, f"{UNAVAILABLE} (rate limit CoinMarketCap)"
            await asyncio.sleep(0.5 * (2**attempt_429))
            continue

        if response.status_code >= 500:
            if not timeout_retried:
                timeout_retried = True
                await asyncio.sleep(5.0)
                continue
            logger.warning("coinmarketcap: HTTP %s sur %s", response.status_code, url)
            return None, f"{UNAVAILABLE} (erreur serveur CoinMarketCap {response.status_code})"

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.warning("coinmarketcap: %s", exc)
            return None, f"{UNAVAILABLE} ({exc})"

        payload = response.json()
        if not isinstance(payload, dict):
            return None, f"{UNAVAILABLE} (réponse inattendue)"

        # Enveloppe CMC : un HTTP 200 peut quand même porter un échec logique
        # (`status.error_code` != "0") -- jamais interprété comme un succès
        # juste parce que le code HTTP est 200.
        status = payload.get("status")
        if isinstance(status, dict):
            error_code = str(status.get("error_code", "0"))
            if error_code not in ("0", ""):
                error_message = status.get("error_message") or error_code
                logger.warning("coinmarketcap: error_code=%s sur %s -> %s", error_code, url, error_message)
                return None, f"{UNAVAILABLE} ({error_message})"

        return payload, None


async def resolve_primary_pool(token_address: str, *, network_slug: str = "base") -> PoolMetadata:
    """Résout le pool à la plus forte liquidité pour ``token_address`` via
    ``/v1/dex/token/pools`` -- même logique de sélection que
    ``geckoterminal.resolve_primary_pool`` (comparaison défensive, liquidité
    malformée traitée comme 0, jamais un crash). Réserve honnête : cet endpoint
    a retourné HTTP 500 sur toutes les tentatives keyless en direct ce soir --
    ``available=False`` est donc l'issue attendue sans clé API valide."""
    data, error = await _get_json(
        "/v1/dex/token/pools", params={"network_slug": network_slug, "contract_address": token_address}
    )
    if error is not None:
        return PoolMetadata(pool_address=token_address, available=False, error=error)

    pools = data.get("data")
    if not isinstance(pools, list) or not pools:
        return PoolMetadata(pool_address=token_address, available=False, error="aucun pool trouvé pour ce token")

    best_entry: dict | None = None
    best_liquidity = -1.0
    for item in pools:
        if not isinstance(item, dict):
            continue
        try:
            liquidity = float(item.get("liquidity") or item.get("reserve_usd") or 0.0)
        except (TypeError, ValueError):
            liquidity = 0.0
        if liquidity > best_liquidity:
            best_liquidity = liquidity
            best_entry = item

    pool_address = None
    if best_entry:
        pool_address = best_entry.get("pool_address") or best_entry.get("contract_address") or best_entry.get("address")
    if not best_entry or not pool_address:
        return PoolMetadata(pool_address=token_address, available=False, error="aucun pool exploitable pour ce token")

    created_at = None
    raw_created = best_entry.get("pool_created_at") or best_entry.get("created_at")
    if raw_created:
        try:
            created_at = datetime.fromisoformat(str(raw_created).replace("Z", "+00:00"))
        except ValueError:
            created_at = None

    return PoolMetadata(pool_address=str(pool_address), created_at=created_at, available=True, error=None)


async def get_ohlcv(pool_address: str, *, network_slug: str = "base") -> OHLCVResult:
    """Bougies OHLCV pour ``pool_address`` via ``/v1/k-line/candles``. Parsing
    tolérant (schéma non confirmé en direct, cf. docstring du module) : accepte
    plusieurs noms de champs plausibles, toute forme inattendue -> `available=False`,
    jamais une bougie inventée."""
    data, error = await _get_json(
        "/v1/k-line/candles", params={"network_slug": network_slug, "contract_address": pool_address, "time_period": "hourly"}
    )
    if error is not None:
        return OHLCVResult(candles=[], available=False, error=error)

    raw_candles = data.get("data")
    if isinstance(raw_candles, dict):
        raw_candles = raw_candles.get("quotes") or raw_candles.get("candles")
    if not isinstance(raw_candles, list) or not raw_candles:
        return OHLCVResult(candles=[], available=False, error=f"{UNAVAILABLE} (aucune bougie)")

    candles: list[Candle] = []
    for row in raw_candles:
        if not isinstance(row, dict):
            continue
        try:
            ts_raw = row.get("timestamp") or row.get("time_open") or row.get("ts")
            ts = int(ts_raw) if ts_raw is not None else None
            if ts is not None and ts > 10_000_000_000:  # millisecondes -> secondes
                ts //= 1000
            o = float(row.get("open"))
            h = float(row.get("high"))
            l = float(row.get("low"))
            c = float(row.get("close"))
            v = float(row.get("volume") or 0.0)
        except (TypeError, ValueError):
            continue
        if ts is None:
            continue
        candles.append(Candle(ts=ts, open=o, high=h, low=l, close=c, volume=v))

    if not candles:
        return OHLCVResult(candles=[], available=False, error=f"{UNAVAILABLE} (bougies illisibles)")

    candles.sort(key=lambda c: c.ts)
    return OHLCVResult(candles=candles, available=True, error=None)
