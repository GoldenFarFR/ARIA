from __future__ import annotations

import asyncio

import httpx

from app.config import settings
from app.models.schemas import Candle, Timeframe
from app.services.candle_aggregator import resample_candles
from app.services.chain_mapping import to_gecko_network

# Timeframes récupérés directement depuis GeckoTerminal
DIRECT_FETCH: dict[Timeframe, tuple[str, int]] = {
    Timeframe.M1: ("minute", 1),
    Timeframe.M5: ("minute", 5),
    Timeframe.M15: ("minute", 15),
    Timeframe.H1: ("hour", 1),
    Timeframe.D1: ("day", 1),
}

# Timeframes reconstruits par agrégation
RESAMPLED_FROM: dict[Timeframe, Timeframe] = {
    Timeframe.M30: Timeframe.M15,
    Timeframe.H4: Timeframe.H1,
}


class GeckoTerminalClient:
    def __init__(self) -> None:
        self.base_url = settings.geckoterminal_base_url
        self._lock = asyncio.Lock()
        self._last_request = 0.0

    async def _throttle(self) -> None:
        async with self._lock:
            now = asyncio.get_event_loop().time()
            wait = 2.1 - (now - self._last_request)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request = asyncio.get_event_loop().time()

    async def _fetch_raw(
        self,
        network: str,
        pair_address: str,
        period: str,
        aggregate: int,
        limit: int,
    ) -> list[Candle]:
        await self._throttle()
        url = f"{self.base_url}/networks/{network}/pools/{pair_address}/ohlcv/{period}"
        params = {"aggregate": aggregate, "limit": limit}

        async with httpx.AsyncClient(timeout=25.0) as client:
            response = await client.get(
                url,
                params=params,
                headers={"Accept": "application/json"},
            )
            if response.status_code in (400, 404, 429):
                return []
            response.raise_for_status()
            payload = response.json()

        rows = payload.get("data", {}).get("attributes", {}).get("ohlcv_list", [])
        candles = [
            Candle(
                timestamp=int(row[0]),
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                volume=float(row[5]),
            )
            for row in rows
        ]
        candles.sort(key=lambda c: c.timestamp)
        return candles

    async def get_ohlcv(
        self,
        chain_id: str,
        pair_address: str,
        timeframe: Timeframe,
        limit: int | None = None,
    ) -> list[Candle]:
        network = to_gecko_network(chain_id)
        if not network:
            return []

        candle_limit = limit or settings.max_candles

        if timeframe in RESAMPLED_FROM:
            source = RESAMPLED_FROM[timeframe]
            source_candles = await self.get_ohlcv(chain_id, pair_address, source, candle_limit * 2)
            return resample_candles(source_candles, timeframe)[-candle_limit:]

        if timeframe not in DIRECT_FETCH:
            return []

        period, aggregate = DIRECT_FETCH[timeframe]
        return await self._fetch_raw(network, pair_address, period, aggregate, candle_limit)


geckoterminal_client = GeckoTerminalClient()