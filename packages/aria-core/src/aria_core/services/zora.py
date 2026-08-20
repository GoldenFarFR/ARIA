"""Zora Coins discovery -- recently-created "content coins" on Base.

20/08 diligence (real, live-verified, not built on a hypothesis -- see
CLAUDE.md's "depth proportional to the stakes" doctrine):

1. Real, public, unauthenticated GraphQL-backed REST endpoint confirmed live:
   ``GET https://api-sdk.zora.engineering/explore?listType=NEW&count=N``
   returned a real token created MINUTES before the test call, no API key
   sent at all. Base URL and path confirmed by inspecting the ACTUAL
   installed npm package (``@zoralabs/coins-sdk`` 0.8.0, downloaded from the
   npm registry and read directly -- ``src/client/client.gen.ts``'s
   ``baseUrl``, ``src/client/sdk.gen.ts``'s ``getExplore`` -> ``url:
   "/explore"``), never guessed from the rendered (JS-only, unreadable by a
   plain HTML fetch) Swagger UI page.
2. API key (``ZORA_API_KEY`` env var, header ``api-key``) is OPTIONAL --
   Zora's own docs: unauthenticated calls work but are subject to a stricter,
   UNDOCUMENTED rate limit ("Authentication is strongly recommended... to
   prevent rate limiting"). No numeric limit is published anywhere (checked:
   no ``X-RateLimit-*`` header on a real unauthenticated call either) -- per
   house doctrine (CLAUDE.md "Throughput calibrated to 90% of real
   capacity"), this means reactive backoff only, NEVER a fabricated
   proactive per-minute throttle. Self-serve key creation:
   https://zora.co/settings/developer (not provisioned as of this diligence
   -- this module works unauthenticated, a key can be added later purely to
   raise the ceiling, no code change needed beyond setting the env var).
3. ``listType=NEW`` is the discovery-relevant list (chronological, most
   recently created coins) among ~25 real list types the SDK exposes (top
   gainers/most valuable/trending/etc.) -- only this one matches this
   module's job (fast discovery, mirroring ``services/flaunch.py``'s own
   scope), the others are deliberately not wired here.
4. Real response shape confirmed against the ACTUAL SDK's generated
   TypeScript types (``types.gen.ts``'s ``GetExploreResponses``), not the
   docs page (which omitted several fields): ``exploreList.edges[].node``
   carries ``address``/``name``/``symbol``/``marketCap``/``totalVolume``/
   ``chainId``/``createdAt`` among others -- only the fields this module
   actually reads are parsed here.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

API_ROOT = "https://api-sdk.zora.engineering"
_FAIL_STREAK_WARN_THRESHOLD = 3


def zora_api_key() -> str:
    """Zora API key from the env ONLY (never hardcoded, never logged) --
    optional, see module docstring point 2 (unauthenticated calls work,
    just at an undocumented stricter rate)."""
    return os.environ.get("ZORA_API_KEY", "").strip()


@dataclass
class ZoraCoin:
    contract: str  # the coin's own address, always on Base for this module
    name: str | None = None
    symbol: str | None = None
    coin_type: str | None = None  # "CREATOR" | "CONTENT" | "TREND"
    market_cap_usd: float | None = None
    volume24h_usd: float | None = None
    chain_id: int | None = None
    created_at: str | None = None
    creator_address: str | None = None


def _to_float(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _parse_node(node) -> ZoraCoin | None:
    """Parses one ``exploreList.edges[].node`` entry -- see module docstring
    point 4 for where this shape is confirmed."""
    if not isinstance(node, dict):
        return None
    contract = node.get("address")
    if not contract:
        return None
    return ZoraCoin(
        contract=str(contract),
        name=node.get("name"),
        symbol=node.get("symbol"),
        coin_type=node.get("coinType"),
        market_cap_usd=_to_float(node.get("marketCap")),
        volume24h_usd=_to_float(node.get("volume24h")),
        chain_id=node.get("chainId"),
        created_at=node.get("createdAt"),
        creator_address=node.get("creatorAddress"),
    )


class ZoraClient:
    """Recently-created Zora Coins on Base -- single REST path (no on-chain
    fallback needed: unlike Flaunch's 24/07 diligence, the REST endpoint here
    is confirmed live and unauthenticated-accessible right now, so there is
    no outage/no-key scenario to fall back from yet)."""

    def __init__(self) -> None:
        self._consecutive_failures = 0

    def _record_success(self) -> None:
        self._consecutive_failures = 0

    def _record_failure(self, detail: str) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= _FAIL_STREAK_WARN_THRESHOLD:
            logger.warning(
                "zora: %s consecutive failures (last: %s) -- no blocking",
                self._consecutive_failures, detail,
            )
        else:
            logger.info(
                "zora: call failed (%s/%s) -- %s",
                self._consecutive_failures, _FAIL_STREAK_WARN_THRESHOLD, detail,
            )

    async def fetch_recent(self, *, limit: int = 50) -> list[ZoraCoin]:
        params = {"listType": "NEW", "count": min(max(limit, 1), 50)}
        headers = {"Accept": "application/json"}
        key = zora_api_key()
        if key:
            headers["api-key"] = key

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(f"{API_ROOT}/explore", params=params, headers=headers)
        except httpx.TransportError as exc:
            self._record_failure(f"/explore -> {exc}")
            return []

        if response.status_code == 429 or response.status_code >= 500:
            self._record_failure(f"/explore -> HTTP {response.status_code}")
            return []
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            self._record_failure(f"/explore -> HTTP {exc.response.status_code}")
            return []

        self._record_success()
        data = response.json()
        edges = ((data or {}).get("exploreList") or {}).get("edges")
        if not isinstance(edges, list):
            return []
        coins = [c for c in (_parse_node(edge.get("node")) for edge in edges if isinstance(edge, dict)) if c is not None]
        return coins[:limit]


zora_client = ZoraClient()
