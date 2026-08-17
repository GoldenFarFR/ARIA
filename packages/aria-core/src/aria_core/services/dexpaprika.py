"""DexPaprika client (read-only, public, no key) -- last tier of the momentum
OHLCV cascade (26/07, Item #130), inserted between Mobula and the degraded
DexScreener synthesis.

Context: a 3-agent due-diligence workflow (26/07) verified this provider live
before any integration, per the "verify before affirming" doctrine -- never
assumed from marketing docs alone:
- Legitimacy: real company (CoinPaprika, Poland, 2018), DexPaprika itself is a
  young sub-product (launched 2025-03-31). No confirmed incident, but the
  official docs are self-contradictory on the exact free-tier limit (docs say
  200k req/month + 30 req/min, the marketing blog says "no daily rate limits"
  / "no SLA, use at your own risk"). Operator decision (26/07): use it anyway
  as a LAST-RESORT tier only, never primary while GeckoTerminal answers.
- Robustness: sustained rate measured empirically at ~53 req/min over 5m29s
  (303 real requests, 96.4% success, isolated 429s, never two consecutive) --
  a burst without any delay hits a hard wall after ~30 requests, so this
  client throttles proactively rather than relying on retry alone.
- Real defect found and worked around here: a malformed ``start`` date is
  NEVER rejected by the API -- it silently falls back to an arbitrary default
  date (2024-11-01) and returns HTTP 200 with data from a completely
  different window, no warning at all. ``_compute_start`` below always
  builds the date via ``datetime``, never string concatenation -- this is
  the one thing that must never regress.
- ``liquidity_usd`` is unreliable on Uniswap V4 pools (always 0 regardless of
  real volume) -- irrelevant here, this client never reads that field.
- Data quality vs GeckoTerminal: prices match within 0.001-0.35%, volumes
  within 0.03-6.2% (never flagrant) on 6 real Base pools compared.

"Dome" doctrine (identical to mobula.py/coinmarketcap.py):
- 429: exponential backoff, 3 attempts max, then give up without blocking the
  pipeline.
- Timeout / 5xx: 1 retry after 5s, then explicit degradation.
- Missing data is never replaced by a guess.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone

import httpx

from aria_core.services.geckoterminal import OHLCVResult, TrendingPool, TrendingPoolsResult
from aria_core.skills.ta_levels import Candle

logger = logging.getLogger(__name__)

UNAVAILABLE = "donnée DexPaprika indisponible"

BASE_URL = "https://api.dexpaprika.com"

# 26/07 -- proactive throttle, calibrated from the empirical test above rather
# than the self-contradictory documented figure (CLAUDE.md "90% of the real
# capacity, verified" doctrine): the test targeted ~1 req/s (60/min) and
# measured 96.4% success -- a slightly wider margin here (1.2s = 50/min, 94%
# of the tested rate) trades a little throughput for near-100% success in
# sustained production use rather than accepting the ~4% failure rate seen
# in the one-shot test.
_MIN_INTERVAL = 1.2
_last_call_at = 0.0
_throttle_lock = asyncio.Lock()

# Real seconds per DexPaprika-valid interval -- used only to compute a safe
# ``start`` date (never to validate the ``interval`` param itself, DexPaprika
# already rejects an invalid one with a clear 400 + explicit valid-list).
_INTERVAL_SECONDS: dict[str, int] = {
    "1m": 60, "5m": 300, "10m": 600, "15m": 900, "30m": 1800,
    "1h": 3600, "6h": 21600, "12h": 43200, "24h": 86400,
}

# 26/07 -- standard-mode ladder (mirrors ohlcv.py's day->4h->1h spirit with
# DexPaprika's own valid intervals) and the dedicated scalping ladder (mirrors
# mobula.py's 15m->30m, same reasoning: never mix scalping-tuned RSI/golden-
# pocket reads with day-scale candles).
_STANDARD_LADDER: tuple[str, ...] = ("24h", "6h", "1h")
_SCALPING_LADDER: tuple[str, ...] = ("15m", "30m")

_MIN_USEFUL_CANDLES = 20
# Safety margin on the fetch window: request candles going back further than
# a strict `limit * interval` would need, since a real pool never has
# perfectly contiguous data (gaps from illiquid periods) -- the API returns
# whatever exists inside [start, now], not necessarily exactly `limit` rows.
_WINDOW_SAFETY_FACTOR = 2.0
_CANDLES_TO_REQUEST = 120


async def _throttle() -> None:
    global _last_call_at
    async with _throttle_lock:
        elapsed = asyncio.get_event_loop().time() - _last_call_at
        if elapsed < _MIN_INTERVAL:
            await asyncio.sleep(_MIN_INTERVAL - elapsed)
        _last_call_at = asyncio.get_event_loop().time()


def _compute_start(interval: str, limit: int) -> str:
    """Real gap this closes (26/07 diligence): a ``start`` too close to "now"
    silently returns an empty/thin window if the real trading activity
    happened earlier in the requested span -- always built via ``datetime``,
    NEVER string concatenation (the one thing that must never regress, see
    module docstring: a malformed date is never rejected by the API, it just
    silently serves the wrong window)."""
    seconds = _INTERVAL_SECONDS.get(interval, 3600) * limit * _WINDOW_SAFETY_FACTOR
    start_dt = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    return start_dt.strftime("%Y-%m-%d")


_key_marked_invalid = False


def _auth_headers() -> dict[str, str]:
    """04/08 -- optional free-tier key (DEXPAPRIKA_API_KEY), read from the
    environment only, never logged/displayed. Keyless calls remain the
    default (this tier works without one, see module docstring); when
    present, the key is sent exactly as their dashboard shows it: a raw
    Authorization header, no "Bearer " prefix.

    05/08 -- once a configured key has been rejected once (401, see
    ``_get_json``), stop sending it for the rest of the process instead of
    retrying a known-bad key on every call: same anti-pattern already fixed
    for Blockscout on 07/20 (an invalid config must degrade gracefully, not
    silently break a tier that works fine keyless)."""
    if _key_marked_invalid:
        return {}
    key = os.environ.get("DEXPAPRIKA_API_KEY", "").strip()
    return {"Authorization": key} if key else {}


async def _get_json(path: str, *, params: dict) -> tuple[object | None, str | None]:
    """GET with retry on 429/5xx/timeout -- same policy as the rest of the dome."""
    global _key_marked_invalid
    url = f"{BASE_URL}{path}"
    attempt_429 = 0
    timeout_retried = False
    key_fallback_tried = False
    headers = _auth_headers()

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
            logger.warning("dexpaprika: timeout on %s -> %s", url, exc)
            return None, f"{UNAVAILABLE} (timeout, {exc})"

        if response.status_code == 401 and headers and not key_fallback_tried:
            # 05/08 -- a configured key rejected outright (verified against
            # DexPaprika's own docs: this tier requires no auth at all, a 401
            # only ever means "key present but invalid/revoked", never "auth
            # required"). Fall back to keyless immediately rather than
            # burning the whole cascade on a config mistake.
            key_fallback_tried = True
            _key_marked_invalid = True
            logger.warning(
                "dexpaprika: configured API key rejected (401) on %s -- "
                "falling back to keyless access for the rest of this process",
                url,
            )
            headers = {}
            continue

        if response.status_code == 429:
            attempt_429 += 1
            if attempt_429 >= 3:
                logger.warning("dexpaprika: HTTP 429 on %s after %s attempts", url, attempt_429)
                return None, f"{UNAVAILABLE} (rate limit)"
            await asyncio.sleep(0.5 * (2**attempt_429))
            continue

        if response.status_code >= 500:
            if not timeout_retried:
                timeout_retried = True
                await asyncio.sleep(5.0)
                continue
            logger.warning("dexpaprika: HTTP %s on %s", response.status_code, url)
            return None, f"{UNAVAILABLE} (erreur serveur {response.status_code})"

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.warning("dexpaprika: %s", exc)
            return None, f"{UNAVAILABLE} ({exc})"

        return response.json(), None


def _parse_candles(data: object) -> list[Candle]:
    if not isinstance(data, list):
        return []
    candles: list[Candle] = []
    for row in data:
        if not isinstance(row, dict):
            continue
        try:
            time_open = row.get("time_open")
            if not isinstance(time_open, str):
                continue
            ts = int(datetime.fromisoformat(time_open.replace("Z", "+00:00")).timestamp())
            o = float(row.get("open"))
            h = float(row.get("high"))
            low = float(row.get("low"))
            c = float(row.get("close"))
            v = float(row.get("volume") or 0.0)
        except (TypeError, ValueError):
            continue
        candles.append(Candle(ts=ts, open=o, high=h, low=low, close=c, volume=v))
    candles.sort(key=lambda c: c.ts)
    return candles


async def _fetch_one_interval(pool_address: str, network: str, interval: str) -> list[Candle]:
    start = _compute_start(interval, _CANDLES_TO_REQUEST)
    data, error = await _get_json(
        f"/networks/{network}/pools/{pool_address}/ohlcv",
        params={"start": start, "interval": interval, "limit": _CANDLES_TO_REQUEST},
    )
    if error is not None:
        logger.info("dexpaprika: %s/%s (%s) failed -- %s", network, pool_address[:10], interval, error)
        return []
    return _parse_candles(data)


async def get_ohlcv(pool_address: str, *, network: str = "base", mode: str = "standard") -> OHLCVResult:
    """Real OHLCV candles for ``pool_address`` on ``network`` -- last-resort
    tier of the cascade (never primary, see module docstring). Walks a short
    ladder of DexPaprika-valid intervals, same "stop escalating on a real
    error" doctrine as ohlcv.py (Item #121, 26/07): here, a genuinely EMPTY
    list (network call succeeded, just nothing at this interval) is the only
    reason to try the next rung -- a raised/logged error inside
    ``_fetch_one_interval`` already means the whole endpoint is unavailable
    for this pool at any granularity, so there is no next rung worth trying
    in that case either (the ladder is short by design, no partial-result
    bookkeeping needed at this cascade depth).

    ``mode="scalping"`` walks the dedicated 15m->30m ladder (mirrors
    mobula.py) -- default ``"standard"`` walks 24h->6h->1h."""
    ladder = _SCALPING_LADDER if mode == "scalping" else _STANDARD_LADDER
    best: list[Candle] = []
    for interval in ladder:
        candles = await _fetch_one_interval(pool_address, network, interval)
        if not candles:
            continue
        if len(candles) >= _MIN_USEFUL_CANDLES:
            return OHLCVResult(candles=candles, available=True, error=None)
        if len(candles) > len(best):
            best = candles

    if best:
        return OHLCVResult(candles=best, available=True, error=None)
    return OHLCVResult(candles=[], available=False, error=f"{UNAVAILABLE} (aucune bougie)")


async def _resolve_base_token(
    network: str, pool_address: str,
) -> tuple[str, str | None, datetime | None] | None:
    """One extra call to the single-pool detail endpoint -- the ONLY place
    DexPaprika exposes an explicit ``base_token_id``/``quote_token_id`` split
    (verified live 16/08: ``/pools/search`` returns an unordered ``tokens``
    list with no base/quote marker at all, picking either side blindly would
    silently mislabel the wrong leg of the pair as the tracked token). Same
    call also carries ``created_at`` (pool creation timestamp, verified live
    16/08) -- resolved here at zero extra cost rather than a 3rd call.
    Returns ``None`` (never a guess) if the detail call fails or the response
    doesn't carry a usable ``base_token_id``."""
    data, error = await _get_json(f"/networks/{network}/pools/{pool_address}", params={})
    if error is not None or not isinstance(data, dict):
        return None
    base_id = data.get("base_token_id")
    if not isinstance(base_id, str):
        return None
    symbol = None
    for tok in data.get("tokens") or []:
        if isinstance(tok, dict) and tok.get("id") == base_id:
            raw_symbol = tok.get("symbol")
            symbol = raw_symbol if isinstance(raw_symbol, str) and raw_symbol else None
            break
    created_at = None
    raw_created = data.get("created_at")
    if isinstance(raw_created, str) and raw_created:
        try:
            created_at = datetime.fromisoformat(raw_created.replace("Z", "+00:00"))
        except ValueError:
            created_at = None  # never fabricate -- an unparseable date stays None
    return base_id, symbol, created_at


async def get_trending_pools(
    network: str, *, limit: int = 20, min_price_change_5m: float | None = None,
) -> TrendingPoolsResult:
    """Independent discovery source (16/08) -- a SEPARATE provider from
    GeckoTerminal's own ``get_trending_pools``, deliberately used to avoid
    two chains competing for the SAME shared adaptive throttle (16/08 real
    incident: Robinhood Chain discovery was starved of GeckoTerminal's shared
    budget by Solana's own calls in the same loop iteration, not a genuine
    per-chain support gap -- both networks answered fine in isolation).
    DexPaprika's own module-level throttle (``_MIN_INTERVAL``) is completely
    separate, so this never competes with GeckoTerminal callers for budget.

    ``/networks/{network}/pools/search?order_by=price_change_percentage_5m``
    (verified live 16/08 against the real API, including confirming
    ``"robinhood"`` IS a supported network id) is sorted DESCENDING by the
    same 5-minute surge signal the shadow's own ``M5_SURGE_THRESHOLD_PCT``
    gates on -- passing ``min_price_change_5m`` here lets the caller stop
    this function from resolving (via ``_resolve_base_token``, one extra
    network call each) every candidate that would be filtered out anyway by
    the caller's own threshold, keeping the real API cost proportional to
    the pools that actually qualify as a signal, not the full ``limit``
    fetched. ``None`` (default) resolves every fetched pool, unfiltered --
    the generic behavior for a caller with no threshold of its own.

    Only ``m5``/``h1``/``h6``/``h24`` are populated in the returned
    ``TrendingPool.price_change_pct`` (DexPaprika's search response has no
    ``m15``/``m30`` window at all, unlike GeckoTerminal) -- never fabricated,
    simply absent from the dict, same as any other missing-data case in this
    dome. Likewise ``transactions_m15``/``volume_usd_m15`` stay ``None``
    (this endpoint only reports 24h/7d/30d volume, no m15 buy/sell
    breakdown) -- ``reserve_usd`` maps from ``liquidity_usd`` (observed
    ``0``/very small on several genuinely-new Robinhood Chain pools during
    live verification, a known DexPaprika data-quality gap already
    documented for Uniswap V4 elsewhere in this module -- passed through
    as-is, never patched over)."""
    params: dict[str, object] = {
        "limit": limit, "order_by": "price_change_percentage_5m", "sort": "desc",
    }
    data, error = await _get_json(f"/networks/{network}/pools/search", params=params)
    if error is not None:
        return TrendingPoolsResult(available=False, error=error)
    if not isinstance(data, dict):
        return TrendingPoolsResult(available=False, error=UNAVAILABLE)

    pools: list[TrendingPool] = []
    for item in data.get("results") or []:
        if not isinstance(item, dict):
            continue
        pool_address = item.get("id")
        if not isinstance(pool_address, str):
            continue
        m5 = item.get("price_change_percentage_5m")
        m5 = m5 if isinstance(m5, (int, float)) else None
        if min_price_change_5m is not None and (m5 is None or m5 < min_price_change_5m):
            continue

        base = await _resolve_base_token(network, pool_address)
        if base is None:
            continue  # never fabricate a token_address -- skip honestly
        token_address, symbol, pool_created_at = base

        price_usd = item.get("price_usd")
        liquidity_usd = item.get("liquidity_usd")
        price_change_pct: dict[str, float] = {}
        for key, field_name in (
            ("m5", "price_change_percentage_5m"),
            ("h1", "price_change_percentage_1h"),
            ("h6", "price_change_percentage_6h"),
            ("h24", "price_change_percentage_24h"),
        ):
            value = item.get(field_name)
            if isinstance(value, (int, float)):
                price_change_pct[key] = float(value)

        pools.append(TrendingPool(
            pool_address=pool_address,
            token_address=token_address,
            symbol=symbol,
            price_usd=float(price_usd) if isinstance(price_usd, (int, float)) else None,
            price_change_pct=price_change_pct,
            transactions_m15=None,
            volume_usd_m15=None,
            reserve_usd=float(liquidity_usd) if isinstance(liquidity_usd, (int, float)) else None,
            pool_created_at=pool_created_at,
        ))
    return TrendingPoolsResult(pools=pools, available=True, error=None)
