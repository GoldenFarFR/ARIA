"""Read-only Frankfurter client (exchange rates) — major currencies.

Frankfurter (`frankfurter.dev`) republishes the European Central Bank's (ECB)
daily reference rates: free, no key, no documented rate limit,
stable and documented API for years (unlike Clanker
earlier today — same "depth proportional to the stakes" doctrine:
only well-documented/verifiable clients are wired without detour). ECB
reference rates = daily (business days), not tick-by-tick — largely sufficient
for a conversational question like "how much is the dollar in euros", far
better than a scraped web page with no proof of freshness (cf. 10/07 incident:
BTC/SOL prices cited from a stale page as if it were "live").

Request/response shape (``/latest?base=X&symbols=Y`` → ``{"amount":1,"base":"USD",
"date":"...","rates":{"EUR":...}}``) confirmed by cross-checking (independent
third-party documentation, consistent across several sources) — the DIRECT fetch from
this cloud environment was blocked with HTTP 403 (same anti-bot behavior as
Clanker/Virtuals earlier today), so **not tested live**; to be reconfirmed
from the VPS before a first real call, same caution as the other clients in
this folder.

Same policies as the other clients in this folder:
- No writes, GET only.
- 429: exponential backoff, 3 attempts max, then give up without blocking.
- Timeout / 5xx: 1 retry after 5s, then explicit fallback.
- ``fetch_*`` NEVER raises on a network error: returns a result with ``available=False``.
- Missing data is never replaced by a guess.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://api.frankfurter.dev/v1"

UNAVAILABLE = "donnée de change indisponible"

_FAIL_STREAK_WARN_THRESHOLD = 3


@dataclass
class ExchangeRateResult:
    """Real ECB reference rate for a pair, never an invented data point."""

    base: str
    rates: dict[str, float] = field(default_factory=dict)
    date: str | None = None  # ECB reference date (YYYY-MM-DD), provided by the API
    available: bool = False
    error: str | None = None


class ForexClient:
    """Async HTTP client, read-only, cautious throttle (public API, no key)."""

    def __init__(self, base_url: str = BASE_URL, *, min_interval: float = 0.5) -> None:
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
                "forex: %s consecutive failures (last: %s) — no blocking, no escalation",
                self._consecutive_failures,
                detail,
            )
        else:
            logger.info(
                "forex: call failed (%s/%s) — %s",
                self._consecutive_failures,
                _FAIL_STREAK_WARN_THRESHOLD,
                detail,
            )

    async def _get_json(self, url: str) -> tuple[object | None, str | None]:
        attempt_429 = 0
        timeout_retried = False

        while True:
            await self._throttle()
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    response = await client.get(url, headers={"accept": "application/json"})
            except httpx.TransportError as exc:
                if not timeout_retried:
                    timeout_retried = True
                    await asyncio.sleep(5.0)
                    continue
                detail = f"{url} -> {exc}"
                self._record_failure(detail)
                return None, f"{UNAVAILABLE} (timeout Frankfurter)"

            if response.status_code == 429:
                attempt_429 += 1
                if attempt_429 >= 3:
                    detail = f"{url} -> HTTP 429 apres {attempt_429} tentatives"
                    self._record_failure(detail)
                    return None, f"{UNAVAILABLE} (rate limit Frankfurter)"
                await asyncio.sleep(0.5 * (2**attempt_429))
                continue

            if response.status_code >= 500:
                if not timeout_retried:
                    timeout_retried = True
                    await asyncio.sleep(5.0)
                    continue
                detail = f"{url} -> HTTP {response.status_code}"
                self._record_failure(detail)
                return None, f"{UNAVAILABLE} (erreur serveur Frankfurter)"

            if response.status_code in (400, 404):
                self._record_success()
                return None, f"{UNAVAILABLE} (devise inconnue)"

            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                detail = f"{url} -> {exc}"
                self._record_failure(detail)
                return None, f"{UNAVAILABLE} ({exc})"

            self._record_success()
            return response.json(), None

    async def get_latest_rates(self, base: str, symbols: list[str]) -> ExchangeRateResult:
        """Most recent ECB reference rates for ``base`` -> each currency in
        ``symbols`` (e.g. ``base="USD"``, ``symbols=["EUR"]``)."""
        base_ccy = (base or "").strip().upper()
        syms = [s.strip().upper() for s in symbols if s and s.strip()]
        if not base_ccy or not syms:
            return ExchangeRateResult(base=base_ccy, available=False, error=UNAVAILABLE)

        url = f"{self.base_url}/latest?base={base_ccy}&symbols={','.join(syms)}"
        data, error = await self._get_json(url)
        if error is not None:
            return ExchangeRateResult(base=base_ccy, available=False, error=error)
        if not isinstance(data, dict):
            return ExchangeRateResult(base=base_ccy, available=False, error=UNAVAILABLE)

        raw_rates = data.get("rates")
        if not isinstance(raw_rates, dict) or not raw_rates:
            return ExchangeRateResult(base=base_ccy, available=False, error=UNAVAILABLE)

        rates: dict[str, float] = {}
        for ccy, value in raw_rates.items():
            try:
                rates[str(ccy).upper()] = float(value)
            except (TypeError, ValueError):
                continue
        if not rates:
            return ExchangeRateResult(base=base_ccy, available=False, error=UNAVAILABLE)

        return ExchangeRateResult(
            base=base_ccy, rates=rates, date=data.get("date"), available=True, error=None
        )


forex_client = ForexClient()
