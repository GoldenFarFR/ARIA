"""DexScreener client (read-only, public, no key) -- DEX pairs (Base).

Extracted from ``skills/acp_onchain_scan.py`` (14/07, #157) to be reusable
without duplicating a second DexScreener client: wallet-scoring (#157) now
also uses it, triangulating with GeckoTerminal for pool resolution
(``has_pool``) -- if GeckoTerminal finds no pool for a token but DexScreener
finds one, that's a real signal (a gap between the two sources), not just
an illiquid token. Behavior of the existing `/vc` scan strictly unchanged
(same dataclass, same parsing, `acp_onchain_scan.py` delegates here).

Added along the way (14/07): retry on 429/timeout, absent until now (the
original call never retried a rate limit) -- same dome policy as
``blockscout.py``/``geckoterminal.py``.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from urllib.parse import quote

import httpx

from aria_core.skills.ta_levels import Candle

logger = logging.getLogger(__name__)

BASE_URL = "https://api.dexscreener.com"
WEB_BASE_URL = "https://dexscreener.com"

# 21/07 -- first proactive throttle for this client (there was none --
# only a reactive retry after an already-received 429, see `_get_json`).
# CLAUDE.md "Rate calibrated at 90%" doctrine: only the "60 req/min" figure
# is confirmed VERBATIM in the official doc (docs.dexscreener.com/api/reference,
# for the token-profiles/token-boosts endpoints) -- a "300 req/min" for the
# pairs/tokens/search endpoints circulates across several independent sources
# but couldn't be confirmed word for word despite 3 attempts to fetch the
# doc directly. This module shares A SINGLE entry point (`_get_json`) for
# all endpoints -- calibrated on the LOWEST confirmed figure (60/min) rather
# than risk exceeding it on the token-profiles/token-boosts endpoints with a
# throttle designed for the unconfirmed "300/min". 90% of 60/min = 54/min =
# 1.111s. An empirical test (25 back-to-back requests on token-pairs, no
# error in 1.1s) did not contradict this caution -- it just showed that the
# real cap for the pairs/tokens endpoints is probably higher, without
# confirming it precisely.
_MIN_INTERVAL = 1.111
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

_SOCIAL_LABELS = {
    "twitter": "X (Twitter)",
    "x": "X (Twitter)",
    "telegram": "Telegram",
    "discord": "Discord",
    "github": "GitHub",
    "reddit": "Reddit",
    "farcaster": "Farcaster",
}


@dataclass
class PairSnapshot:
    pair_address: str = ""
    dex_id: str = ""
    liquidity_usd: float = 0.0
    volume_24h_usd: float = 0.0
    price_usd: float = 0.0
    price_change_24h: float = 0.0
    price_change_h6: float = 0.0
    price_change_h1: float = 0.0
    price_change_m5: float = 0.0
    buys_24h: int = 0
    sells_24h: int = 0
    # 07/23 -- liquidity-rotation signal: DexScreener already returns these in
    # the SAME response as the h24 fields above (txns/volume broken down by
    # h1/h6/h24) -- previously parsed only at h24, the shorter windows were
    # silently discarded. Exposed here at zero extra network cost so
    # liquidity_rotation.py can measure whether capital is rotating INTO this
    # token right now (a fresh buy-pressure acceleration), the low-info-token
    # edge the operator described (no fundamentals to judge, but the flow is
    # fully on-chain and readable).
    volume_h1_usd: float = 0.0
    volume_h6_usd: float = 0.0
    buys_h1: int = 0
    sells_h1: int = 0
    buys_h6: int = 0
    sells_h6: int = 0
    pair_created_at: int | None = None
    base_address: str = ""  # base token address (#194) -- to correlate a batch
    base_symbol: str = ""
    quote_symbol: str = ""
    project_links: list[dict] = field(default_factory=list)


def _extract_project_links(raw: dict) -> list[dict]:
    """Official links declared by the project (DexScreener `info.websites`/`socials`).

    No estimation: only what DexScreener actually returns, and only http(s)
    URLs (scheme allowlist -- defense in depth, the data comes from an
    untrusted third party and will be revalidated anyway before any
    clickable HTML rendering).
    """
    info = raw.get("info")
    if not isinstance(info, dict):
        return []

    links: list[dict] = []
    for site in info.get("websites") or []:
        if not isinstance(site, dict):
            continue
        url = str(site.get("url") or "").strip()
        if url.lower().startswith(("http://", "https://")):
            links.append({"label": str(site.get("label") or "Site officiel"), "url": url})

    for social in info.get("socials") or []:
        if not isinstance(social, dict):
            continue
        url = str(social.get("url") or "").strip()
        if not url.lower().startswith(("http://", "https://")):
            continue
        kind = str(social.get("type") or "").strip().lower()
        links.append({"label": _SOCIAL_LABELS.get(kind, kind.capitalize() or "Lien"), "url": url})

    return links


def _parse_pair(raw: dict) -> PairSnapshot:
    liq = raw.get("liquidity") or {}
    vol = raw.get("volume") or {}
    txns = raw.get("txns") or {}
    h24 = txns.get("h24") if isinstance(txns, dict) else {}
    h6 = txns.get("h6") if isinstance(txns, dict) else {}
    h1 = txns.get("h1") if isinstance(txns, dict) else {}
    base = raw.get("baseToken") or {}
    quote = raw.get("quoteToken") or {}
    change = raw.get("priceChange")
    change = change if isinstance(change, dict) else {}
    return PairSnapshot(
        pair_address=str(raw.get("pairAddress") or ""),
        dex_id=str(raw.get("dexId") or ""),
        liquidity_usd=float(liq.get("usd") or 0),
        volume_24h_usd=float(vol.get("h24") or 0),
        price_usd=float(raw.get("priceUsd") or 0),
        price_change_24h=float(change.get("h24") or 0),
        price_change_h6=float(change.get("h6") or 0),
        price_change_h1=float(change.get("h1") or 0),
        price_change_m5=float(change.get("m5") or 0),
        buys_24h=int(h24.get("buys") or 0) if isinstance(h24, dict) else 0,
        sells_24h=int(h24.get("sells") or 0) if isinstance(h24, dict) else 0,
        volume_h1_usd=float(vol.get("h1") or 0),
        volume_h6_usd=float(vol.get("h6") or 0),
        buys_h1=int(h1.get("buys") or 0) if isinstance(h1, dict) else 0,
        sells_h1=int(h1.get("sells") or 0) if isinstance(h1, dict) else 0,
        buys_h6=int(h6.get("buys") or 0) if isinstance(h6, dict) else 0,
        sells_h6=int(h6.get("sells") or 0) if isinstance(h6, dict) else 0,
        pair_created_at=int(raw.get("pairCreatedAt") or 0) or None,
        base_address=str(base.get("address") or "").lower(),
        base_symbol=str(base.get("symbol") or ""),
        quote_symbol=str(quote.get("symbol") or ""),
        project_links=_extract_project_links(raw),
    )


async def _get_json(url: str) -> tuple[object | None, str | None]:
    """GET with retry on 429/5xx/timeout -- same policy as blockscout.py/
    geckoterminal.py. The original implementation (in acp_onchain_scan.py)
    had no retry; an isolated 429 gave up outright with no log."""
    attempt_429 = 0
    timeout_retried = False

    while True:
        await _throttle()
        try:
            async with httpx.AsyncClient(timeout=18.0) as client:
                response = await client.get(url)
        except httpx.TransportError as exc:
            if not timeout_retried:
                timeout_retried = True
                await asyncio.sleep(5.0)
                continue
            logger.warning("dexscreener: timeout on %s -> %s", url, exc)
            return None, f"dexscreener unavailable (timeout, {exc})"

        if response.status_code == 429:
            attempt_429 += 1
            if attempt_429 >= 3:
                logger.warning("dexscreener: HTTP 429 on %s after %s attempts", url, attempt_429)
                return None, "dexscreener unavailable (rate limit)"
            await asyncio.sleep(0.5 * (2**attempt_429))
            continue

        if response.status_code >= 500:
            if not timeout_retried:
                timeout_retried = True
                await asyncio.sleep(5.0)
                continue
            logger.warning("dexscreener: HTTP %s on %s", response.status_code, url)
            return None, f"dexscreener unavailable (server error {response.status_code})"

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.warning("dexscreener: %s", exc)
            return None, f"dexscreener unavailable ({exc})"

        return response.json(), None


async def fetch_token_pairs(contract: str, *, chain: str = "base") -> list[PairSnapshot]:
    """Known DEX pairs for ``contract`` on ``chain``. Empty list if no pair
    OR if the call fails (never a bubbling exception -- graceful degradation,
    same policy as the existing `/vc` scan)."""
    url = f"{BASE_URL}/token-pairs/v1/{chain}/{contract}"
    data, error = await _get_json(url)
    if error is not None:
        logger.warning("dexscreener: token-pairs %s -> %s", contract[:10], error)
        return []
    if not isinstance(data, list):
        return []
    return [_parse_pair(row) for row in data if isinstance(row, dict)]


def token_url(contract: str, *, chain: str = "base") -> str:
    """Public DexScreener URL (web page, not the API) for ``contract`` on ``chain`` --
    17/07, operator request: every ARIA position must be linked to the real chart.
    Pure construction (no network call): DexScreener uses the same chain
    identifier in its web URLs as in its API (``chain`` as already stored on a
    position), no mapping table to maintain. "Token address" form (not a
    specific pair) -- DexScreener itself picks the most liquid pair to
    display, consistent with ``_best_pair`` on the scan side (highest
    liquidity)."""
    return f"{WEB_BASE_URL}/{(chain or 'base').strip().lower()}/{(contract or '').strip().lower()}"


async def has_any_pair(contract: str, *, chain: str = "base") -> bool | None:
    """Triangulation (#157, 14/07): ``True``/``False`` if DexScreener responded
    normally (at least one pair found or not), ``None`` if the call
    failed -- never confuse "no pair" with "couldn't verify"."""
    url = f"{BASE_URL}/token-pairs/v1/{chain}/{contract}"
    data, error = await _get_json(url)
    if error is not None:
        return None
    if not isinstance(data, list):
        return None
    return len(data) > 0


async def search_pairs(query: str) -> list[PairSnapshot]:
    """Free-text DexScreener search (``/latest/dex/search``, #194, 15/07) --
    covers ALL indexed chains (not one endpoint per chain), multi-chain
    sourcing source verified live (curl, HTTP 200) before building. Same
    pair shape as ``token-pairs/v1`` (``_parse_pair`` reused as-is).
    Empty list if no result OR if the call fails -- never an exception."""
    url = f"{BASE_URL}/latest/dex/search?q={quote(query)}"
    data, error = await _get_json(url)
    if error is not None:
        logger.warning("dexscreener: search '%s' -> %s", query[:30], error)
        return []
    if not isinstance(data, dict):
        return []
    pairs = data.get("pairs")
    if not isinstance(pairs, list):
        return []
    return [_parse_pair(row) for row in pairs if isinstance(row, dict)]


@dataclass
class TokenListing:
    """DexScreener "boost" or "profile" entry (#194) -- discovery metadata
    WITHOUT price/liquidity data (unlike ``PairSnapshot``): just enough to
    identify a contract + chain to then pass to the real decision pipeline
    (honeypot + TA + R/R), never used alone as a buy signal."""

    chain_id: str = ""
    token_address: str = ""
    description: str = ""
    links: list[dict] = field(default_factory=list)


def parse_listing(raw: dict) -> TokenListing:
    """Public render (#196, was ``_parse_listing``): reused as-is by
    ``aria_core.momentum_websocket`` -- DexScreener WebSocket frames (verified
    live 16/07) carry EXACTLY the same per-element shape as the REST response
    (``chainId``/``tokenAddress``/``description``/``links``), no duplicated
    parsing."""
    links: list[dict] = []
    for link in raw.get("links") or []:
        if not isinstance(link, dict):
            continue
        url = str(link.get("url") or "").strip()
        if not url.lower().startswith(("http://", "https://")):
            continue
        kind = str(link.get("type") or "").strip().lower()
        label = str(link.get("label") or "") or _SOCIAL_LABELS.get(kind, kind.capitalize() or "Lien")
        links.append({"label": label, "url": url})
    return TokenListing(
        chain_id=str(raw.get("chainId") or ""),
        token_address=str(raw.get("tokenAddress") or ""),
        description=str(raw.get("description") or ""),
        links=links,
    )


async def _fetch_listings(path: str) -> list[TokenListing]:
    data, error = await _get_json(f"{BASE_URL}{path}")
    if error is not None:
        logger.warning("dexscreener: %s -> %s", path, error)
        return []
    if not isinstance(data, list):
        return []
    return [parse_listing(row) for row in data if isinstance(row, dict)]


async def token_boosts_top() -> list[TokenListing]:
    """Tokens currently the most "boosted" (paid DexScreener promotion,
    #194) -- signal "someone is investing for THIS token's visibility right
    now", never a buy signal on its own (sourcing bonus)."""
    return await _fetch_listings("/token-boosts/top/v1")


async def token_boosts_latest() -> list[TokenListing]:
    """Most RECENT boosts (#194) -- favors freshness ("signals that are just
    starting to form") over an already well-advanced ranking."""
    return await _fetch_listings("/token-boosts/latest/v1")


async def token_profiles_latest() -> list[TokenListing]:
    """Most recently CREATED project profiles (#194) -- sourcing fresh tokens
    with filled-in metadata, independent of paid boosts."""
    return await _fetch_listings("/token-profiles/latest/v1")


async def token_profiles_recent_updates() -> list[TokenListing]:
    """Most recently UPDATED project profiles (#194, distinct from
    ``token_profiles_latest`` which covers creations) -- captures a project
    that just touched up its metadata, a recent-activity signal."""
    return await _fetch_listings("/token-profiles/recent-updates/v1")


async def fetch_tokens_batch(addresses: list[str], *, chain: str = "base") -> list[PairSnapshot]:
    """``/tokens/v1/{chainId}/{tokenAddresses}`` (#194, official OpenAPI spec
    verified -- docs/aria-learning-inbox/2026-07-15-dexscreener-openapi-spec-verifiee.yaml):
    up to 30 comma-separated addresses in A SINGLE call (300 req/min), much
    more efficient than N individual ``token-pairs/v1`` calls to pre-filter
    a batch of sourced candidates (liquidity) before the full decision
    pipeline. Addresses beyond 30 silently truncated (documented API limit,
    never a call that would silently fail on an oversized batch)."""
    addrs = [a.strip() for a in addresses if a and a.strip()][:30]
    if not addrs:
        return []
    url = f"{BASE_URL}/tokens/v1/{chain}/{','.join(addrs)}"
    data, error = await _get_json(url)
    if error is not None:
        logger.warning("dexscreener: tokens/v1 batch (%s, %d addresses) -> %s", chain, len(addrs), error)
        return []
    if not isinstance(data, list):
        return []
    return [_parse_pair(row) for row in data if isinstance(row, dict)]


@dataclass
class MetaTrend:
    """DexScreener narrative/meta trend (#194, ``/metas/*``) -- e.g. "AI",
    groups several tokens under one theme. CONTEXT signal (a hot narrative
    can carry several candidates at once), never an isolated buy signal."""

    slug: str = ""
    name: str = ""
    description: str = ""
    market_cap: float = 0.0
    liquidity: float = 0.0
    volume: float = 0.0
    token_count: int = 0
    market_cap_change_24h: float = 0.0


def _parse_meta(raw: dict) -> MetaTrend:
    change = raw.get("marketCapChange")
    change_24h = float(change.get("h24") or 0) if isinstance(change, dict) else 0.0
    return MetaTrend(
        slug=str(raw.get("slug") or ""),
        name=str(raw.get("name") or ""),
        description=str(raw.get("description") or ""),
        market_cap=float(raw.get("marketCap") or 0),
        liquidity=float(raw.get("liquidity") or 0),
        volume=float(raw.get("volume") or 0),
        token_count=int(raw.get("tokenCount") or 0),
        market_cap_change_24h=change_24h,
    )


async def metas_trending() -> list[MetaTrend]:
    """Trending narratives (#194, ``/metas/trending/v1``)."""
    data, error = await _get_json(f"{BASE_URL}/metas/trending/v1")
    if error is not None:
        logger.warning("dexscreener: metas/trending -> %s", error)
        return []
    if not isinstance(data, list):
        return []
    return [_parse_meta(row) for row in data if isinstance(row, dict)]


async def meta_by_slug(slug: str) -> tuple[MetaTrend | None, list[PairSnapshot]]:
    """Detail of a narrative + its pairs (#194, ``/metas/meta/v1/{slug}``).
    ``(None, [])`` if unavailable -- never an invented pair."""
    data, error = await _get_json(f"{BASE_URL}/metas/meta/v1/{quote(slug)}")
    if error is not None:
        logger.warning("dexscreener: metas/meta %s -> %s", slug, error)
        return None, []
    if not isinstance(data, dict):
        return None, []
    meta = _parse_meta(data)
    pairs_raw = data.get("pairs")
    pairs = (
        [_parse_pair(row) for row in pairs_raw if isinstance(row, dict)]
        if isinstance(pairs_raw, list)
        else []
    )
    return meta, pairs


# ---------------------------------------------------------------------------
# Degraded candle synthesis (16/07, OHLCV cascade #194 -- explicit operator
# request: "I want everything wired up even if they do the same thing, I
# want a highway not a back road" / "wire them all up, I want a complete
# web with dexscreener and dune").
#
# DexScreener EXPOSES NO public OHLCV endpoint (verified in this file --
# only pair snapshots + aggregated variation windows m5/h1/h6/h24, never a
# real candle series). This is therefore NOT a third OHLCV provider on par
# with GeckoTerminal/CoinMarketCap -- it's an APPROXIMATE RECONSTRUCTION
# from what's already on hand (``PairSnapshot`` already fetched by
# ``evaluate_momentum_entry`` for the current price, NO extra network
# call): 5 price points (now, -5m, -1h, -6h, -24h) derived backwards from
# the current price via the % variations. Each "candle" is a simple
# degenerate OHLC point (open=high=low=close, volume=0) -- never a real
# candlestick with real wicks.
#
# HONEST SCOPE: enough for a rough trend bias (EMA/MACD on 5 points remains
# computable but not very meaningful), practically useless for
# ``entry_signals.detect_entry`` (golden pocket + RSI divergence requires
# real price history, not 5 synthetic points) -- HOLD will remain the most
# likely outcome even with this synthesis, which is the expected honest
# behavior (never a fabricated R/R on such poor data). Used ONLY as a last
# resort after GeckoTerminal AND CoinMarketCap have failed -- free and
# instant, so no cost to try before Dune (SQL executor, slow, costs
# credits).
def synthesize_candles_from_pair(pair: PairSnapshot) -> list[Candle]:
    """Degraded reconstruction (see comment above) -- never a substitute for
    real OHLCV, only a free last resort."""
    if not pair or not pair.price_usd or pair.price_usd <= 0:
        return []

    now_price = pair.price_usd
    windows = (
        ("h24", pair.price_change_24h),
        ("h6", pair.price_change_h6),
        ("h1", pair.price_change_h1),
        ("m5", pair.price_change_m5),
    )

    points: list[tuple[int, float]] = []
    for offset_seconds, (_label, pct_change) in zip((86_400, 21_600, 3_600, 300), windows):
        try:
            past_price = now_price / (1.0 + (pct_change / 100.0))
        except ZeroDivisionError:
            continue
        if past_price <= 0:
            continue
        points.append((-offset_seconds, past_price))

    points.append((0, now_price))
    points.sort(key=lambda p: p[0])

    return [
        Candle(ts=ts, open=price, high=price, low=price, close=price, volume=0.0)
        for ts, price in points
    ]
