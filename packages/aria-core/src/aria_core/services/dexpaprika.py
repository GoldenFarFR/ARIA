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
import time
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

# system_issues #125b (18/08, multi-agent pipeline audit, "low" finding):
# unlike GeckoTerminal/GoPlus/Blockscout/DexScreener, this client had per-call
# 429/5xx retry (the dome, above) but no memory ACROSS calls of a sustained
# outage -- every caller kept paying the full retry latency on every single
# request even during a confirmed multi-minute provider outage. Built here,
# at the ONE choke point every public function in this module funnels
# through (``_get_json``), rather than in momentum_entry.py's own private
# per-cascade ``_provider_in_cooldown`` (#95, 19/07) -- that one already
# protects the OHLCV cascade's own use of this provider, but shadow modules
# and the watchlist collector call this module's functions directly,
# bypassing that cascade-local mechanism entirely. Same threshold/duration as
# the proven #95 pattern (3 consecutive failures -> 180s cooldown), process-
# local state (best-effort latency optimization, never a correctness
# concern -- losing it on a restart just means retrying a provider that may
# have had time to recover).
_CIRCUIT_COOLDOWN_SECONDS = 180.0
_CIRCUIT_FAIL_THRESHOLD = 3
_circuit_fail_count = 0
_circuit_cooldown_until = 0.0


def _circuit_open() -> bool:
    return time.monotonic() < _circuit_cooldown_until


def _record_circuit_outcome(*, ok: bool) -> None:
    global _circuit_fail_count, _circuit_cooldown_until
    if ok:
        was_open = _circuit_fail_count >= _CIRCUIT_FAIL_THRESHOLD
        _circuit_fail_count = 0
        _circuit_cooldown_until = 0.0
        if was_open:
            from aria_core import circuit_breaker_log

            circuit_breaker_log.record_transition_nowait(
                "ohlcv_dexpaprika", "closed", consecutive_failures=0, cooldown_seconds=0.0,
            )
        return
    _circuit_fail_count += 1
    if _circuit_fail_count >= _CIRCUIT_FAIL_THRESHOLD:
        _circuit_cooldown_until = time.monotonic() + _CIRCUIT_COOLDOWN_SECONDS
        from aria_core import circuit_breaker_log

        circuit_breaker_log.record_transition_nowait(
            "ohlcv_dexpaprika", "open",
            consecutive_failures=_circuit_fail_count, cooldown_seconds=_CIRCUIT_COOLDOWN_SECONDS,
        )


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
    silently serves the wrong window).

    17/08 -- real bug found live (a Solana pool with confirmed, active
    recent trading -- verified against GeckoTerminal already tracking it --
    returned ZERO candles through this function while the raw endpoint
    returned real ones with real wicks): the DATE-ONLY format below
    (``%Y-%m-%d``, midnight, no time-of-day) introduced up to ~24h of slack
    on top of the intended lookback window. Combined with the safety
    factor, the computed ``[start, start+window]`` range could land
    entirely in the past without ever reaching "now" -- silently missing
    all recent activity depending on what hour of the day the call
    happened to run at. Isolated by testing date-only vs full-ISO at the
    SAME lookback distance: only the date-only form failed. Fixed by
    keeping full second-level precision -- the exact anti-regression this
    function's own docstring already asked for, just not carried all the
    way through to the return value."""
    seconds = _INTERVAL_SECONDS.get(interval, 3600) * limit * _WINDOW_SAFETY_FACTOR
    start_dt = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    return start_dt.strftime("%Y-%m-%dT%H:%M:%SZ")


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
    """GET with retry on 429/5xx/timeout -- same policy as the rest of the dome.
    Wrapped by the module-level circuit breaker (see its own comment above):
    a confirmed sustained outage short-circuits immediately, before even the
    proactive throttle, rather than paying the full retry latency again."""
    global _key_marked_invalid
    if _circuit_open():
        return None, f"{UNAVAILABLE} (coupe-circuit ouvert, pannes consécutives récentes)"

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
            _record_circuit_outcome(ok=False)
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
                _record_circuit_outcome(ok=False)
                return None, f"{UNAVAILABLE} (rate limit)"
            await asyncio.sleep(0.5 * (2**attempt_429))
            continue

        if response.status_code >= 500:
            if not timeout_retried:
                timeout_retried = True
                await asyncio.sleep(5.0)
                continue
            logger.warning("dexpaprika: HTTP %s on %s", response.status_code, url)
            _record_circuit_outcome(ok=False)
            return None, f"{UNAVAILABLE} (erreur serveur {response.status_code})"

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.warning("dexpaprika: %s", exc)
            # 4xx other than 401/429 (malformed request, not found...) is
            # never a provider-health signal -- same doctrine as momentum_
            # entry.py's own circuit breaker (only an outage/rate-limit/
            # network failure counts as a "failure").
            return None, f"{UNAVAILABLE} ({exc})"

        _record_circuit_outcome(ok=True)
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
    # 17/08, real bug caught live by the operator on solana_support_bounce_
    # shadow (a token's support-distance math used candles ending ~10h in
    # the past on an actively-traded pool). Root cause verified live via
    # curl: `start` is computed _WINDOW_SAFETY_FACTOR times further back
    # than `_CANDLES_TO_REQUEST` candles actually need (by design, to
    # tolerate gaps on illiquid pools -- see _WINDOW_SAFETY_FACTOR's own
    # comment) but the `limit` sent to the API was never widened to match.
    # On a DENSE pool (near-zero gaps, exactly the kind of active token this
    # project cares about), the API fills the requested `limit` starting
    # from `start` and simply stops there -- confirmed live: limit=120
    # returned candles ending 03:35->13:30 UTC (10h short of "now", 23:34
    # UTC); limit=240 (== _CANDLES_TO_REQUEST * _WINDOW_SAFETY_FACTOR)
    # returned candles reaching 23:30 UTC, 4min of "now" (the normal
    # candle-closing lag). Requesting the full safety-widened count closes
    # this for every interval, not just 5m -- the same ratio mismatch
    # applies structurally regardless of interval.
    request_limit = int(_CANDLES_TO_REQUEST * _WINDOW_SAFETY_FACTOR)
    data, error = await _get_json(
        f"/networks/{network}/pools/{pool_address}/ohlcv",
        params={"start": start, "interval": interval, "limit": request_limit},
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


async def get_pool_reserve_usd(pool_address: str, *, network: str = "solana") -> float | None:
    """18/08, real-liquidity backfill for the shadow modules' realistic-exit
    simulation: a THIRD independent reserve source (after DexScreener's own
    figure and a GeckoTerminal backfill), on a rate-limit budget entirely
    separate from both -- confirmed live to matter (SadDog, real GeckoTerminal
    429s at the exact moment its shadow exit-check ran caused a false
    ``PIEGEE``/stranded classification despite $13K/24h real volume). Same
    single-pool detail endpoint ``_resolve_base_token`` already calls
    (``/networks/{network}/pools/{pool_address}``), which carries its own
    ``liquidity_usd`` field (verified live 18/08) -- a second, standalone call
    rather than threading the value through ``_resolve_base_token`` (called
    from a different, earlier point in the pipeline, and this is deliberately
    a RARE fallback path, not a hot one). Returns ``None`` -- never a
    fabricated number -- on any failure or a missing/non-numeric field."""
    data, error = await _get_json(f"/networks/{network}/pools/{pool_address}", params={})
    if error is not None or not isinstance(data, dict):
        return None
    liquidity_usd = data.get("liquidity_usd")
    return float(liquidity_usd) if isinstance(liquidity_usd, (int, float)) else None


async def get_trending_pools(
    network: str, *, limit: int = 20, min_price_change_5m: float | None = None,
    order_by: str = "price_change_percentage_5m", min_order_value: float | None = None,
    min_liquidity_usd: float | None = None, min_price_change_1h: float | None = None,
    max_pool_age_minutes: float | None = None, min_pool_age_minutes: float | None = None,
    max_pages: int = 1,
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
    as-is, never patched over).

    17/08 -- ``order_by``/``min_order_value`` generalize the sort/pre-filter
    beyond m5 (added for a support-bounce pocket that needs h1-sorted
    candidates, e.g. ``order_by="price_change_percentage_1h"``). Defaults
    preserve the exact original m5-sorted behavior for every existing
    caller -- ``min_price_change_5m`` still gates on m5 SPECIFICALLY
    regardless of ``order_by`` (a caller sorting by h1 can still also want an
    m5 floor), while ``min_order_value`` gates on whichever field
    ``order_by`` names, same resolve-skipping cost-control doctrine as
    ``min_price_change_5m`` already documented above.

    17/08, later same day -- REAL server-side filters, verified live against
    the documented ``/pools/search`` params (``liquidity_usd_min``,
    ``price_change_percentage_1h_min``, ``created_before``/``created_after``,
    confirmed via ``docs.dexpaprika.com/tutorials/pool-filtering`` and a live
    curl test). Operator's own instinct was right ("paprika arrive a nous
    renvoyer... sur un plateau") -- earlier that day this function only
    filtered CLIENT-side after fetching, wasting a ``_resolve_base_token``
    call on candidates that could have been excluded by the API itself.
    ``min_liquidity_usd``/``min_price_change_1h`` map straight to their real
    param names; ``min_pool_age_minutes``/``max_pool_age_minutes`` compute
    the UNIX-timestamp ``created_before``/``created_after`` cutoffs from
    "now" (a pool at least X minutes old was created before now-X; a pool at
    most Y minutes old was created after now-Y) -- the API does the age math
    itself, no per-candidate age check needed downstream anymore for callers
    that use this. All four default to ``None`` (omitted from the request),
    so every pre-existing caller's exact behavior is unchanged.

    ``max_pages`` (18/08, operator-directed): the real API supports cursor
    pagination (``next_cursor``/``has_next_page``, confirmed live against
    ``docs.dexpaprika.com/tutorials/pool-filtering`` -- the old
    ``page``/``offset`` params are gone, cursor-only now) -- a single call
    only ever returned the first ``limit`` results, silently missing every
    pool beyond that even when it also matched the filters. Confirmed live
    18/08 on Solana with the support-bounce pocket's own filters (liquidity
    >=5000$, h1>-5%, age>=70min): still ``has_next_page=True`` after 7
    consecutive pages (350+ matching pools, likely far more), nowhere near
    exhausted. Default ``max_pages=1`` preserves the EXACT original
    single-page behavior for every pre-existing caller (Base/Robinhood
    momentum pipeline) -- opt-in only. Each pool costs one extra
    ``_resolve_base_token`` call regardless of which page it came from, so
    raising this multiplies real API load linearly -- deliberately left to
    the caller to size against DexPaprika's real (already fragile some
    nights) throughput, never a large default here."""
    params: dict[str, object] = {
        "limit": limit, "order_by": order_by, "sort": "desc",
    }
    if min_liquidity_usd is not None:
        params["liquidity_usd_min"] = min_liquidity_usd
    if min_price_change_1h is not None:
        params["price_change_percentage_1h_min"] = min_price_change_1h
    now_ts = int(datetime.now(timezone.utc).timestamp())
    if min_pool_age_minutes is not None:
        params["created_before"] = now_ts - int(min_pool_age_minutes * 60)
    if max_pool_age_minutes is not None:
        params["created_after"] = now_ts - int(max_pool_age_minutes * 60)

    raw_items: list[dict] = []
    cursor: str | None = None
    for _page in range(max(1, max_pages)):
        page_params = dict(params)
        if cursor:
            page_params["cursor"] = cursor
        data, error = await _get_json(f"/networks/{network}/pools/search", params=page_params)
        if error is not None:
            if raw_items:
                break  # keep whatever earlier pages already yielded
            return TrendingPoolsResult(available=False, error=error)
        if not isinstance(data, dict):
            if raw_items:
                break
            return TrendingPoolsResult(available=False, error=UNAVAILABLE)
        raw_items.extend(item for item in (data.get("results") or []) if isinstance(item, dict))
        cursor = data.get("next_cursor")
        if not data.get("has_next_page") or not isinstance(cursor, str):
            break

    pools: list[TrendingPool] = []
    for item in raw_items:
        pool_address = item.get("id")
        if not isinstance(pool_address, str):
            continue
        m5 = item.get("price_change_percentage_5m")
        m5 = m5 if isinstance(m5, (int, float)) else None
        if min_price_change_5m is not None and (m5 is None or m5 < min_price_change_5m):
            continue
        order_value = item.get(order_by)
        order_value = order_value if isinstance(order_value, (int, float)) else None
        if min_order_value is not None and (order_value is None or order_value < min_order_value):
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
