"""Tests du client Alpha Vantage (lecture seule) — aucun appel réseau réel, tout
mocké. Couvre : le dôme d'erreurs (429/timeout/5xx/plafond fournisseur), le cache
persistant (TTL 24h), le budget quotidien (20/jour, retombe sur le cache même
expiré une fois épuisé), la liste blanche des fonctions commodities (or/argent
absents), et le point d'entrée compact ``fetch_equities_commodities_context``."""

from __future__ import annotations

import json

import pytest

from aria_core.services import alphavantage as av


class FakeResponse:
    def __init__(self, status_code: int, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx

            raise httpx.HTTPStatusError("error", request=None, response=self)


class FakeClient:
    def __init__(self, responses: dict):
        # keyed by frozenset(params.items()) -> FakeResponse or list of FakeResponse
        self._responses = responses

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def get(self, url, params=None):
        key = frozenset((params or {}).items())
        queue = self._responses[key]
        if isinstance(queue, list):
            return queue.pop(0)
        return queue


def _patch_client(monkeypatch, responses: dict):
    monkeypatch.setattr(
        "aria_core.services.alphavantage.httpx.AsyncClient",
        lambda **kw: FakeClient(responses),
    )


def _patch_no_sleep(monkeypatch):
    async def _fake_sleep(_seconds):
        return None

    monkeypatch.setattr("aria_core.services.alphavantage.asyncio.sleep", _fake_sleep)


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(av, "DB_PATH", str(tmp_path / "alphavantage_test.db"))
    monkeypatch.setenv("ALPHAVANTAGE_API_KEY", "test-key")
    yield


def _quote_params(symbol: str) -> frozenset:
    return frozenset({"function": "GLOBAL_QUOTE", "symbol": symbol, "apikey": "test-key"}.items())


def _commodity_params(fn: str) -> frozenset:
    return frozenset({"function": fn, "apikey": "test-key"}.items())


# ── get_quote ───────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_quote_success_marks_proxy_symbols(monkeypatch):
    client = av.AlphaVantageClient()
    payload = {
        "Global Quote": {
            "05. price": "512.34",
            "10. change percent": "1.23%",
            "07. latest trading day": "2026-07-13",
        }
    }
    _patch_client(monkeypatch, {_quote_params("SPY"): FakeResponse(200, payload)})

    result = await client.get_quote("spy")

    assert isinstance(result, av.QuoteResult)
    assert result.available is True
    assert result.price == pytest.approx(512.34)
    assert result.change_pct == pytest.approx(1.23)
    assert result.latest_trading_day == "2026-07-13"
    assert result.is_proxy is True
    assert result.stale is False


@pytest.mark.asyncio
async def test_get_quote_non_proxy_symbol_not_flagged(monkeypatch):
    client = av.AlphaVantageClient()
    payload = {"Global Quote": {"05. price": "10.0", "10. change percent": "0.5%", "07. latest trading day": "2026-07-13"}}
    _patch_client(monkeypatch, {_quote_params("AAPL"): FakeResponse(200, payload)})

    result = await client.get_quote("AAPL")
    assert result.is_proxy is False


@pytest.mark.asyncio
async def test_get_quote_empty_symbol_no_call():
    client = av.AlphaVantageClient()
    result = await client.get_quote("")
    assert result.available is False


@pytest.mark.asyncio
async def test_get_quote_missing_api_key_no_cache_returns_unavailable(monkeypatch):
    monkeypatch.delenv("ALPHAVANTAGE_API_KEY", raising=False)
    client = av.AlphaVantageClient()
    result = await client.get_quote("SPY")
    assert result.available is False
    assert "clé API" in result.error


@pytest.mark.asyncio
async def test_get_quote_provider_cap_message_never_parsed_as_data(monkeypatch):
    client = av.AlphaVantageClient()
    _patch_client(
        monkeypatch,
        {_quote_params("SPY"): FakeResponse(200, {"Note": "Thank you for using Alpha Vantage! ..."})},
    )
    result = await client.get_quote("SPY")
    assert result.available is False
    assert result.price is None


@pytest.mark.asyncio
async def test_get_quote_network_error_never_raises(monkeypatch):
    _patch_no_sleep(monkeypatch)
    client = av.AlphaVantageClient()

    import httpx

    class TimeoutClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, params=None):
            raise httpx.ConnectError("network blocked")

    monkeypatch.setattr("aria_core.services.alphavantage.httpx.AsyncClient", lambda **kw: TimeoutClient())

    result = await client.get_quote("SPY")
    assert result.available is False


@pytest.mark.asyncio
async def test_get_quote_rate_limited_never_invents_a_price(monkeypatch):
    _patch_no_sleep(monkeypatch)
    client = av.AlphaVantageClient()
    _patch_client(monkeypatch, {_quote_params("SPY"): [FakeResponse(429), FakeResponse(429), FakeResponse(429)]})

    result = await client.get_quote("SPY")
    assert result.available is False
    assert result.price is None


# ── get_commodity ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_commodity_success(monkeypatch):
    client = av.AlphaVantageClient()
    payload = {"unit": "index points", "data": [{"date": "2026-07-13", "value": "104.2"}]}
    _patch_client(monkeypatch, {_commodity_params("ALL_COMMODITIES"): FakeResponse(200, payload)})

    result = await client.get_commodity("all_commodities")
    assert result.available is True
    assert result.value == pytest.approx(104.2)
    assert result.unit == "index points"
    assert result.date == "2026-07-13"


@pytest.mark.asyncio
async def test_get_commodity_rejects_functions_outside_whitelist_no_call():
    client = av.AlphaVantageClient()
    for fn in ("GOLD", "SILVER", "NOT_A_REAL_FUNCTION", ""):
        result = await client.get_commodity(fn)
        assert result.available is False
        assert "non couverte" in result.error


def test_gold_silver_never_in_whitelist():
    assert "GOLD" not in av.COMMODITY_FUNCTIONS
    assert "SILVER" not in av.COMMODITY_FUNCTIONS


# ── cache + budget quotidien ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_second_call_within_ttl_serves_cache_no_new_network_call(monkeypatch):
    client = av.AlphaVantageClient()
    payload = {"Global Quote": {"05. price": "1.0", "10. change percent": "0%", "07. latest trading day": "d"}}
    calls = {"n": 0}

    class CountingClient(FakeClient):
        async def get(self, url, params=None):
            calls["n"] += 1
            return await super().get(url, params=params)

    monkeypatch.setattr(
        "aria_core.services.alphavantage.httpx.AsyncClient",
        lambda **kw: CountingClient({_quote_params("SPY"): FakeResponse(200, payload)}),
    )

    await client.get_quote("SPY")
    await client.get_quote("SPY")

    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_daily_budget_exhausted_falls_back_to_stale_cache(monkeypatch):
    client = av.AlphaVantageClient()
    await av._ensure_tables()
    await av._set_cached("GLOBAL_QUOTE:SPY", {"Global Quote": {"05. price": "9.99", "10. change percent": "0%", "07. latest trading day": "old"}})
    # Force the cached entry to read as expired (fetched_at far in the past).
    import aiosqlite

    async with aiosqlite.connect(av.DB_PATH) as db:
        await db.execute(
            "UPDATE alphavantage_cache SET fetched_at = ? WHERE cache_key = ?",
            ("2000-01-01T00:00:00+00:00", "GLOBAL_QUOTE:SPY"),
        )
        await db.commit()
        await db.execute(
            "INSERT INTO alphavantage_daily_calls (call_date, count) VALUES (?, ?)",
            (av._today(), av.DAILY_BUDGET),
        )
        await db.commit()

    result = await client.get_quote("SPY")

    assert result.available is True
    assert result.stale is True
    assert result.price == pytest.approx(9.99)


@pytest.mark.asyncio
async def test_daily_budget_exhausted_and_no_cache_returns_unavailable_not_fabricated():
    await av._ensure_tables()
    import aiosqlite

    async with aiosqlite.connect(av.DB_PATH) as db:
        await db.execute(
            "INSERT INTO alphavantage_daily_calls (call_date, count) VALUES (?, ?)",
            (av._today(), av.DAILY_BUDGET),
        )
        await db.commit()

    client = av.AlphaVantageClient()
    result = await client.get_quote("SPY")
    assert result.available is False
    assert result.price is None


@pytest.mark.asyncio
async def test_record_call_increments_persist_across_client_instances(monkeypatch):
    payload = {"Global Quote": {"05. price": "1.0", "10. change percent": "0%", "07. latest trading day": "d"}}
    _patch_client(monkeypatch, {_quote_params("SPY"): [FakeResponse(200, payload), FakeResponse(200, {**payload})]})

    client1 = av.AlphaVantageClient()
    await client1.get_quote("SPY")
    remaining_after_one = await av._budget_remaining()

    assert remaining_after_one == av.DAILY_BUDGET - 1


# ── gate + point d'entrée compact ───────────────────────────────────────────────────

def test_context_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ARIA_ALPHAVANTAGE_ENABLED", raising=False)
    assert av.alphavantage_context_enabled() is False


@pytest.mark.asyncio
async def test_fetch_context_returns_none_when_disabled(monkeypatch):
    monkeypatch.delenv("ARIA_ALPHAVANTAGE_ENABLED", raising=False)
    result = await av.fetch_equities_commodities_context()
    assert result is None


@pytest.mark.asyncio
async def test_fetch_context_aggregates_independent_sources(monkeypatch):
    monkeypatch.setenv("ARIA_ALPHAVANTAGE_ENABLED", "1")

    class StubClient:
        async def get_quote(self, symbol):
            if symbol == "SPY":
                return av.QuoteResult(symbol="SPY", price=500.0, change_pct=1.0, latest_trading_day="d", is_proxy=True, available=True)
            return av.QuoteResult(symbol=symbol, available=False, error="down")

        async def get_commodity(self, function):
            return av.CommodityResult(function=function, value=42.0, unit="pts", date="d", available=True)

    ctx = await av.fetch_equities_commodities_context(client=StubClient())

    assert ctx is not None
    assert "spy" in ctx and ctx["spy"]["price"] == 500.0
    assert "qqq" not in ctx  # source manquante, jamais fabriquée
    assert ctx["commodities"]["value"] == 42.0


@pytest.mark.asyncio
async def test_fetch_context_none_when_all_sources_fail(monkeypatch):
    monkeypatch.setenv("ARIA_ALPHAVANTAGE_ENABLED", "1")

    class AllDownClient:
        async def get_quote(self, symbol):
            return av.QuoteResult(symbol=symbol, available=False, error="down")

        async def get_commodity(self, function):
            return av.CommodityResult(function=function, available=False, error="down")

    ctx = await av.fetch_equities_commodities_context(client=AllDownClient())
    assert ctx is None


def test_unavailable_message_exposed():
    assert isinstance(av.UNAVAILABLE, str) and av.UNAVAILABLE
