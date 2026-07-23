"""Mobula client (read-only) -- 3rd tier of the momentum OHLCV cascade (#194),
inserted between CoinMarketCap and the degraded DexScreener synthesis (18/07, #212).

Context: diagnosed live tonight on the momentum pipeline -- GeckoTerminal
(HTTP 429) then CoinMarketCap (HTTP 500) unavailable at the same time,
cascade fell back to DexScreener synthesis (5 approximate price points,
never a real candlestick) -- ``detect_entry`` (golden pocket + RSI
divergence) then almost never finds a valid setup on such poor data
(systematic ``no_entry_signal`` observed on 4/4 tested Base candidates).
Explicit operator request ("we need more call headroom, we're too
constrained") led to the Mobula diligence (docs.mobula.io, verified live,
not assumed): Base+Solana coverage confirmed, including on a token with
`is_listed:false` (CoinGecko returns 404 on the same address -- comparison
done live), real OHLCV endpoint (v2, not a synthesis) confirmed working
with the real response schema.

"Dome" doctrine (identical to geckoterminal.py/coinmarketcap.py):
- 429: exponential backoff, 3 attempts max, then give up without blocking the pipeline.
- Timeout / 5xx: 1 retry after 5s, then explicit degradation (``available=False``).
- Missing data is never replaced by a guess.

API key: ``MOBULA_API_KEY`` -- REQUIRED from the very first call (verified
live: even the Free tier returns 429 "You need to create an API key"
without it, unlike GeckoTerminal/DexScreener/GoPlus which have a public
path). Client neutralized (``available=False`` immediately, no network
call) if the key is absent -- never a pipeline blocker.

Mobula's ``blockchain`` parameter = same vocabulary as ARIA's DexScreener
chains ("base", "solana" -- both verified live, passed through as-is, NO
translation table needed unlike GoPlus/CoinMarketCap)."""
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

# 21/07 -- calibrated at 90% of the documented 1 req/s (docs.mobula.io/pricing),
# CLAUDE.md "Rate calibrated at 90%" doctrine: 0.9 req/s = 1.111s.
_MIN_INTERVAL = 1.111
_last_call_at = 0.0
_throttle_lock = asyncio.Lock()


def mobula_configured() -> bool:
    """True if ``MOBULA_API_KEY`` is present -- no anonymous path at Mobula,
    unlike the rest of ARIA's dome (#212, verified live: systematic 429
    without a key, even on the Free tier)."""
    return bool(os.environ.get("MOBULA_API_KEY", "").strip())


async def _throttle() -> None:
    global _last_call_at
    async with _throttle_lock:
        elapsed = asyncio.get_event_loop().time() - _last_call_at
        if elapsed < _MIN_INTERVAL:
            await asyncio.sleep(_MIN_INTERVAL - elapsed)
        _last_call_at = asyncio.get_event_loop().time()


async def _get_json(path: str, *, params: dict) -> tuple[object | None, str | None]:
    """GET with retry on 429/5xx/timeout -- same policy as the rest of the dome."""
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
            logger.warning("mobula: timeout on %s -> %s", url, exc)
            return None, f"{UNAVAILABLE} (timeout, {exc})"

        if response.status_code == 429:
            attempt_429 += 1
            if attempt_429 >= 3:
                logger.warning("mobula: HTTP 429 on %s after %s attempts", url, attempt_429)
                return None, f"{UNAVAILABLE} (rate limit)"
            await asyncio.sleep(0.5 * (2**attempt_429))
            continue

        if response.status_code >= 500:
            if not timeout_retried:
                timeout_retried = True
                await asyncio.sleep(5.0)
                continue
            logger.warning("mobula: HTTP %s on %s", response.status_code, url)
            return None, f"{UNAVAILABLE} (erreur serveur {response.status_code})"

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.warning("mobula: %s", exc)
            return None, f"{UNAVAILABLE} ({exc})"

        return response.json(), None


async def get_ohlcv(contract: str, *, blockchain: str = "base", period: str = "1d", amount: int = 60) -> OHLCVResult:
    """Real OHLCV candles (not a synthesis) for ``contract`` on ``blockchain``
    -- ``/api/2/token/ohlcv-history`` (schema verified live, 18/07: ``{t,o,h,l,c,v}``,
    ``t`` in milliseconds). ``amount`` defaults to 60 (consistent with the
    other tiers of the cascade, never the documented 2000 max -- unneeded
    for a momentum entry scan)."""
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
            if ts is not None and ts > 10_000_000_000:  # milliseconds -> seconds
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
