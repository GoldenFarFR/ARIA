"""Robinhood Chain "Stock Token" registry (#309, 16/08) -- tokenized US
equities/ETFs (NVDA, AAPL, GOOG...) issued as standard ERC-20 contracts by
Robinhood Assets (Jersey) Limited directly on Robinhood Chain (chain_id
4663), tradable on the same DEXs as ordinary memecoins. The momentum
pipeline's golden-pocket/RSI-divergence thesis (``momentum_entry.py``) is
built for speculative memecoin momentum and makes no sense applied to a
tokenized equity (different market structure, corporate-action-adjusted
price via a ``uiMultiplier()`` extension -- see
``docs.robinhood.com/chain/stock-tokens``, ERC-8056 "Scaled UI Amount
Extension"). This module exists to keep such a token out of the momentum
funnel entirely, wherever a candidate happens to be on "robinhood".

Detection method verified live 16/08 (WebSearch + direct ``curl``, never
guessed): Robinhood Chain's own docs page (``docs.robinhood.com/chain/
contracts``) states its token table is "generated live from the on-chain
asset registry" but is JS-rendered (no static HTML list to scrape). The
underlying REST endpoint it calls, referenced from ``docs.robinhood.com/
chain/stock-token-apis``, is real and directly confirmed reachable:

    GET https://api.robinhood.com/rhj/assets

No API key, no auth header. Live-tested 16/08: HTTP 200, ``application/
json``, 194 assets, every one carrying a ``deployments[]`` entry with
``chainId: 4663`` and a unique ``contractAddress``, plus an ``isin`` field
(a real ISIN security identifier on each entry) -- strong confirmation
these are genuine regulated stock/ETF tokens, not an arbitrary list. No
on-chain registry CONTRACT address was found documented anywhere (the docs
page never states one, only "generated live from the on-chain asset
registry" with no address) -- this REST endpoint is the only verified,
reliable, machine-readable source found; there is no purely on-chain
(ABI/bytecode/factory) marker documented to tell a Stock Token apart from
an ordinary ERC-20 without this list (confirmed via docs.robinhood.com/
chain/stock-tokens: "standard ERC-20 tokens" with a "Standard ERC-20
interface", the only extension being the corporate-action multiplier,
which any other token could also implement and is therefore not a reliable
positive signal).

Robinhood's own docs mention a "60 requests/second" rate limit on this
endpoint (and a 15s response cache) -- NOT independently burst-tested
(project doctrine: an unverified documented figure could be wrong), but
irrelevant in practice here: this module caches the full asset list for
``_CACHE_TTL_SECONDS`` and calls the endpoint at most a couple of times per
hour, several orders of magnitude below even a skeptical read of that
limit.

Dormant on 16/08: ``momentum_entry.DEFAULT_CHAINS`` is Base-only (narrowed
27/07) and no caller passes ``chains=(..., "robinhood", ...)`` anywhere in
the current codebase, so ``is_stock_token`` never actually reaches its
network branch today (chain-mismatch short-circuit below). Wired into
``evaluate_hard_gates`` anyway (ANTICIPATION doctrine, cf. CLAUDE.md) so
the guardrail is already armed the day Robinhood Chain re-enters
discovery scope -- never something to rebuild reactively at that point."""
from __future__ import annotations

import asyncio
import logging
import time

import httpx

logger = logging.getLogger(__name__)

UNAVAILABLE = "Robinhood stock-token registry unavailable"

ASSETS_URL = "https://api.robinhood.com/rhj/assets"

# Robinhood Chain's own numeric chain_id (verified live 16/08 -- same value
# already sourced independently for Blockscout coverage, see
# ``services/blockscout.py::CHAIN_IDS["robinhood"]`` and
# ``momentum_entry.py``'s ``_DEXSCREENER_TO_GOPLUS_CHAIN_ID["robinhood"]``,
# both "4663"). Kept as an int here to match the API's own ``chainId`` field
# type without a cast at every comparison site.
ROBINHOOD_CHAIN_ID = 4663

# Slow-changing registry (new stock tokens are added on discrete corporate
# events, not continuously) -- no need for a short TTL like the per-candidate
# holder/price caches elsewhere in this pipeline. 1h keeps the endpoint call
# vanishingly rare (see module docstring) while still picking up a newly
# listed ticker same-session rather than requiring a redeploy.
_CACHE_TTL_SECONDS = 3600.0

# Module-level cache: (addresses, fetched_at). ``None`` addresses means
# "never successfully fetched yet". A stale-but-present cache is preferred
# over a failed refresh (see ``get_stock_token_addresses`` below) -- once
# warmed, this registry never silently goes back to empty just because one
# refresh attempt failed.
_cache_addresses: frozenset[str] | None = None
_cache_fetched_at: float = 0.0


async def _fetch_assets_json() -> tuple[object | None, str | None]:
    """GET with the same retry-on-429/5xx/timeout dome as the other clients
    in this folder (blockscout.py/defillama.py) -- 429: backoff, 3 attempts
    max; timeout/5xx: 1 retry after 5s, then explicit degradation."""
    attempt_429 = 0
    timeout_retried = False

    while True:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(ASSETS_URL, headers={"Accept": "application/json"})
        except httpx.TransportError as exc:
            if not timeout_retried:
                timeout_retried = True
                await asyncio.sleep(5.0)
                continue
            logger.warning("robinhood_stock_tokens: timeout on %s -> %s", ASSETS_URL, exc)
            return None, f"{UNAVAILABLE} (timeout)"

        if response.status_code == 429:
            attempt_429 += 1
            if attempt_429 >= 3:
                logger.warning(
                    "robinhood_stock_tokens: HTTP 429 on %s after %s attempts", ASSETS_URL, attempt_429,
                )
                return None, f"{UNAVAILABLE} (rate limit)"
            await asyncio.sleep(0.5 * (2**attempt_429))
            continue

        if response.status_code >= 500:
            if not timeout_retried:
                timeout_retried = True
                await asyncio.sleep(5.0)
                continue
            logger.warning("robinhood_stock_tokens: HTTP %s on %s", response.status_code, ASSETS_URL)
            return None, f"{UNAVAILABLE} (server error {response.status_code})"

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.warning("robinhood_stock_tokens: %s", exc)
            return None, f"{UNAVAILABLE} ({exc})"

        return response.json(), None


async def fetch_stock_token_addresses() -> frozenset[str] | None:
    """One live GET, no cache -- returns the full set of lowercased contract
    addresses deployed with ``chainId == ROBINHOOD_CHAIN_ID`` across every
    asset in the registry, or ``None`` on any network/shape failure (never a
    silently-empty set confused with "no stock tokens exist"). Callers wanting
    the cached/throttled path should use ``get_stock_token_addresses``
    instead -- this is the raw fetch it wraps."""
    data, error = await _fetch_assets_json()
    if error is not None:
        logger.warning("robinhood_stock_tokens: registry unavailable -> %s", error)
        return None
    if not isinstance(data, dict):
        logger.warning("robinhood_stock_tokens: unexpected response shape (not a dict)")
        return None
    assets = data.get("assets")
    if not isinstance(assets, list):
        logger.warning("robinhood_stock_tokens: unexpected response shape (no 'assets' list)")
        return None

    addresses: set[str] = set()
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        deployments = asset.get("deployments")
        if not isinstance(deployments, list):
            continue
        for deployment in deployments:
            if not isinstance(deployment, dict):
                continue
            if deployment.get("chainId") != ROBINHOOD_CHAIN_ID:
                continue
            address = deployment.get("contractAddress")
            if isinstance(address, str) and address.strip():
                addresses.add(address.strip().lower())

    return frozenset(addresses)


async def get_stock_token_addresses() -> frozenset[str]:
    """Cached getter (``_CACHE_TTL_SECONDS``) -- the function every caller in
    the pipeline should actually use. On a failed refresh, falls back to the
    last successfully-fetched set (even if stale) rather than treating a
    transient outage as "no stock tokens exist" -- same fail-safe doctrine as
    the rest of this pipeline (never invent/erase a data point on a network
    hiccup). Returns an empty frozenset only if NO fetch has ever succeeded
    yet (cold start with the registry down) -- deliberately fail-OPEN in that
    single edge case (never blocks a legitimate momentum candidate on total
    registry unavailability), logged clearly so it's never a silent gap."""
    global _cache_addresses, _cache_fetched_at

    now = time.time()
    if _cache_addresses is not None and (now - _cache_fetched_at) < _CACHE_TTL_SECONDS:
        return _cache_addresses

    fresh = await fetch_stock_token_addresses()
    if fresh is not None:
        _cache_addresses = fresh
        _cache_fetched_at = now
        return _cache_addresses

    if _cache_addresses is not None:
        logger.info(
            "robinhood_stock_tokens: refresh failed, serving stale cache (%d addresses, age %.0fs)",
            len(_cache_addresses), now - _cache_fetched_at,
        )
        return _cache_addresses

    logger.warning(
        "robinhood_stock_tokens: registry never successfully fetched -- "
        "treating as empty (fail-open), no candidate blocked on this basis"
    )
    return frozenset()


async def is_stock_token(contract: str, chain: str) -> bool:
    """True if ``contract`` is a registered Robinhood Chain Stock Token.

    Cheap short-circuit on every chain other than "robinhood" (no network
    call, no cache read) -- this is called from the shared hard-gates path
    (``momentum_entry.evaluate_hard_gates``) that every candidate on every
    chain passes through, so it must stay free for the overwhelming majority
    (Base) of candidates."""
    if (chain or "").strip().lower() != "robinhood":
        return False
    normalized = (contract or "").strip().lower()
    if not normalized:
        return False
    addresses = await get_stock_token_addresses()
    return normalized in addresses
