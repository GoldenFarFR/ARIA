"""DefiLlama client (read-only, public, keyless) -- TVL ranking of EVM
chains for the /walletscore dynamic scan (#157, 07/14).

"Guardrail" doctrine (identical to blockscout.py/geckoterminal.py/dexscreener.py/
coinmarketcap.py):
- 429: exponential backoff, 3 attempts max, then abandon without blocking the pipeline.
- Timeout / 5xx: 1 retry after 5s, then explicit degradation (`None`).
- No missing data is ever replaced by a guess.

This module knows NOTHING about SQLite -- pure HTTP client, like the other
clients in this folder. Caching (`wallet_scoring_chain_ranking` table) lives
in `smart_money.py`, which orchestrates the network call + the DB write.

Filtering to confirmed chains happens via `blockscout.CHAIN_IDS` -- the SOLE
source of truth, never a duplicated registry here (a second copy could have
silently diverged, like the "bnb" forgotten in `DEFAULT_SCAN_CHAINS` before
its fix that same evening)."""

from __future__ import annotations

import asyncio
import logging

import httpx

logger = logging.getLogger(__name__)

UNAVAILABLE = "donnée DefiLlama indisponible"

BASE_URL = "https://api.llama.fi"


async def _get_json(path: str) -> tuple[object | None, str | None]:
    """GET with retry on 429/5xx/timeout -- same policy as the other clients
    in this folder."""
    url = f"{BASE_URL}{path}"
    attempt_429 = 0
    timeout_retried = False

    while True:
        try:
            async with httpx.AsyncClient(timeout=18.0) as client:
                response = await client.get(url, headers={"Accept": "application/json"})
        except httpx.TransportError as exc:
            if not timeout_retried:
                timeout_retried = True
                await asyncio.sleep(5.0)
                continue
            logger.warning("defillama: timeout on %s -> %s", url, exc)
            return None, f"{UNAVAILABLE} (timeout DefiLlama)"

        if response.status_code == 429:
            attempt_429 += 1
            if attempt_429 >= 3:
                logger.warning("defillama: HTTP 429 on %s after %s attempts", url, attempt_429)
                return None, f"{UNAVAILABLE} (rate limit DefiLlama)"
            await asyncio.sleep(0.5 * (2**attempt_429))
            continue

        if response.status_code >= 500:
            if not timeout_retried:
                timeout_retried = True
                await asyncio.sleep(5.0)
                continue
            logger.warning("defillama: HTTP %s on %s", response.status_code, url)
            return None, f"{UNAVAILABLE} (erreur serveur DefiLlama {response.status_code})"

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.warning("defillama: %s", exc)
            return None, f"{UNAVAILABLE} ({exc})"

        return response.json(), None


async def fetch_chain_tvl_ranking() -> list[tuple[str, float]] | None:
    """TVL ranking of confirmed ARIA chains, sorted descending.

    GET ``/v2/chains`` (public, keyless), filters by numeric ``chainId``
    (never by ``name`` -- DefiLlama's labels don't always follow ARIA's
    vocabulary, e.g. "ZKsync Era" vs our "zksync") against
    ``blockscout.CHAIN_IDS``, the sole source of truth for confirmed
    queryable chains (Blockscout x GeckoTerminal, established 07/14).

    Returns ``None`` on any network failure or unexpected response shape --
    never an empty list silently confused with "zero TVL everywhere"."""
    from aria_core.services.blockscout import CHAIN_IDS

    data, error = await _get_json("/v2/chains")
    if error is not None:
        logger.warning("defillama: TVL ranking unavailable -> %s", error)
        return None
    if not isinstance(data, list):
        logger.warning("defillama: /v2/chains response has unexpected shape")
        return None

    chain_id_to_name = {cid: name for name, cid in CHAIN_IDS.items()}
    ranked: dict[str, float] = {}
    for entry in data:
        if not isinstance(entry, dict):
            continue
        chain_id = entry.get("chainId")
        name = chain_id_to_name.get(chain_id)
        if name is None:
            continue
        try:
            tvl = float(entry.get("tvl") or 0.0)
        except (TypeError, ValueError):
            tvl = 0.0
        ranked[name] = tvl

    return sorted(ranked.items(), key=lambda item: item[1], reverse=True)
