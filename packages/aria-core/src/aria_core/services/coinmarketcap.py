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
#
# 02/08 -- tightened 45->40/min (1.333s->1.5s), operator's explicit call
# after a real cluster of 18 HTTP 500s (not 429s -- a server-side error, not
# our own rate-limit being exceeded) observed within ~4 minutes on
# /v1/dex/token/pools. Lowering our own call rate doesn't directly explain a
# 500, but is a reasonable precaution if CMC's backend is itself strained by
# aggregate load across its clients -- disclosed honestly: this may not fix
# the root cause if it's a pure transient CMC-side outage, revisit if 500s
# keep recurring after this change.
_MIN_INTERVAL = 1.5
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


async def get_ohlcv(contract_address: str, *, network_slug: str = "base") -> OHLCVResult:
    """OHLCV candles for the TOKEN ``contract_address`` (never a pool/pair
    address, see 04/08 note below) via ``/v1/k-line/candles``.

    04/08 -- two real bugs found live, both confirmed against real requests
    (operator OHLCV-cascade quality audit), not from docs alone:
    1. Wrong parameter names caused a sustained HTTP 500 (reproduced
       on-demand, not a transient outage): ``network_slug``/
       ``contract_address``/``time_period="hourly"`` were never this
       endpoint's real parameters. Official docs (coinmarketcap.com/api/
       documentation/pro-api-reference/ohlcv) confirm the real ones:
       ``platform`` ("Platform name or id"), ``address`` ("Token or pool
       address"), ``interval`` (enum including "1h", not "hourly").
    2. Even with correct parameter names, passing the POOL/pair address
       (as every other provider in this cascade expects) silently returned
       real-looking but YEAR-STALE data (confirmed live: last candle dated
       2025-07-29 against a request made 2026-08-04) -- worse than the 500,
       since a stale-but-well-formed response looks like a success. Passing
       the TOKEN contract address instead resolves to live, current data
       (confirmed live: last candle within the current hour, close price
       matching the live pair quote within normal noise). This confirms the
       module's own docstring warning ("schema not confirmed live") -- this
       path had never actually been exercised against a real successful,
       FRESH response before now.

    Row shape is a positional array, not a dict, also never confirmed live
    before now: ``[open, high, low, close, volume, ts_ms, trader_count]``."""
    data, error = await _get_json(
        "/v1/k-line/candles", params={"platform": network_slug, "address": contract_address, "interval": "1h"}
    )
    if error is not None:
        return OHLCVResult(candles=[], available=False, error=error)

    raw_candles = data.get("data")
    if not isinstance(raw_candles, list) or not raw_candles:
        return OHLCVResult(candles=[], available=False, error=f"{UNAVAILABLE} (aucune bougie)")

    candles: list[Candle] = []
    for row in raw_candles:
        if not isinstance(row, (list, tuple)) or len(row) < 6:
            continue
        try:
            o = float(row[0])
            h = float(row[1])
            l = float(row[2])
            c = float(row[3])
            v = float(row[4] or 0.0)
            ts_raw = row[5]
            ts = int(ts_raw) if ts_raw is not None else None
            if ts is not None and ts > 10_000_000_000:  # milliseconds -> seconds
                ts //= 1000
        except (TypeError, ValueError):
            continue
        if ts is None:
            continue
        candles.append(Candle(ts=ts, open=o, high=h, low=l, close=c, volume=v))

    if not candles:
        return OHLCVResult(candles=[], available=False, error=f"{UNAVAILABLE} (bougies illisibles)")

    candles.sort(key=lambda c: c.ts)
    return OHLCVResult(candles=candles, available=True, error=None)
