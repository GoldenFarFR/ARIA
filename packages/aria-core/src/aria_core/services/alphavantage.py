"""Read-only Alpha Vantage client — equity indices (ETF proxy), ETFs,
commodities excluding precious metals (task #14 follow-up, 07/13 -- macro overlay).

Guardrail doctrine, same pattern as ``forex.py``: GET only, no writes,
exponential backoff on 429 (3 attempts), 1 retry after 5s on timeout/5xx,
``fetch_*``/``get_*`` never raise on network error, explicit ``available=False``,
no missing data ever replaced by a guess.

Deliberate divergence from Frankfurter (cf. research note
``docs/aria-learning-inbox/2026-07-13-veille-sources-donnees-actions-etf-matieres-
premieres.md``): API key required, real free cap of only 25
requests/day. Two structural consequences:

- **No native index endpoint** (``^GSPC``/``^IXIC``): "equity indices"
  are queried via their ETF-replica (``GLOBAL_QUOTE`` on SPY/QQQ) --
  ``QuoteResult.is_proxy`` makes this explicit to every caller, never presented
  as the index itself.
- **Gold/silver not covered**: no documented endpoint at this provider for
  precious metals (verified in the research note) -- a structural absence, not a
  wiring choice. ``get_commodity`` refuses any function outside the
  whitelist below, even if a caller tried "GOLD"/"SILVER".

Strict cache + daily budget: an in-memory-only cache (cf.
``btc_cycles._phase_cache``) would lose the count on every process restart
and could exceed the real 25/day cap -- here persistence is
necessary, not just good-taste frugality. ``aiosqlite`` + ``aria_db_path()``
(same infra as ``ux_watch.py``/``pump_dump_autopsy.py``), two tables:
``alphavantage_cache`` (JSON payload, 24h TTL) and ``alphavantage_daily_calls``
(counter per calendar day). Internal budget deliberately lower than the
real cap (20 instead of 25) -- safety margin to absorb a
manual/debug test without ever hitting the wall.

**Assumed hypothesis, to correct if the exact info is known**: the reset
day of the Alpha Vantage cap is not specified in the research note (probably
midnight US market time, not UTC). Absent confirmation, the counter uses
a UTC calendar day -- deterministic and simple, potentially offset by
a few hours from the provider's real reset. In the worst case this
slightly underuses the quota (never an overrun), never the reverse.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone

import aiosqlite
import httpx

from aria_core.paths import aria_db_path

logger = logging.getLogger(__name__)

BASE_URL = "https://www.alphavantage.co/query"

UNAVAILABLE = "donnée Alpha Vantage indisponible"

DB_PATH = str(aria_db_path())

# Internal budget < real cap (25/day) -- deliberate safety margin.
DAILY_BUDGET = 20

# Cache TTL: uniform 24h for everything (quotes AND commodities), even though
# the research note flags commodities as "close to real-time" at this
# provider -- the 25/day cap dominates anyway, no differentiated
# handling that would complicate the logic without real benefit.
_CACHE_TTL_SECONDS = 24 * 3600

# ETF-proxy symbols for equity indices (not the native index -- absent from
# the API). SPY = S&P 500, QQQ = Nasdaq 100.
PROXY_SYMBOLS = {"SPY", "QQQ"}

# "Commodities" functions documented by Alpha Vantage and verified in the
# research note -- strict whitelist, no free parameter. Gold/silver absent
# (not an oversight: no endpoint at this provider).
COMMODITY_FUNCTIONS = {
    "WTI",
    "BRENT",
    "NATURAL_GAS",
    "COPPER",
    "ALUMINUM",
    "WHEAT",
    "CORN",
    "COTTON",
    "SUGAR",
    "COFFEE",
    "ALL_COMMODITIES",
}


@dataclass
class QuoteResult:
    """Real ETF quote (``GLOBAL_QUOTE``), never an invented data point."""

    symbol: str
    price: float | None = None
    change_pct: float | None = None
    latest_trading_day: str | None = None
    is_proxy: bool = False
    stale: bool = False
    available: bool = False
    error: str | None = None


@dataclass
class CommodityResult:
    """Real commodity value, never an invented data point."""

    function: str
    value: float | None = None
    unit: str | None = None
    date: str | None = None
    stale: bool = False
    available: bool = False
    error: str | None = None


def alphavantage_context_enabled() -> bool:
    return os.environ.get("ARIA_ALPHAVANTAGE_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


async def _ensure_tables() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS alphavantage_cache (
                cache_key TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                fetched_at TEXT NOT NULL
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS alphavantage_daily_calls (
                call_date TEXT PRIMARY KEY,
                count INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        await db.commit()


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _get_cached(cache_key: str) -> tuple[dict | None, bool]:
    """Returns ``(payload, stale)``. ``payload`` is ``None`` if never cached;
    ``stale=True`` if present but expired (still usable as a last
    resort if the daily budget is exhausted)."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT payload, fetched_at FROM alphavantage_cache WHERE cache_key = ?",
            (cache_key,),
        )
        row = await cursor.fetchone()
    if row is None:
        return None, False
    payload_raw, fetched_at = row
    try:
        payload = json.loads(payload_raw)
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(fetched_at)).total_seconds()
    except (json.JSONDecodeError, TypeError, ValueError):
        return None, False
    return payload, age >= _CACHE_TTL_SECONDS


async def _set_cached(cache_key: str, payload: dict) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO alphavantage_cache (cache_key, payload, fetched_at) VALUES (?, ?, ?) "
            "ON CONFLICT(cache_key) DO UPDATE SET payload=excluded.payload, fetched_at=excluded.fetched_at",
            (cache_key, json.dumps(payload), _now()),
        )
        await db.commit()


async def _budget_remaining() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT count FROM alphavantage_daily_calls WHERE call_date = ?", (_today(),)
        )
        row = await cursor.fetchone()
    used = row[0] if row else 0
    return max(0, DAILY_BUDGET - used)


async def _record_call() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO alphavantage_daily_calls (call_date, count) VALUES (?, 1) "
            "ON CONFLICT(call_date) DO UPDATE SET count = count + 1",
            (_today(),),
        )
        await db.commit()


class AlphaVantageClient:
    """Async HTTP client, read-only, persisted cache + daily budget."""

    def __init__(self, base_url: str = BASE_URL) -> None:
        self.base_url = base_url

    def _api_key(self) -> str | None:
        return os.environ.get("ALPHAVANTAGE_API_KEY", "").strip() or None

    async def _get_json(self, params: dict) -> tuple[dict | None, str | None]:
        attempt_429 = 0
        timeout_retried = False
        url = self.base_url

        while True:
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    response = await client.get(url, params=params)
            except httpx.TransportError as exc:
                if not timeout_retried:
                    timeout_retried = True
                    await asyncio.sleep(5.0)
                    continue
                logger.info("alphavantage: timeout (%s)", exc)
                return None, f"{UNAVAILABLE} (timeout)"

            if response.status_code == 429:
                attempt_429 += 1
                if attempt_429 >= 3:
                    logger.info("alphavantage: HTTP 429 after %s attempts", attempt_429)
                    return None, f"{UNAVAILABLE} (rate limit)"
                await asyncio.sleep(0.5 * (2**attempt_429))
                continue

            if response.status_code >= 500:
                if not timeout_retried:
                    timeout_retried = True
                    await asyncio.sleep(5.0)
                    continue
                logger.info("alphavantage: HTTP %s", response.status_code)
                return None, f"{UNAVAILABLE} (erreur serveur)"

            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                logger.info("alphavantage: %s", exc)
                return None, f"{UNAVAILABLE} ({exc})"

            data = response.json()
            if not isinstance(data, dict) or "Note" in data or "Information" in data:
                # Alpha Vantage returns a 200 OK with a text message in
                # "Note"/"Information" when the cap is hit server-side
                # (not a real payload) -- treated as an explicit failure, never
                # parsed as real data.
                return None, f"{UNAVAILABLE} (plafond fournisseur atteint)"
            return data, None

    async def _fetch_with_budget(self, cache_key: str, params: dict) -> tuple[dict | None, bool, str | None]:
        """Returns ``(payload, stale, error)``. Serves the cache if valid; otherwise
        checks the daily budget before any real network call; if the budget
        is exhausted, falls back to the cache even if expired rather than nothing."""
        await _ensure_tables()

        cached, stale = await _get_cached(cache_key)
        if cached is not None and not stale:
            return cached, False, None

        remaining = await _budget_remaining()
        if remaining <= 0:
            if cached is not None:
                return cached, True, None
            return None, False, f"{UNAVAILABLE} (budget quotidien épuisé, aucune valeur en cache)"

        api_key = self._api_key()
        if not api_key:
            if cached is not None:
                return cached, True, None
            return None, False, f"{UNAVAILABLE} (clé API absente)"

        await _record_call()
        data, error = await self._get_json({**params, "apikey": api_key})
        if error is not None:
            if cached is not None:
                return cached, True, None
            return None, False, error

        await _set_cached(cache_key, data)
        return data, False, None

    async def get_quote(self, symbol: str) -> QuoteResult:
        """Real ETF quote via ``GLOBAL_QUOTE`` -- a proxy for the index it
        replicates (SPY/QQQ), never presented as the native index."""
        sym = (symbol or "").strip().upper()
        if not sym:
            return QuoteResult(symbol=sym, available=False, error=UNAVAILABLE)

        cache_key = f"GLOBAL_QUOTE:{sym}"
        data, stale, error = await self._fetch_with_budget(
            cache_key, {"function": "GLOBAL_QUOTE", "symbol": sym}
        )
        if error is not None:
            return QuoteResult(symbol=sym, is_proxy=sym in PROXY_SYMBOLS, available=False, error=error)

        quote = (data or {}).get("Global Quote") if data else None
        if not isinstance(quote, dict) or not quote:
            return QuoteResult(
                symbol=sym, is_proxy=sym in PROXY_SYMBOLS, available=False, error=UNAVAILABLE
            )

        try:
            price = float(quote.get("05. price"))
        except (TypeError, ValueError):
            price = None
        change_pct_raw = str(quote.get("10. change percent", "")).rstrip("%")
        try:
            change_pct = float(change_pct_raw) if change_pct_raw else None
        except ValueError:
            change_pct = None

        if price is None:
            return QuoteResult(
                symbol=sym, is_proxy=sym in PROXY_SYMBOLS, available=False, error=UNAVAILABLE
            )

        return QuoteResult(
            symbol=sym,
            price=price,
            change_pct=change_pct,
            latest_trading_day=quote.get("07. latest trading day"),
            is_proxy=sym in PROXY_SYMBOLS,
            stale=stale,
            available=True,
        )

    async def get_commodity(self, function: str) -> CommodityResult:
        """Real commodity value -- function restricted to the
        whitelist verified in the research note (excluding precious metals, absent at this
        provider)."""
        fn = (function or "").strip().upper()
        if fn not in COMMODITY_FUNCTIONS:
            return CommodityResult(function=fn, available=False, error=f"{UNAVAILABLE} (fonction non couverte)")

        cache_key = f"COMMODITY:{fn}"
        data, stale, error = await self._fetch_with_budget(cache_key, {"function": fn})
        if error is not None:
            return CommodityResult(function=fn, available=False, error=error)

        series = (data or {}).get("data") if data else None
        if not isinstance(series, list) or not series:
            return CommodityResult(function=fn, available=False, error=UNAVAILABLE)

        latest = series[0]
        try:
            value = float(latest.get("value"))
        except (TypeError, ValueError, AttributeError):
            return CommodityResult(function=fn, available=False, error=UNAVAILABLE)

        return CommodityResult(
            function=fn,
            value=value,
            unit=data.get("unit") if data else None,
            date=latest.get("date") if isinstance(latest, dict) else None,
            stale=stale,
            available=True,
        )


alphavantage_client = AlphaVantageClient()


async def fetch_equities_commodities_context(*, client: AlphaVantageClient | None = None) -> dict | None:
    """Compact entry point for the VC reports' macro overlay (task #14
    follow-up, 07/13). Fail-closed (gate OFF by default) AND soft degradation:
    each source (SPY, QQQ, composite commodities) is independent --
    the absence of one never blocks the others. ``None`` only if all
    THREE fail (nothing to show), never an invented value to fill in."""
    if not alphavantage_context_enabled():
        return None

    if client is None:
        client = alphavantage_client

    spy = await client.get_quote("SPY")
    qqq = await client.get_quote("QQQ")
    commodities = await client.get_commodity("ALL_COMMODITIES")

    ctx: dict = {}
    if spy.available:
        ctx["spy"] = {"price": spy.price, "change_pct": spy.change_pct, "date": spy.latest_trading_day, "stale": spy.stale}
    if qqq.available:
        ctx["qqq"] = {"price": qqq.price, "change_pct": qqq.change_pct, "date": qqq.latest_trading_day, "stale": qqq.stale}
    if commodities.available:
        ctx["commodities"] = {"value": commodities.value, "unit": commodities.unit, "date": commodities.date, "stale": commodities.stale}

    return ctx or None
