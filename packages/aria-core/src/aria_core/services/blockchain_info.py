"""Read-only Blockchain.com client (charts API) — long BTC/USD history.

Replaces CoinGecko for BTC history older than 365 days: CoinGecko changed its
policy (confirmed live on 07/09, `error_code 10012`) and now refuses any
request on its free tier for data older than 365 days, regardless of the
window size — structurally incompatible with `btc_cycles` (segmented over 3
halving cycles, 10+ years). Blockchain.com is a company established since
2011, the `charts/market-price` endpoint is public, documented, keyless, and
covers 2009 to today (~1600 daily points, native API sampling).

No writes, no API key. Same error policy as `services/coingecko.py`:
- Timeout / endpoint unavailable: 1 retry after 5s, then explicit fallback.
- No missing data is ever replaced by a guess — the `error` field (and
  `available=False`) carries the absence of data.
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
    prices: list[tuple[int, float]] = field(default_factory=list)  # (epoch_ms, usd_price)
    error: str | None = None


class BlockchainInfoClient:
    """Async HTTP client, read-only, cautious throttle (keyless public API)."""

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
        logger.info("blockchain_info: call failed -- %s", detail)

    def _record_success(self) -> None:
        self._consecutive_failures = 0

    async def fetch_btc_market_price_history(self, *, timespan: str = "all") -> BtcMarketPriceResult:
        """Real BTC/USD price series (`charts/market-price`), native API
        sampling (~1600 points on `timespan=all`, from 2009 to today). Never
        an invented price: absence -> `available=False`."""
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
        except Exception as exc:  # noqa: BLE001 -- a network failure must never bubble up
            self._record_failure(f"{url} -> {exc}")
            return BtcMarketPriceResult(available=False, error=UNAVAILABLE)

        if response.status_code >= 400:
            self._record_failure(f"{url} -> HTTP {response.status_code}")
            return BtcMarketPriceResult(available=False, error=f"{UNAVAILABLE} (HTTP {response.status_code})")

        try:
            data = response.json()
            values = data.get("values")
        except Exception:  # noqa: BLE001
            self._record_failure(f"{url} -> unreadable response")
            return BtcMarketPriceResult(available=False, error=UNAVAILABLE)

        if not isinstance(values, list) or not values:
            self._record_failure(f"{url} -> no values")
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
            self._record_failure(f"{url} -> no usable value")
            return BtcMarketPriceResult(available=False, error=UNAVAILABLE)

        self._record_success()
        return BtcMarketPriceResult(available=True, prices=prices)


blockchain_info_client = BlockchainInfoClient()
