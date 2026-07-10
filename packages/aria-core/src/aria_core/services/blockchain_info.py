"""Client de lecture seule Blockchain.com (charts API) — historique long BTC/USD.

Remplace CoinGecko pour l'historique BTC de plus de 365 jours : CoinGecko a changé
sa politique (confirmé en direct le 09/07, `error_code 10012`) et refuse désormais
toute requête sur son tier gratuit portant sur des données plus anciennes que 365
jours, quelle que soit la taille de la fenêtre — structurellement incompatible avec
`btc_cycles` (segmentation sur 3 cycles de halving, 10+ ans). Blockchain.com est une
société établie depuis 2011, l'endpoint `charts/market-price` est public, documenté,
sans clé, et couvre 2009 à aujourd'hui (~1600 points quotidiens, échantillonnage
natif de l'API).

Aucune écriture, aucune clé API. Politique d'erreurs identique à `services/coingecko.py` :
- Timeout / endpoint indisponible : 1 retry après 5s, puis fallback explicite.
- Aucune donnée manquante n'est jamais remplacée par une supposition — le champ
  `error` (et `available=False`) porte l'absence de donnée.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://api.blockchain.info"
UNAVAILABLE = "historique BTC indisponible (Blockchain.com)"


@dataclass
class BtcMarketPriceResult:
    available: bool
    prices: list[tuple[int, float]] = field(default_factory=list)  # (epoch_ms, prix_usd)
    error: str | None = None


class BlockchainInfoClient:
    """Client HTTP async, lecture seule, throttle prudent (API publique sans clé)."""

    def __init__(self, base_url: str = BASE_URL, *, min_interval: float = 2.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._min_interval = min_interval
        self._lock = asyncio.Lock()
        self._last_request = 0.0
        self._consecutive_failures = 0

    async def _throttle(self) -> None:
        async with self._lock:
            now = asyncio.get_event_loop().time()
            wait = self._min_interval - (now - self._last_request)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request = asyncio.get_event_loop().time()

    def _record_failure(self, detail: str) -> None:
        self._consecutive_failures += 1
        logger.info("blockchain_info: echec appel -- %s", detail)

    def _record_success(self) -> None:
        self._consecutive_failures = 0

    async def fetch_btc_market_price_history(self, *, timespan: str = "all") -> BtcMarketPriceResult:
        """Série de prix BTC/USD réelle (`charts/market-price`), échantillonnage natif
        de l'API (~1600 points sur `timespan=all`, de 2009 à aujourd'hui). Jamais de
        prix inventé : absence -> `available=False`."""
        url = f"{self.base_url}/charts/market-price?timespan={timespan}&format=json"
        await self._throttle()
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.get(url)
        except httpx.TransportError as exc:
            await asyncio.sleep(5.0)
            await self._throttle()
            try:
                async with httpx.AsyncClient(timeout=20.0) as client:
                    response = await client.get(url)
            except httpx.TransportError as exc2:
                self._record_failure(f"{url} -> {exc2}")
                return BtcMarketPriceResult(available=False, error=f"{UNAVAILABLE} (timeout)")
        except Exception as exc:  # noqa: BLE001 -- une panne reseau ne doit jamais remonter
            self._record_failure(f"{url} -> {exc}")
            return BtcMarketPriceResult(available=False, error=UNAVAILABLE)

        if response.status_code >= 400:
            self._record_failure(f"{url} -> HTTP {response.status_code}")
            return BtcMarketPriceResult(available=False, error=f"{UNAVAILABLE} (HTTP {response.status_code})")

        try:
            data = response.json()
            values = data.get("values")
        except Exception:  # noqa: BLE001
            self._record_failure(f"{url} -> reponse illisible")
            return BtcMarketPriceResult(available=False, error=UNAVAILABLE)

        if not isinstance(values, list) or not values:
            self._record_failure(f"{url} -> pas de valeurs")
            return BtcMarketPriceResult(available=False, error=UNAVAILABLE)

        prices: list[tuple[int, float]] = []
        for point in values:
            try:
                epoch_s = int(point["x"])
                price = float(point["y"])
            except (KeyError, TypeError, ValueError):
                continue
            prices.append((epoch_s * 1000, price))

        if not prices:
            self._record_failure(f"{url} -> aucune valeur exploitable")
            return BtcMarketPriceResult(available=False, error=UNAVAILABLE)

        self._record_success()
        return BtcMarketPriceResult(available=True, prices=prices)


blockchain_info_client = BlockchainInfoClient()
