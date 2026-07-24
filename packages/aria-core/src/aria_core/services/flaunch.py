"""Flaunch discovery -- recently-created tokens on Base.

Primary source: Flaunch's official self-serve V2 API
(``api-v2.flayerlabs.xyz/v2/{chain}/coins/top?sort=new``, ``X-Api-Key``
header, ``FLAUNCH_API_KEY`` env var) -- richer data (price, volume, market
cap) than an on-chain event alone, confirmed live 24/07 once a real key was
provisioned (self-serve signup at ``builders.flaunch.gg``, free tier --
confirmed on the account's own dashboard, "Tier: free").

24/07 diligence trail (why this module has two paths):
1. Flaunch's OLDER free REST API (``dev-api.flayerlabs.xyz``,
   ``docs.flaunch.gg/references/restful-data-api``) was found down (HTTP
   522, confirmed on 3 separate attempts ~40 minutes apart).
2. The operator asked to build an on-chain path instead ("passe par le
   onchain") while a key for the newer V2 API got sorted out separately --
   see ``_fetch_recent_onchain`` below, kept as the fallback for whenever no
   key is configured or the API call fails (service outage, key revoked).
3. Auth gotcha found live once a real key arrived: the V2 API's own docs
   ("API Documentation", Scalar reference) confirm two DISTINCT auth modes
   for two different consumers -- ``X-Api-Key`` for "builders" (self-serve
   API keys, what this module uses) vs. ``Authorization: Bearer <id_token>``
   for "portal" (the dashboard's own web session, a Google-issued token) --
   sending the self-serve key as a Bearer token is rejected
   (``GOOGLE_INVALID_TOKEN``, confirmed live) because the server tries to
   validate it as a portal session token instead of an API key.
4. Rate limit verified two ways (never guessed): the live
   ``X-RateLimit-Limit-Minute/Hour/Day`` response headers on a real call
   (60/min, 1000/hour, 10000/day), cross-checked against the same numbers
   shown live on the account's own usage dashboard. The per-minute limit is
   the binding one for a polling client -- throttle calibrated to 90% of it
   per house doctrine (see ``FlaunchClient.__init__``).

On-chain fallback gotcha (kept for when the API is unavailable): the address
already on file in ``knowledge/launchpads.yaml``
(``0x51Bba15255406Cfe7099a42183302640ba7dAFDC``) is explicitly commented
"only old V1.0: doesn't use FeeEscrow" in Flaunch's own SDK source
(``flaunch-sdk/src/addresses.ts``) -- scanning 250k+ blocks of its logs found
ZERO ``PoolCreated`` events. ``FlaunchPositionManagerV1_2Address`` ("1.3" per
the SDK's own GitHub-release comment) is the contract actually receiving new
launches. The ``_params`` tuple's field count/positions differ between
versions (V1.0 has 10 fields, V1.2 has 11) -- only the first 3 fields (name,
symbol, tokenUri) are read here, always first regardless of version
(Solidity struct-extension convention appends fields, never prepends).
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

API_ROOT = "https://api-v2.flayerlabs.xyz/v2"
UNAVAILABLE = "flaunch data unavailable"
_FAIL_STREAK_WARN_THRESHOLD = 3

# "only old V1.0" per flaunch-sdk/src/addresses.ts -- kept here only as a
# documented historical note, never used for discovery (see module docstring).
_LEGACY_V1_0_POSITION_MANAGER = "0x51Bba15255406Cfe7099a42183302640ba7dAFDC"

# On-chain fallback contract (active for new launches), verified live 24/07.
POSITION_MANAGER_ADDRESS = "0x23321f11a6d44fd1ab790044fdfde5758c902fdc"


def flaunch_api_key() -> str:
    """Flaunch V2 API key from the env ONLY (never hardcoded, never logged)."""
    return os.environ.get("FLAUNCH_API_KEY", "").strip()


@dataclass
class FlaunchToken:
    contract: str  # the memecoin's own address, never the PositionManager's
    name: str | None = None
    symbol: str | None = None
    token_uri: str | None = None
    treasury: str | None = None
    pool_id: str | None = None
    block_number: int | None = None
    timestamp: str | None = None
    tx_hash: str | None = None
    price_usd: float | None = None
    market_cap_usd: float | None = None
    volume24h_usd: float | None = None


def _parse_pool_created(log) -> FlaunchToken | None:
    """``log`` is a ``blockscout.DecodedLog`` whose ``method_call`` starts
    with "PoolCreated". Reads only the top-level fields (stable across
    contract versions) plus the first 3 ``_params`` tuple entries -- see
    module docstring for why later tuple fields are never read here."""
    contract = log.parameters.get("_memecoin")
    if not contract:
        return None
    params = log.parameters.get("_params")
    name = symbol = token_uri = None
    if isinstance(params, (list, tuple)) and len(params) >= 3:
        name, symbol, token_uri = params[0], params[1], params[2]
    return FlaunchToken(
        contract=str(contract),
        name=str(name) if name else None,
        symbol=str(symbol) if symbol else None,
        token_uri=str(token_uri) if token_uri else None,
        treasury=log.parameters.get("_memecoinTreasury"),
        pool_id=log.parameters.get("_poolId"),
        block_number=log.block_number,
        timestamp=log.timestamp,
        tx_hash=log.tx_hash,
    )


def _to_float(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _parse_api_coin(payload) -> FlaunchToken | None:
    """Parses one entry from ``GET /v2/{chain}/coins/top`` -- confirmed live
    shape 24/07 against a real response (``tokenAddress``/``priceUSD``/
    ``marketCapUSD``/``twentyFourHourVolumeUSD``, among others)."""
    if not isinstance(payload, dict):
        return None
    contract = payload.get("tokenAddress")
    if not contract:
        return None
    return FlaunchToken(
        contract=str(contract),
        name=payload.get("name"),
        symbol=payload.get("symbol"),
        price_usd=_to_float(payload.get("priceUSD")),
        market_cap_usd=_to_float(payload.get("marketCapUSD")),
        volume24h_usd=_to_float(payload.get("twentyFourHourVolumeUSD")),
    )


class FlaunchClient:
    """Recently-created Flaunch tokens -- V2 API first (richer data, needs
    ``FLAUNCH_API_KEY``), on-chain fallback (``services/blockscout.py``) if
    no key is configured or the API call fails. Base only, for now."""

    # 24/07 -- calibrated to 90% of the confirmed 60 req/min (verified live via
    # the real `X-RateLimit-Limit-Minute: 60` response header on the actual
    # account/key, not guessed): 54/min = 1.111s. Per-account limits also
    # exist at 1000/hour and 10000/day (same headers) -- the per-minute one is
    # the binding constraint for a polling discovery client.
    def __init__(self, *, min_interval: float = 1.111) -> None:
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

    def _record_success(self) -> None:
        self._consecutive_failures = 0

    def _record_failure(self, detail: str) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= _FAIL_STREAK_WARN_THRESHOLD:
            logger.warning(
                "flaunch: %s consecutive failures (last: %s) -- no blocking",
                self._consecutive_failures, detail,
            )
        else:
            logger.info(
                "flaunch: call failed (%s/%s) -- %s",
                self._consecutive_failures, _FAIL_STREAK_WARN_THRESHOLD, detail,
            )

    async def _fetch_recent_via_api(self, *, chain: str, limit: int) -> list[FlaunchToken] | None:
        """``None`` (never ``[]``) means "API unusable, try the on-chain
        fallback" -- distinct from a real empty result."""
        key = flaunch_api_key()
        if not key:
            return None

        await self._throttle()
        url = f"{API_ROOT}/{chain}/coins/top"
        params = {"sort": "new", "limit": min(max(limit, 1), 50)}
        # NEVER "Authorization: Bearer" -- validated server-side as a Google
        # OAuth token, confirmed live 24/07 (GOOGLE_INVALID_TOKEN on a real,
        # valid self-serve key). X-Api-Key is the correct header.
        headers = {"X-Api-Key": key}
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(url, params=params, headers=headers)
        except httpx.TransportError as exc:
            self._record_failure(f"{url} -> {exc}")
            return None

        if response.status_code in (401, 403):
            self._record_failure(f"{url} -> HTTP {response.status_code} (key?)")
            return None
        if response.status_code == 429 or response.status_code >= 500:
            self._record_failure(f"{url} -> HTTP {response.status_code}")
            return None
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            self._record_failure(f"{url} -> HTTP {exc.response.status_code}")
            return None

        self._record_success()
        data = response.json()
        items = data.get("data") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return None
        tokens = [t for t in (_parse_api_coin(item) for item in items) if t is not None]
        return tokens[:limit]

    async def _fetch_recent_onchain(self, *, limit: int, max_pages: int) -> list[FlaunchToken]:
        from aria_core.services.blockscout import get_blockscout_client

        client = get_blockscout_client("base")
        result = await client.get_contract_logs_bounded(POSITION_MANAGER_ADDRESS, max_pages=max_pages)
        if not result.available:
            return []

        tokens: list[FlaunchToken] = []
        seen: set[str] = set()
        for log in result.logs:
            if not log.method_call.startswith("PoolCreated("):
                continue
            token = _parse_pool_created(log)
            if token is None or token.contract in seen:
                continue
            seen.add(token.contract)
            tokens.append(token)
            if len(tokens) >= limit:
                break
        return tokens

    async def fetch_recent(
        self, *, chain: str = "base", limit: int = 50, max_pages: int = 5,
    ) -> list[FlaunchToken]:
        api_result = await self._fetch_recent_via_api(chain=chain, limit=limit)
        if api_result is not None:
            return api_result
        return await self._fetch_recent_onchain(limit=limit, max_pages=max_pages)


flaunch_client = FlaunchClient()
