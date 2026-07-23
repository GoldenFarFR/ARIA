"""Webacy client (read-only) -- 2nd contract security opinion, fallback/complement
to GoPlus (#194, 21/07).

Context: GoPlus remains the ONLY hard honeypot guard of the momentum pipeline,
but its free tier is very tight (10 real tokens/min, 10,000/month -- calibrated
on 21/07 after discovering the real per-token billing structure). Webacy
verified live that day (docs.webacy.com/api-reference): Contract Risk API,
Base in "Full Support", free Demo tier 2 req/s (burst 5), 2,000 requests/month
-- a COMPLEMENTARY profile to GoPlus (much more generous per-second rate,
lower monthly cap), not a replacement.

"Dome" doctrine (identical to goplus.py/mobula.py):
- 429: exponential backoff, 3 attempts max, then gives up without blocking
  the pipeline.
- Timeout / 5xx: 1 retry after 5s, then explicit degradation (``available=False``).
- Missing data is never replaced by a guess.

API key: ``WEBACY_API_KEY`` -- REQUIRED from the very first call (Demo tier,
no documented public path). Client neutralized (immediate ``available=False``,
no network call) if the key is absent -- never a pipeline block.
``x-api-key`` header confirmed via docs.webacy.com/api-reference/contract-risk
(verified live on 21/07 -- never guessed, a lesson from the GoPlus header bug
the same day).

**NOT YET wired into ``momentum_entry.py``** -- this module is a standalone
client, tested in isolation. Wiring it as a fallback/complement to the
GoPlus honeypot guard (``_check_honeypot``) remains a separate decision, not
made here.

**Response schema NOT VERIFIED UNDER REAL CONDITIONS** -- no API key
available at the time of writing (21/07). Based on the official docs only
(``score``/``tags``/``categories``, e.g. ``contract_possible_drainer``). To
be reconfirmed with a real call as soon as a Demo key is configured -- never
deploy this module without that test, same discipline as the rest of the
project."""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)

UNAVAILABLE = "donnée Webacy indisponible"

BASE_URL = "https://api.webacy.com"

# Webacy chain vocabulary (docs.webacy.com/essentials/supported-blockchains) --
# NOT the same vocabulary as GoPlus (numeric ids) nor GeckoTerminal (specific
# slugs) -- a dedicated translation table, like for coinmarketcap.py.
WEBACY_CHAIN_SLUGS: dict[str, str] = {
    "base": "base",
    "ethereum": "eth",
    "polygon": "pol",
    "optimism": "opt",
    "arbitrum": "arb",
    "bsc": "bsc",
    "solana": "sol",
}

# 21/07 -- calibrated to 90% of the confirmed Demo tier (2 req/s, docs.webacy.com/
# essentials/rate-limits), CLAUDE.md "90% calibrated throughput" doctrine:
# 1.8 req/s = 0.556s. Monthly cap (2,000 requests) NOT handled here (no
# persistent counter) -- to be added if this client is ever wired into prod,
# same guardrail family as the one proposed (not yet built) for GoPlus.
_MIN_INTERVAL = 0.556
_last_call_at = 0.0
_throttle_lock = asyncio.Lock()


def webacy_api_key() -> str | None:
    return os.environ.get("WEBACY_API_KEY", "").strip() or None


def webacy_configured() -> bool:
    return bool(webacy_api_key())


async def _throttle() -> None:
    global _last_call_at
    async with _throttle_lock:
        now = asyncio.get_event_loop().time()
        wait = _MIN_INTERVAL - (now - _last_call_at)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_call_at = asyncio.get_event_loop().time()


@dataclass
class ContractRiskResult:
    contract: str
    score: float | None = None
    tags: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    is_drainer: bool | None = None
    available: bool = True
    error: str | None = None


async def _get_json(path: str, *, params: dict | None = None) -> tuple[object | None, str | None]:
    """GET with the dome's error policy -- same pattern as goplus.py."""
    api_key = webacy_api_key()
    if not api_key:
        return None, f"{UNAVAILABLE} (WEBACY_API_KEY absente)"

    url = f"{BASE_URL}{path}"
    headers = {"x-api-key": api_key, "Accept": "application/json"}
    attempt_429 = 0
    retried = False

    while True:
        await _throttle()
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(url, params=params, headers=headers)
        except httpx.TransportError as exc:
            if not retried:
                retried = True
                await asyncio.sleep(5.0)
                continue
            logger.warning("webacy: timeout sur %s -> %s", url, exc)
            return None, f"{UNAVAILABLE} (timeout Webacy)"

        if response.status_code == 429:
            attempt_429 += 1
            if attempt_429 >= 3:
                logger.warning("webacy: HTTP 429 sur %s apres %s tentatives", url, attempt_429)
                return None, f"{UNAVAILABLE} (rate limit Webacy)"
            await asyncio.sleep(0.5 * (2**attempt_429))
            continue

        if response.status_code >= 500:
            if not retried:
                retried = True
                await asyncio.sleep(5.0)
                continue
            logger.warning("webacy: HTTP %s sur %s", response.status_code, url)
            return None, f"{UNAVAILABLE} (erreur serveur Webacy)"

        if response.status_code in (400, 401, 404):
            return None, f"{UNAVAILABLE} (HTTP {response.status_code})"
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.warning("webacy: %s", exc)
            return None, f"{UNAVAILABLE} ({exc})"

        return response.json(), None


async def get_contract_risk(contract: str, *, chain: str = "base") -> ContractRiskResult:
    """Webacy contract risk analysis -- ``GET /api/v1/risk-score/contract/{address}``.
    Path CORRECTED on 21/07: the 1st version of this module (based on an
    ambiguous doc page, docs.webacy.com/api-reference/contract-risk) used
    ``/contracts/{contractAddress}`` -- wrong, confirmed by cross-checking the
    official OpenAPI (docs.webacy.com/openapi.json, the most authoritative
    source) AND the real Quickstart guide (which shows a 3rd, different path,
    ``/addresses/{address}``, for a distinct generic use -- not this one).
    Response schema (``score``/``tags``/``categories``) confirmed correct in
    the OpenAPI. ``chain`` (ARIA vocabulary, e.g. "base") translated via
    ``WEBACY_CHAIN_SLUGS`` -- an uncovered chain -> explicit ``available=False``,
    never a guessed URL."""
    webacy_chain = WEBACY_CHAIN_SLUGS.get(chain)
    if not webacy_chain:
        return ContractRiskResult(
            contract=contract, available=False,
            error=f"chaîne {chain} non couverte par Webacy",
        )

    data, error = await _get_json(f"/api/v1/risk-score/contract/{contract}", params={"chain": webacy_chain})
    if error is not None:
        return ContractRiskResult(contract=contract, available=False, error=error)
    if not isinstance(data, dict):
        return ContractRiskResult(contract=contract, available=False, error=UNAVAILABLE)

    categories = list((data.get("categories") or {}).keys()) if isinstance(data.get("categories"), dict) else list(data.get("categories") or [])
    return ContractRiskResult(
        contract=contract,
        score=data.get("score"),
        tags=list(data.get("tags") or []),
        categories=categories,
        is_drainer="contract_possible_drainer" in categories,
        available=True,
        error=None,
    )


webacy_client_configured = webacy_configured  # alias explicite pour les appelants
