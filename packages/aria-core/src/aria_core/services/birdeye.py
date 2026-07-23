"""Birdeye client (read-only) -- bulk discovery of Base tokens (21/07),
answering a real bottleneck found the same day: ``discover_momentum_
candidates()`` only finds ~18 raw candidates per cycle (DexScreener
boosts/profiles + GeckoTerminal pools sources, never an exhaustive filtered
search), while an equivalent manual filter on DexScreener (liquidity>=50k$,
volume>=500$) finds ~380-520 on Base. DexScreener has NO bulk filtered search
API (confirmed several times this month) -- Birdeye does: ``/defi/v3/token/list``
(verified live on 21/07, free tier).

Verified under real conditions (21/07): 520 Base tokens retrieved in 6 paginated
calls (liquidity>=50k$, 24h volume>=500$, same thresholds as the momentum
pipeline -- ``momentum_entry._MIN_LIQUIDITY_USD``/``_MIN_VOLUME_24H_USD``, never
hardcoded a second time here, passed in by the caller).

CU cost verified (docs.birdeye.so/docs/compute-unit-cost): 75 CU/call on this
endpoint. Free "Standard" tier (30,000 CU/month, 1 req/s = 60/min, confirmed on
the real dashboard) -- a full scan (~6 calls) costs ~450 CU, i.e. ~66 full
scans/month sustainable FOR FREE (~2/day) without ever touching the paid tier.
12h process-local cache (2x/day) in ``momentum_entry.py`` so this endpoint is
never called on every heartbeat cycle (15 min, 96x/day -- would blow past the
free budget by several orders of magnitude without this cache).

"Dome" doctrine (identical to goplus.py/webacy.py/mobula.py):
- Missing key -- immediate ``available=False``, no network call, never
  blocking the pipeline (same degradation family as the rest of the discovery
  sources, ``discover_momentum_candidates`` continues with the other sources).
- 429/5xx/timeout -- explicit degradation, never an exception bubbling up.
- Capped pagination (``_MAX_PAGES``) -- anti-infinite-loop protection
  independent of what the API returns, same pattern as
  ``blockscout_x402._MAX_PAGES_PER_EXTRACTION``.

Throttle calibrated to 90% of the confirmed free tier (1 req/s, real dashboard
21/07) -- ``_MIN_INTERVAL_S = 1.11`` (same "90% of the real capacity, never
guessed" doctrine as the rest of the project, see CLAUDE.md)."""
from __future__ import annotations

import asyncio
import logging
import os
import time

import httpx

logger = logging.getLogger(__name__)

UNAVAILABLE = "donnée Birdeye indisponible"

BASE_URL = "https://public-api.birdeye.so"

_MIN_INTERVAL_S = 1.11
_MAX_PAGES = 10
_PAGE_LIMIT = 100

_last_call_at = 0.0
_lock = asyncio.Lock()


def birdeye_api_key() -> str | None:
    return os.environ.get("BIRDEYE_API_KEY", "").strip() or None


def birdeye_available() -> bool:
    return bool(birdeye_api_key())


async def _throttle() -> None:
    global _last_call_at
    async with _lock:
        now = time.monotonic()
        wait = _MIN_INTERVAL_S - (now - _last_call_at)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_call_at = time.monotonic()


async def discover_base_tokens_bulk(
    *, min_liquidity_usd: float = 50_000.0, min_volume_24h_usd: float = 500.0,
) -> list[str]:
    """Paginated list of Base token addresses whose liquidity AND 24h volume
    exceed the given thresholds (same thresholds as the momentum pipeline,
    never hardcoded here -- the caller passes in the real constants).
    Degrades to an empty list on any failure -- never an exception, never a
    made-up candidate. Capped at ``_MAX_PAGES`` * ``_PAGE_LIMIT`` = 1000 tokens
    max per call (anti-infinite-loop safeguard, independent of what Birdeye
    actually returns)."""
    api_key = birdeye_api_key()
    if not api_key:
        return []

    headers = {"X-API-KEY": api_key, "x-chain": "base", "accept": "application/json"}
    url = f"{BASE_URL}/defi/v3/token/list"
    contracts: list[str] = []

    async with httpx.AsyncClient(timeout=20.0) as client:
        for page in range(_MAX_PAGES):
            await _throttle()
            params = {
                "sort_by": "liquidity", "sort_type": "desc",
                "min_liquidity": min_liquidity_usd, "min_volume_24h_usd": min_volume_24h_usd,
                "limit": _PAGE_LIMIT, "offset": page * _PAGE_LIMIT,
            }
            try:
                resp = await client.get(url, params=params, headers=headers)
            except Exception as exc:  # noqa: BLE001 -- network failure, never blocking
                logger.info("birdeye: token/list failed on page %s (%s)", page, exc)
                break
            if resp.status_code != 200:
                logger.info(
                    "birdeye: token/list HTTP %s on page %s -- %s",
                    resp.status_code, page, resp.text[:200],
                )
                break
            try:
                body = resp.json()
            except Exception:  # noqa: BLE001 -- unreadable response, never an exception bubbling up
                break
            data = body.get("data") if isinstance(body, dict) else None
            # 22/07 -- real bug found under real conditions: the key is "items",
            # NOT "tokens" (the "tokens" field never exists in the real response,
            # verified live -- this module was silently returning [] since the
            # 21/07 deployment, never an exception, never detected before a
            # manual test). "tokens" fallback kept out of caution (no cost,
            # never seen in the real response to date).
            items = (data or {}).get("items") if isinstance(data, dict) else None
            if not items:
                items = (data or {}).get("tokens") if isinstance(data, dict) else None
            if not isinstance(items, list) or not items:
                break
            for item in items:
                if not isinstance(item, dict):
                    continue
                addr = item.get("address")
                if addr:
                    contracts.append(str(addr))
            if len(items) < _PAGE_LIMIT:
                break

    return contracts
