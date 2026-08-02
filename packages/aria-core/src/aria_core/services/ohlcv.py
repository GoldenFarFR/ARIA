"""Read-only GeckoTerminal client — OHLCV (candle) series for Base.

Provides the **raw material** of technical analysis: a series of real OHLCV
candles for a DEX pool, which `skills/ta_levels.py` turns into levels
(support / resistance / trend) and `skills/chart_render.py` charts.

GeckoTerminal public tier, OPTIONAL authentication (08/01, real bug found
live -- see `_get_json`'s own comment): if `COINGECKO_DEMO_API_KEY` is set,
attached as the `x-cg-demo-api-key` header on every call, same pattern as
`services/geckoterminal.py` (which already did this correctly since 18/07 --
this module never had). Error policy identical to
`services/coingecko.py` (see AGENTS.md):
- 429: exponential backoff, 3 attempts max, then give up without blocking the pipeline.
- Timeout / endpoint unavailable: 1 retry after 5s, then explicit fallback.
- 400 / 404 (unknown pool, uncovered network): `available=False` + clear message.
- Missing data is never replaced by a guess — the absence is carried by
  `available=False` and `error`, never by a fabricated candle.

The module only depends on `ta_levels.Candle` (pure dataclass, no I/O) to
share the SAME candle structure end to end (no duplication).
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field

import httpx

from aria_core.skills.ta_levels import Candle

logger = logging.getLogger(__name__)

BASE_URL = "https://api.geckoterminal.com/api/v2"

UNAVAILABLE = "OHLCV series unavailable"

_FAIL_STREAK_WARN_THRESHOLD = 3

# GeckoTerminal network for the Base chain (only chain at launch).
DEFAULT_NETWORK = "base"

# Fallback order: we want a daily frame first (macro levels), and if the
# token is too young to have enough daily candles, we fall back to 4h then
# 1h — so a recent token still gets usable levels.
# (GeckoTerminal period, aggregate, limit, reported timeframe label).
_FETCH_LADDER: tuple[tuple[str, int, int, str], ...] = (
    ("day", 1, 120, "1D"),
    ("hour", 4, 180, "4H"),
    ("hour", 1, 240, "1H"),
)

# Below this number of candles, a window is judged too thin for reliable
# levels → we try the next finer timeframe in the ladder.
_MIN_USEFUL_CANDLES = 20

# Item #101 (26/07) -- dedicated sub-hour ladder for the scalping mode
# (15-30min candles, operator-fixed). The standard ladder above never goes
# below 1h -- structurally too coarse for a scalping decision. 15min first
# (the operator's primary choice), 30min as a fallback for a pool too thin
# for 15min candles. Deliberately does NOT fall through to the standard
# ladder's 1D/4H/1H rungs: mixing a scalping RSI/golden-pocket read (period
# 10, tuned for 15-30min noise) with day-scale candles would silently
# corrupt its meaning -- a candidate whose pool is too thin even for 30min
# candles gets an honest `available=False`, never a misleadingly coarse
# fallback (same "never fabricate, never mislead" dome as the rest of this
# module).
_SCALPING_FETCH_LADDER: tuple[tuple[str, int, int, str], ...] = (
    ("minute", 15, 120, "15M"),
    ("minute", 30, 120, "30M"),
)


def _ladder_for_mode(mode: str) -> tuple[tuple[str, int, int, str], ...]:
    """``mode="scalping"`` -> the dedicated sub-hour ladder above; anything
    else (default ``"standard"``) -> the original 1D/4H/1H ladder, unchanged
    behavior for every existing caller."""
    if mode == "scalping":
        return _SCALPING_FETCH_LADDER
    return _FETCH_LADDER


@dataclass
class OHLCVResult:
    """OHLCV series of a pool, or the explicit absence of data.

    ``candles`` is sorted by ascending timestamp. ``timeframe`` indicates
    which rung of the ladder actually provided the data (1D / 4H / 1H).
    """

    pool_address: str
    network: str = DEFAULT_NETWORK
    candles: list[Candle] = field(default_factory=list)
    timeframe: str | None = None
    available: bool = False
    error: str | None = None


def _parse_candles(payload: object) -> list[Candle]:
    """Extracts ``data.attributes.ohlcv_list`` into a sorted ``list[Candle]``.

    Each GeckoTerminal row = ``[ts, open, high, low, close, volume]``. A
    malformed row is ignored (never an exception bubbling up), true to the
    dome: we don't fabricate a value, we discard what isn't usable.
    """
    if not isinstance(payload, dict):
        return []
    rows = (
        payload.get("data", {})
        .get("attributes", {})
        .get("ohlcv_list", [])
    )
    if not isinstance(rows, list):
        return []
    candles: list[Candle] = []
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 6:
            continue
        try:
            candles.append(
                Candle(
                    ts=int(row[0]),
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    volume=float(row[5]),
                )
            )
        except (TypeError, ValueError):
            continue
    candles.sort(key=lambda c: c.ts)
    return candles


class OHLCVClient:
    """Async HTTP client, read-only, cautious throttle (public API, no key)."""

    def __init__(
        self, base_url: str = BASE_URL, *, min_interval: float = 2.2, use_shared_throttle: bool = False,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._min_interval = min_interval
        self._lock = asyncio.Lock()
        self._last_request = 0.0
        self._consecutive_failures = 0
        # 07/24 -- throughput audit found this client's OWN independent lock
        # never coordinated with geckoterminal.py's (both pace calls to the
        # SAME external GeckoTerminal account -- smart_money.py's per-token
        # loop calls both `gecko.resolve_primary_pool` and `gecko.get_ohlcv`,
        # which silently delegates here). Default False -- unchanged legacy
        # behavior for any test-constructed instance (the 7 existing
        # `OHLCVClient(min_interval=0.0)` sites keep working unmodified, never
        # touching the shared limiter). Only the module-level singleton below
        # opts in.
        self._use_shared_throttle = use_shared_throttle

    async def _throttle(self) -> None:
        if self._use_shared_throttle:
            # Lazy import, only resolved when actually throttling (never at
            # module load) -- no circular-import risk even if geckoterminal.py
            # someday stops being lazy about importing this module back.
            from aria_core.services.geckoterminal import wait_for_shared_rate_limit

            await wait_for_shared_rate_limit()
            return
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
                "ohlcv: %s consecutive failures (last: %s) — no blocking, no escalation",
                self._consecutive_failures,
                detail,
            )
        else:
            logger.info(
                "ohlcv: call failure (%s/%s) — %s",
                self._consecutive_failures,
                _FAIL_STREAK_WARN_THRESHOLD,
                detail,
            )

    async def _get_json(self, path: str, params: dict[str, object]) -> tuple[object | None, str | None]:
        """GET with the AGENTS.md error policy. Returns (data, error).

        08/01 -- real bug found live (operator report "sa trade beaucoup moin
        depuis 14h", traced to a sustained GeckoTerminal 429 burst): this
        client never sent `COINGECKO_DEMO_API_KEY` at all, even though
        `services/geckoterminal.py` has done so correctly since 18/07 -- this
        module (a SEPARATE client, see module docstring) was simply never
        updated. Silently ran at the KEYLESS throughput (~10 req/min per the
        19/07 incident's verified figures) instead of the Demo-key tier
        (~30 req/min) its shared throttle (`use_shared_throttle=True`) was
        already calibrated for -- explains a real, sustained gap between the
        throttle's intent and the actual server-side allowance. Header sent
        whenever the key is configured, never invented if absent."""
        url = f"{self.base_url}{path}"
        attempt_429 = 0
        timeout_retried = False
        headers = {"Accept": "application/json"}
        api_key = os.environ.get("COINGECKO_DEMO_API_KEY", "").strip()
        if api_key:
            headers["x-cg-demo-api-key"] = api_key

        while True:
            await self._throttle()
            try:
                async with httpx.AsyncClient(timeout=25.0) as client:
                    response = await client.get(url, params=params, headers=headers)
            except httpx.TransportError as exc:
                if not timeout_retried:
                    timeout_retried = True
                    await asyncio.sleep(5.0)
                    continue
                self._record_failure(f"{url} -> {exc}")
                return None, f"{UNAVAILABLE} (GeckoTerminal timeout)"

            if response.status_code == 429:
                attempt_429 += 1
                if attempt_429 >= 3:
                    self._record_failure(f"{url} -> HTTP 429 after {attempt_429} attempts")
                    return None, f"{UNAVAILABLE} (GeckoTerminal rate limit)"
                await asyncio.sleep(0.5 * (2**attempt_429))
                continue

            if response.status_code >= 500:
                if not timeout_retried:
                    timeout_retried = True
                    await asyncio.sleep(5.0)
                    continue
                self._record_failure(f"{url} -> HTTP {response.status_code}")
                return None, f"{UNAVAILABLE} (GeckoTerminal server error)"

            if response.status_code in (400, 404):
                self._record_success()
                return None, "pool not found on GeckoTerminal"

            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                self._record_failure(f"{url} -> {exc}")
                return None, f"{UNAVAILABLE} ({exc})"

            self._record_success()
            return response.json(), None

    async def get_ohlcv(
        self, pool_address: str, *, network: str = DEFAULT_NETWORK,
        min_useful_candles: int = _MIN_USEFUL_CANDLES, mode: str = "standard",
        skip_daily: bool = False,
    ) -> OHLCVResult:
        """Fetches the best available OHLCV series for a pool.

        Walks the 1D → 4H → 1H ladder and stops at the first timeframe that
        provides enough candles (`min_useful_candles`, default
        `_MIN_USEFUL_CANDLES`). If none reaches the threshold, returns the
        richest one obtained; if nothing is obtained, an explicit
        `OHLCVResult(available=False)`.

        ``min_useful_candles`` (#182, 15/07, wallet-scoring speed fix): the
        default threshold (20 candles) makes sense for
        `ta_levels`/`chart_render` (needs enough candles to compute
        support/resistance), but makes NO sense for a caller that only uses
        `price_at` (a single candle closest to a timestamp) -- this case is
        satisfied with ONE candle and never needs to escalate through 2 extra
        GeckoTerminal calls (insufficient day -> 4h -> 1h) for a
        young/microcap token that doesn't yet have 20 daily candles. Default
        unchanged (`_MIN_USEFUL_CANDLES`) for all existing callers -- no
        regression on `/vc`.

        ``mode`` (Item #101, 26/07): ``"scalping"`` walks the dedicated
        15min -> 30min ladder instead -- see ``_ladder_for_mode``. Default
        ``"standard"`` is the original 1D/4H/1H ladder, unchanged behavior
        for every existing caller.

        ``skip_daily`` (#157, revived 08/02 -- real bug found live 14/07): a
        token with a long enough history (>= `min_useful_candles` daily
        candles) always got the daily rung first, even when a caller (e.g.
        wallet-scoring) has multiple trades on the SAME civil day -- valuing
        them all against one candle/day silently collapses `buy_price` and
        `sell_price` onto the same point, `pnl_usd` becomes 0.0 with no
        existing guard (`unpriced_legs`, tx_hash pricing) catching it. `True`
        excludes the `"day"` rung from the ladder walked. `False` by default
        -- unchanged behavior for every existing caller. Naturally a no-op
        under `mode="scalping"` (that ladder has no daily rung to begin
        with).

        26/07 -- operator-found gap (real prod incident, GeckoTerminal 429s
        during a 40-candidate scan burst): a real network/rate-limit/server
        error (429, timeout, 5xx, unknown pool) used to be treated the SAME
        as "not enough candles at this timeframe" -- both fell through to
        `continue`, escalating to the next rung of the ladder. But a 429 or
        an unknown-pool 404 applies to the WHOLE pool/endpoint regardless of
        which timeframe is requested -- confirmed live in prod logs: the
        SAME pool hit `ohlcv/day -> 429` immediately followed by
        `ohlcv/hour -> 429` within a burst, wasting 2-3x the throttled calls
        on a candidate already doomed by the SAME underlying condition.
        Escalating the ladder only ever helps the OTHER case (server
        answered fine, this pool/timeframe combination just doesn't have
        enough candles yet) -- so a real error now stops the ladder
        immediately (``break``), returning whatever partial result (`best`)
        was already obtained rather than compounding the failure with more
        doomed attempts. Cuts real request volume during a burst by
        ~2-3x with zero loss of coverage (no candidate is scanned less
        often -- this only trims wasted retries on candidates already
        rate-limited)."""
        pool = (pool_address or "").strip()
        if not pool:
            return OHLCVResult(pool_address="", network=network, error=f"{UNAVAILABLE} (missing pool)")

        best: OHLCVResult | None = None
        last_error: str | None = None

        ladder = _ladder_for_mode(mode)
        if skip_daily:
            ladder = tuple(step for step in ladder if step[0] != "day")

        for period, aggregate, limit, label in ladder:
            data, error = await self._get_json(
                f"/networks/{network}/pools/{pool}/ohlcv/{period}",
                {"aggregate": aggregate, "limit": limit},
            )
            if error is not None:
                last_error = error
                break
            candles = _parse_candles(data)
            if not candles:
                last_error = f"{UNAVAILABLE} (no {label} candle)"
                continue
            result = OHLCVResult(
                pool_address=pool,
                network=network,
                candles=candles,
                timeframe=label,
                available=True,
                error=None,
            )
            if len(candles) >= min_useful_candles:
                return result
            # Thin window: keep it as a fallback but try a finer one.
            if best is None or len(candles) > len(best.candles):
                best = result

        if best is not None:
            return best
        return OHLCVResult(
            pool_address=pool, network=network, error=last_error or UNAVAILABLE
        )


ohlcv_client = OHLCVClient(use_shared_throttle=True)
