"""Client Mobula (lecture seule) -- 3e étage de la cascade OHLCV momentum (#194),
inséré entre CoinMarketCap et la synthèse dégradée DexScreener (18/07, #212).

Contexte : diagnostiqué en direct ce soir sur le pipeline momentum -- GeckoTerminal
(HTTP 429) puis CoinMarketCap (HTTP 500) indisponibles simultanément, cascade
retombée sur la synthèse DexScreener (5 points de prix approximatifs, jamais un
vrai chandelier) -- ``detect_entry`` (golden pocket + divergence RSI) ne trouve
alors quasi jamais de setup valide sur des données aussi pauvres (``no_entry_signal``
systématique observé sur 4/4 candidats Base testés). Demande opérateur explicite
("il nous faut plus de marge d'appel on est trop restreint") a mené à la diligence
Mobula (docs.mobula.io, vérifié en direct, pas supposé) : couverture Base+Solana
confirmée, y compris sur un token `is_listed:false` (CoinGecko répond 404 sur la
même adresse -- comparaison faite en direct), endpoint OHLCV réel (v2, pas une
synthèse) confirmé fonctionnel avec le vrai schéma de réponse.

Doctrine « dôme » (identique à geckoterminal.py/coinmarketcap.py) :
- 429 : backoff exponentiel, 3 tentatives max, puis abandon sans bloquer le pipeline.
- Timeout / 5xx : 1 retry après 5s, puis dégradation explicite (``available=False``).
- Aucune donnée manquante n'est jamais remplacée par une supposition.

Clé API : ``MOBULA_API_KEY`` -- REQUISE dès le premier appel (vérifié en direct :
même le tier Free renvoie 429 "You need to create an API key" sans elle, contrairement
à GeckoTerminal/DexScreener/GoPlus qui ont un chemin public). Client neutralisé
(``available=False`` immédiat, aucun appel réseau) si la clé est absente -- jamais
un blocage du pipeline.

Paramètre ``blockchain`` de Mobula = même vocabulaire que les chaînes DexScreener
d'ARIA ("base", "solana" -- les deux vérifiés en direct, transmis tel quel, AUCUNE
table de traduction nécessaire contrairement à GoPlus/CoinMarketCap)."""
from __future__ import annotations

import asyncio
import logging
import os

import httpx

from aria_core.services.geckoterminal import OHLCVResult
from aria_core.skills.ta_levels import Candle

logger = logging.getLogger(__name__)

UNAVAILABLE = "donnée Mobula indisponible"

BASE_URL = "https://api.mobula.io/api"

_MIN_INTERVAL = 1.05  # tier Free = 1 req/s documenté -- marge légère sous le plafond exact
_last_call_at = 0.0
_throttle_lock = asyncio.Lock()


def mobula_configured() -> bool:
    """True si ``MOBULA_API_KEY`` est présente -- aucun chemin anonyme chez Mobula,
    contrairement au reste du dôme ARIA (#212, vérifié en direct : 429 systématique
    sans clé, même sur le tier Free)."""
    return bool(os.environ.get("MOBULA_API_KEY", "").strip())


async def _throttle() -> None:
    global _last_call_at
    async with _throttle_lock:
        elapsed = asyncio.get_event_loop().time() - _last_call_at
        if elapsed < _MIN_INTERVAL:
            await asyncio.sleep(_MIN_INTERVAL - elapsed)
        _last_call_at = asyncio.get_event_loop().time()


async def _get_json(path: str, *, params: dict) -> tuple[object | None, str | None]:
    """GET avec retry sur 429/5xx/timeout -- même politique que le reste du dôme."""
    api_key = os.environ.get("MOBULA_API_KEY", "").strip()
    if not api_key:
        return None, f"{UNAVAILABLE} (MOBULA_API_KEY absente)"

    url = f"{BASE_URL}{path}"
    headers = {"Authorization": api_key}
    attempt_429 = 0
    timeout_retried = False

    while True:
        await _throttle()
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(url, params=params, headers=headers)
        except httpx.TransportError as exc:
            if not timeout_retried:
                timeout_retried = True
                await asyncio.sleep(5.0)
                continue
            logger.warning("mobula: timeout sur %s -> %s", url, exc)
            return None, f"{UNAVAILABLE} (timeout, {exc})"

        if response.status_code == 429:
            attempt_429 += 1
            if attempt_429 >= 3:
                logger.warning("mobula: HTTP 429 sur %s apres %s tentatives", url, attempt_429)
                return None, f"{UNAVAILABLE} (rate limit)"
            await asyncio.sleep(0.5 * (2**attempt_429))
            continue

        if response.status_code >= 500:
            if not timeout_retried:
                timeout_retried = True
                await asyncio.sleep(5.0)
                continue
            logger.warning("mobula: HTTP %s sur %s", response.status_code, url)
            return None, f"{UNAVAILABLE} (erreur serveur {response.status_code})"

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.warning("mobula: %s", exc)
            return None, f"{UNAVAILABLE} ({exc})"

        return response.json(), None


async def get_ohlcv(contract: str, *, blockchain: str = "base", period: str = "1d", amount: int = 60) -> OHLCVResult:
    """Bougies OHLCV réelles (pas une synthèse) pour ``contract`` sur ``blockchain``
    -- ``/api/2/token/ohlcv-history`` (schéma vérifié en direct, 18/07 : ``{t,o,h,l,c,v}``,
    ``t`` en millisecondes). ``amount`` par défaut à 60 (cohérent avec les autres
    étages de la cascade, jamais les 2000 max documentés -- inutile pour un scan
    d'entrée momentum)."""
    data, error = await _get_json(
        "/2/token/ohlcv-history",
        params={"address": contract, "blockchain": blockchain, "period": period, "amount": amount},
    )
    if error is not None:
        return OHLCVResult(candles=[], available=False, error=error)

    raw_candles = data.get("data") if isinstance(data, dict) else None
    if not isinstance(raw_candles, list) or not raw_candles:
        return OHLCVResult(candles=[], available=False, error=f"{UNAVAILABLE} (aucune bougie)")

    candles: list[Candle] = []
    for row in raw_candles:
        if not isinstance(row, dict):
            continue
        try:
            ts_raw = row.get("t")
            ts = int(ts_raw) if ts_raw is not None else None
            if ts is not None and ts > 10_000_000_000:  # millisecondes -> secondes
                ts //= 1000
            o = float(row.get("o"))
            h = float(row.get("h"))
            low = float(row.get("l"))
            c = float(row.get("c"))
            v = float(row.get("v") or 0.0)
        except (TypeError, ValueError):
            continue
        if ts is None:
            continue
        candles.append(Candle(ts=ts, open=o, high=h, low=low, close=c, volume=v))

    if not candles:
        return OHLCVResult(candles=[], available=False, error=f"{UNAVAILABLE} (bougies illisibles)")

    candles.sort(key=lambda c: c.ts)
    return OHLCVResult(candles=candles, available=True, error=None)
