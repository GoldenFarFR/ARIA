"""Read-only GeckoTerminal client — OHLCV (candle) series for Base.

Provides the **raw material** of technical analysis: a series of real OHLCV
candles for a DEX pool, which `skills/ta_levels.py` turns into levels
(support / resistance / trend) and `skills/chart_render.py` charts.

GeckoTerminal public tier (no key required). Error policy identical to
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

    def __init__(self, base_url: str = BASE_URL, *, min_interval: float = 2.2) -> None:
        self.base_url = base_url.rstrip("/")
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
        """GET with the AGENTS.md error policy. Returns (data, error)."""
        url = f"{self.base_url}{path}"
        attempt_429 = 0
        timeout_retried = False

        while True:
            await self._throttle()
            try:
                async with httpx.AsyncClient(timeout=25.0) as client:
                    response = await client.get(
                        url, params=params, headers={"Accept": "application/json"}
                    )
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
        self, pool_address: str, *, network: str = DEFAULT_NETWORK, min_useful_candles: int = _MIN_USEFUL_CANDLES,
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
        regression on `/vc`."""
        pool = (pool_address or "").strip()
        if not pool:
            return OHLCVResult(pool_address="", network=network, error=f"{UNAVAILABLE} (missing pool)")

        best: OHLCVResult | None = None
        last_error: str | None = None

        for period, aggregate, limit, label in _FETCH_LADDER:
            data, error = await self._get_json(
                f"/networks/{network}/pools/{pool}/ohlcv/{period}",
                {"aggregate": aggregate, "limit": limit},
            )
            if error is not None:
                last_error = error
                continue
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


ohlcv_client = OHLCVClient()
