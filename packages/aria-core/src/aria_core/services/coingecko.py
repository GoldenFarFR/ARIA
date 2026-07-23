"""Read-only CoinGecko client — fundamental data (Base).

Complements Blockscout (on-chain) and DexScreener (short-term market) with
fundamental data: market cap, FDV, supply, categories, token age.
No writes. Optional CoinGecko Demo key (`COINGECKO_DEMO_API_KEY`,
free, cf. VPS `.env`) — CoinGecko now requires this key even on its
public tier (systematic 401 without it, policy change observed on
09/07); if absent, the client fails cleanly (never an invented value).
Same error policy as `services/blockscout.py` (cf. AGENTS.md):
- 429: exponential backoff, 3 attempts max, then give up without blocking the pipeline.
- Timeout / endpoint unavailable: 1 retry after 5s, then explicit fallback.
- Missing data is never replaced by a guess — the
  `error` field (and `available=False`) carries the absence of data.
- Repeated consecutive failures (>3): logged, never blocking, never Telegram spam.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://api.coingecko.com/api/v3"

UNAVAILABLE = "donnée fondamentale indisponible"

_FAIL_STREAK_WARN_THRESHOLD = 3


@dataclass
class TokenFundamentals:
    contract: str
    coingecko_id: str | None = None
    name: str | None = None
    symbol: str | None = None
    market_cap_usd: float | None = None
    fully_diluted_valuation_usd: float | None = None
    circulating_supply: float | None = None
    total_supply: float | None = None
    max_supply: float | None = None
    categories: list[str] = field(default_factory=list)
    homepage: str | None = None
    whitepaper: str | None = None
    genesis_date: str | None = None
    available: bool = False
    error: str | None = None


@dataclass
class SimplePriceResult:
    """Real current prices (``simple/price``), never an invented data point —
    absence of data carried by ``available=False`` + ``error``. ``last_updated_at``
    (Unix, provided by CoinGecko itself via ``include_last_updated_at``) is the real
    proof of freshness: unlike a scraped web page (cf. 10/07 incident
    — stale BTC/SOL prices cited as "live"), this isn't just the
    content of a page, it's CoinGecko's own update timestamp."""

    prices: dict[str, dict[str, float]] = field(default_factory=dict)  # {coin_id: {vs_ccy: price}}
    last_updated_at: dict[str, int] = field(default_factory=dict)  # {coin_id: unix_ts}
    available: bool = False
    error: str | None = None


@dataclass
class MarketChartResult:
    """Real (ms timestamp, USD price) series for a major currency — never an invented
    data point: absence of data carried by `available=False` + `error`."""

    coin_id: str
    prices: list[tuple[int, float]] = field(default_factory=list)
    available: bool = False
    error: str | None = None


class CoinGeckoClient:
    """Async HTTP client, read-only, cautious throttle (public API, no key)."""

    # 21/07 -- calibrated to 90% of the confirmed 100 req/min (Demo tier, key
    # COINGECKO_DEMO_API_KEY already configured and attached below -- two
    # independent official sources: docs.coingecko.com/docs/common-errors-
    # rate-limit and coingecko.com/en/api/pricing). CLAUDE.md "90% calibrated
    # throughput" doctrine: 90/min = 0.667s. Replaces 2.2s (27/min, 27% of the
    # real capacity -- under-utilized since the beginning).
    def __init__(self, base_url: str = BASE_URL, *, min_interval: float = 0.667) -> None:
        self.base_url = base_url.rstrip("/")
        self._min_interval = min_interval
        self._lock = asyncio.Lock()
        self._last_request = 0.0
        self._consecutive_failures = 0
        # CoinGecko now requires a free Demo key even on the public tier
        # (policy change observed on 09/07 — systematic 401 without it).
        # Optional here: absent -> behavior unchanged (failure handled normally,
        # never an invented value), present -> added as a header on every call.
        self._api_key = os.environ.get("COINGECKO_DEMO_API_KEY", "").strip() or None

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
                "coingecko: %s consecutive failures (last: %s) — no blocking, no escalation",
                self._consecutive_failures,
                detail,
            )
        else:
            logger.info(
                "coingecko: call failed (%s/%s) — %s",
                self._consecutive_failures,
                _FAIL_STREAK_WARN_THRESHOLD,
                detail,
            )

    async def _get_json(self, path: str) -> tuple[object | None, str | None]:
        """GET with the AGENTS.md error policy. Returns (data, error)."""
        url = f"{self.base_url}{path}"
        attempt_429 = 0
        timeout_retried = False

        while True:
            await self._throttle()
            headers = {"x-cg-demo-api-key": self._api_key} if self._api_key else {}
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    response = await client.get(url, headers=headers)
            except httpx.TransportError as exc:
                if not timeout_retried:
                    timeout_retried = True
                    await asyncio.sleep(5.0)
                    continue
                detail = f"{url} -> {exc}"
                self._record_failure(detail)
                return None, f"{UNAVAILABLE} (timeout CoinGecko)"

            if response.status_code == 429:
                attempt_429 += 1
                if attempt_429 >= 3:
                    detail = f"{url} -> HTTP 429 apres {attempt_429} tentatives"
                    self._record_failure(detail)
                    return None, f"{UNAVAILABLE} (rate limit CoinGecko)"
                await asyncio.sleep(0.5 * (2**attempt_429))
                continue

            if response.status_code >= 500:
                if not timeout_retried:
                    timeout_retried = True
                    await asyncio.sleep(5.0)
                    continue
                detail = f"{url} -> HTTP {response.status_code}"
                self._record_failure(detail)
                return None, f"{UNAVAILABLE} (erreur serveur CoinGecko)"

            if response.status_code == 404:
                self._record_success()
                return None, "token non listé sur CoinGecko"

            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                detail = f"{url} -> {exc}"
                self._record_failure(detail)
                return None, f"{UNAVAILABLE} ({exc})"

            self._record_success()
            return response.json(), None

    async def get_token_fundamentals(self, contract: str, *, platform_id: str = "base") -> TokenFundamentals:
        data, error = await self._get_json(f"/coins/{platform_id}/contract/{contract}")
        if error is not None:
            return TokenFundamentals(contract=contract, available=False, error=error)
        if not isinstance(data, dict):
            return TokenFundamentals(contract=contract, available=False, error=UNAVAILABLE)

        market_data = data.get("market_data") or {}

        def _usd(field_name: str) -> float | None:
            value = (market_data.get(field_name) or {}).get("usd") if isinstance(market_data.get(field_name), dict) else None
            try:
                return float(value) if value is not None else None
            except (TypeError, ValueError):
                return None

        def _num(field_name: str) -> float | None:
            value = market_data.get(field_name)
            try:
                return float(value) if value is not None else None
            except (TypeError, ValueError):
                return None

        links = data.get("links") or {}
        homepage_list = links.get("homepage") or []
        whitepaper = links.get("whitepaper")

        return TokenFundamentals(
            contract=contract,
            coingecko_id=data.get("id"),
            name=data.get("name"),
            symbol=data.get("symbol"),
            market_cap_usd=_usd("market_cap"),
            fully_diluted_valuation_usd=_usd("fully_diluted_valuation"),
            circulating_supply=_num("circulating_supply"),
            total_supply=_num("total_supply"),
            max_supply=_num("max_supply"),
            categories=[c for c in (data.get("categories") or []) if c],
            homepage=next((h for h in homepage_list if h), None),
            whitepaper=whitepaper or None,
            genesis_date=data.get("genesis_date"),
            available=True,
            error=None,
        )

    async def get_simple_price(
        self, coin_ids: list[str], *, vs_currencies: list[str] | None = None
    ) -> SimplePriceResult:
        """Real current prices for a list of CoinGecko coins (``simple/price``).

        Replaces web-page scraping (real incident 10/07: BTC/SOL cited ~30% below
        their real price from a stale page presented as "live") with a structured
        data point, timestamped by CoinGecko itself.
        ``coin_ids`` are CoinGecko identifiers (e.g. ``"bitcoin"``, not ``"BTC"``)
        — symbol→id resolution lives in ``skills/market_quotes.py``, not here
        (this client stays a plain HTTP client, no business logic).
        """
        vs = vs_currencies or ["usd"]
        if not coin_ids:
            return SimplePriceResult(available=False, error=UNAVAILABLE)
        ids_param = ",".join(coin_ids)
        vs_param = ",".join(vs)
        data, error = await self._get_json(
            f"/simple/price?ids={ids_param}&vs_currencies={vs_param}&include_last_updated_at=true"
        )
        if error is not None:
            return SimplePriceResult(available=False, error=error)
        if not isinstance(data, dict) or not data:
            return SimplePriceResult(available=False, error=UNAVAILABLE)

        prices: dict[str, dict[str, float]] = {}
        last_updated_at: dict[str, int] = {}
        for coin_id, payload in data.items():
            if not isinstance(payload, dict):
                continue
            coin_prices = {}
            for ccy in vs:
                value = payload.get(ccy)
                try:
                    if value is not None:
                        coin_prices[ccy] = float(value)
                except (TypeError, ValueError):
                    continue
            if coin_prices:
                prices[coin_id] = coin_prices
            ts = payload.get("last_updated_at")
            try:
                if ts is not None:
                    last_updated_at[coin_id] = int(ts)
            except (TypeError, ValueError):
                pass

        if not prices:
            return SimplePriceResult(available=False, error=UNAVAILABLE)
        return SimplePriceResult(
            prices=prices, last_updated_at=last_updated_at, available=True, error=None
        )

    async def get_market_chart_range(
        self, coin_id: str, start_ts: int, end_ts: int, *, vs_currency: str = "usd"
    ) -> MarketChartResult:
        """Real price series (`market_chart/range`) for a major currency (e.g.
        `bitcoin`) between two Unix timestamps — automatic daily aggregation
        beyond 90 days on the public tier. Never an invented price: absence ->
        `available=False`."""
        data, error = await self._get_json(
            f"/coins/{coin_id}/market_chart/range?vs_currency={vs_currency}"
            f"&from={int(start_ts)}&to={int(end_ts)}"
        )
        if error is not None:
            return MarketChartResult(coin_id=coin_id, available=False, error=error)
        if not isinstance(data, dict):
            return MarketChartResult(coin_id=coin_id, available=False, error=UNAVAILABLE)

        raw = data.get("prices")
        if not isinstance(raw, list):
            return MarketChartResult(coin_id=coin_id, available=False, error=UNAVAILABLE)

        prices: list[tuple[int, float]] = []
        for row in raw:
            if not isinstance(row, (list, tuple)) or len(row) < 2:
                continue
            try:
                prices.append((int(row[0]), float(row[1])))
            except (TypeError, ValueError):
                continue
        if not prices:
            return MarketChartResult(coin_id=coin_id, available=False, error=UNAVAILABLE)
        return MarketChartResult(coin_id=coin_id, prices=prices, available=True, error=None)


coingecko_client = CoinGeckoClient()
