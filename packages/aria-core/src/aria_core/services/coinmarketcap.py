"""CoinMarketCap DEX client (read-only) -- 3rd pricing layer for wallet-scoring
(#157, 14/07), after GeckoTerminal and the DexScreener diagnosis.

"Dome" doctrine (identical to blockscout.py/geckoterminal.py/dexscreener.py):
- 429: exponential backoff, 3 attempts max, then give up without blocking the pipeline.
- Timeout / 5xx: 1 retry after 5s, then explicit degradation (``available=False``).
- Missing data is never replaced by a guess.

API key: ``COINMARKETCAP_API_KEY`` read via ``os.environ.get`` on EVERY call
(never cached at import time -- same pattern as ``tavily.py``, simpler to test
with ``monkeypatch.setenv``/``delenv``). If present: base URL without
``/public-api`` + ``X-CMC_PRO_API_KEY`` header, higher limits. If absent:
automatic fallback to the public keyless tier, no call ever blocked.

Honest caveat (live test on 14/07, no key): ``/v1/dex/token/pools`` and
``/v1/k-line/candles`` returned HTTP 500 ("The system is busy...") on 5
separate keyless attempts, never a success -- this tier appears to NOT
actually unlock these two endpoints. Only ``/v4/dex/pairs/quotes/latest`` was
confirmed working keyless (with a known pool/pair address,
``network_slug`` -- not ``network_id`` -- as the chain parameter, confirmed
live). In practice, this layer will likely only fetch prices once the real
VPS key is present. The exact response schema of ``/v1/k-line/candles`` could
NOT be confirmed live (endpoint unavailable during the test, official doc with
no payload example) -- the parsing below is best-effort and tolerant: any
unexpected shape degrades to ``available=False``, never an exception, never a
guessed value.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime

import httpx

from aria_core.skills.ta_levels import Candle

# 18/07 -- PoolMetadata/OHLCVResult were duplicated identically from
# geckoterminal.py (found by a VPS Secondaire audit), except PoolMetadata
# which had diverged: geckoterminal.py got ``reserve_usd`` (15/07,
# anti-dust/scam-pool defense, #157) that this copy never received. Direct
# reuse instead of a 2nd copy to keep in sync -- eliminates the duplication
# AND the divergence in one move, without inventing new logic (CMC doesn't
# populate ``reserve_usd`` for now, it stays ``None`` -- fail-open behavior
# already documented in geckoterminal.py).
from aria_core.services.geckoterminal import OHLCVResult, PoolMetadata

logger = logging.getLogger(__name__)

UNAVAILABLE = "donnée CoinMarketCap indisponible"

BASE_URL_KEYLESS = "https://pro-api.coinmarketcap.com/public-api"
BASE_URL_KEYED = "https://pro-api.coinmarketcap.com"

# Same chain vocabulary as blockscout.CHAIN_IDS / geckoterminal.GECKO_NETWORK_SLUGS
# (13 chains, #157 dynamic TVL ranking, 14/07). "bnb" removed -- Blockscout
# doesn't serve BNB Smart Chain (cf. blockscout.CHAIN_IDS), no point keeping a
# CMC slug that no active chain will ever reach.
#
# Only "base" was verified live tonight: /v4/dex/pairs/quotes/latest responded
# successfully keyless (`network_slug=base`). The other 12 values are
# reasonable GUESSES (same names as GeckoTerminal most of the time, CMC has no
# equivalent public "networks" registry found to verify line by line) --
# documented as NOT verified, never presented as confirmed. To fix if a real-
# conditions test (with the VPS key) reveals a divergence, same doctrine as
# the rest of this file.
CMC_NETWORK_SLUGS: dict[str, str] = {
    "base": "base",  # verified live
    "ethereum": "ethereum",  # unverified
    "arbitrum": "arbitrum",  # unverified
    "optimism": "optimism",  # unverified
    "polygon": "polygon",  # unverified -- GeckoTerminal says "polygon_pos", different CMC guess (usual short name)
    "celo": "celo",  # unverified
    "gnosis": "gnosis",  # unverified -- GeckoTerminal says "xdai", different CMC guess (usual name, no guarantee)
    "scroll": "scroll",  # unverified
    "zksync": "zksync",  # unverified
    "rootstock": "rootstock",  # unverified
    "unichain": "unichain",  # unverified
    "soneium": "soneium",  # unverified
    "mode": "mode",  # unverified
}


def _api_key() -> str | None:
    return os.environ.get("COINMARKETCAP_API_KEY", "").strip() or None


# 21/07 -- first proactive throttle for this client (there was none -- only a
# reactive retry after an already-received 429). CLAUDE.md "90% calibrated
# throughput" doctrine: real tier CONFIRMED LIVE on the real VPS key via GET
# /v1/key/info (never guessed) -- Basic tier, rate_limit_minute=50. 90% of
# 50/min = 45/min = 1.333s. The keyless tier (no key) has no separately
# confirmed figure -- reuses the same cautious default throttle (fail-safe:
# keyless is structurally not more generous than keyed).
_MIN_INTERVAL = 1.333
_last_request = 0.0
_throttle_lock = asyncio.Lock()


async def _throttle() -> None:
    global _last_request
    async with _throttle_lock:
        now = asyncio.get_event_loop().time()
        wait = _MIN_INTERVAL - (now - _last_request)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_request = asyncio.get_event_loop().time()


async def _get_json(path: str, *, params: dict) -> tuple[object | None, str | None]:
    """GET with retry on 429/5xx/timeout -- same policy as
    blockscout.py/geckoterminal.py/dexscreener.py. Automatically switches to
    the keyed tier (base URL + header) if ``COINMARKETCAP_API_KEY`` is
    present, otherwise the keyless tier -- never blocking if the key is
    absent."""
    api_key = _api_key()
    base_url = BASE_URL_KEYED if api_key else BASE_URL_KEYLESS
    headers = {"Accept": "application/json"}
    if api_key:
        headers["X-CMC_PRO_API_KEY"] = api_key
    url = f"{base_url}{path}"

    attempt_429 = 0
    timeout_retried = False

    while True:
        await _throttle()
        try:
            async with httpx.AsyncClient(timeout=18.0) as client:
                response = await client.get(url, params=params, headers=headers)
        except httpx.TransportError as exc:
            if not timeout_retried:
                timeout_retried = True
                await asyncio.sleep(5.0)
                continue
            logger.warning("coinmarketcap: timeout on %s -> %s", url, exc)
            return None, f"{UNAVAILABLE} (timeout CoinMarketCap)"

        if response.status_code == 429:
            attempt_429 += 1
            if attempt_429 >= 3:
                logger.warning("coinmarketcap: HTTP 429 on %s after %s attempts", url, attempt_429)
                return None, f"{UNAVAILABLE} (rate limit CoinMarketCap)"
            await asyncio.sleep(0.5 * (2**attempt_429))
            continue

        if response.status_code >= 500:
            if not timeout_retried:
                timeout_retried = True
                await asyncio.sleep(5.0)
                continue
            logger.warning("coinmarketcap: HTTP %s on %s", response.status_code, url)
            return None, f"{UNAVAILABLE} (erreur serveur CoinMarketCap {response.status_code})"

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.warning("coinmarketcap: %s", exc)
            return None, f"{UNAVAILABLE} ({exc})"

        payload = response.json()
        if not isinstance(payload, dict):
            return None, f"{UNAVAILABLE} (réponse inattendue)"

        # CMC envelope: an HTTP 200 can still carry a logical failure
        # (`status.error_code` != "0") -- never interpreted as success just
        # because the HTTP code is 200.
        status = payload.get("status")
        if isinstance(status, dict):
            error_code = str(status.get("error_code", "0"))
            if error_code not in ("0", ""):
                error_message = status.get("error_message") or error_code
                logger.warning("coinmarketcap: error_code=%s on %s -> %s", error_code, url, error_message)
                return None, f"{UNAVAILABLE} ({error_message})"

        return payload, None


async def resolve_primary_pool(token_address: str, *, network_slug: str = "base") -> PoolMetadata:
    """Resolves the highest-liquidity pool for ``token_address`` via
    ``/v1/dex/token/pools`` -- same selection logic as
    ``geckoterminal.resolve_primary_pool`` (defensive comparison, malformed
    liquidity treated as 0, never a crash). Honest caveat: this endpoint
    returned HTTP 500 on every keyless attempt live tonight --
    ``available=False`` is therefore the expected outcome without a valid API
    key."""
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
    """OHLCV candles for ``pool_address`` via ``/v1/k-line/candles``. Tolerant
    parsing (schema not confirmed live, cf. module docstring): accepts several
    plausible field names, any unexpected shape -> `available=False`, never a
    fabricated candle."""
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
            if ts is not None and ts > 10_000_000_000:  # milliseconds -> seconds
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
