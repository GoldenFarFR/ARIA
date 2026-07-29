"""Tests for the Codex.io client (GraphQL getBars, Item #185, 29/07) -- new
OHLCV cascade tier inserted between DexPaprika and the DexScreener degraded
synthesis. No real network call, everything is mocked -- no
``CODEX_IO_API_KEY`` exists in this environment (see the module's own
docstring), so this client has never been exercised against the real API."""

from __future__ import annotations

import httpx
import pytest

from aria_core.services import codex

POOL = "0x" + "ab" * 20


class FakeResponse:
    def __init__(self, status_code: int, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=self)


class FakeClient:
    def __init__(self, responses):
        self._responses = responses
        self.calls: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, json=None, headers=None, **kwargs):
        self.calls.append({"url": url, "json": json, "headers": headers})
        queue = self._responses
        if isinstance(queue, list):
            return queue.pop(0)
        return queue


def _patch_client(monkeypatch, responses):
    fake = FakeClient(responses)
    monkeypatch.setattr("aria_core.services.codex.httpx.AsyncClient", lambda **kw: fake)
    return fake


def _patch_no_sleep(monkeypatch):
    async def _fake_sleep(_seconds):
        return None

    monkeypatch.setattr("aria_core.services.codex.asyncio.sleep", _fake_sleep)


def _bars_payload(n: int, *, start_ts: int = 1_700_000_000) -> dict:
    step = 86400
    t = [start_ts + i * step for i in range(n)]
    return {
        "data": {
            "getBars": {
                "o": [1.0 + i for i in range(n)],
                "h": [2.0 + i for i in range(n)],
                "l": [0.5 + i for i in range(n)],
                "c": [1.5 + i for i in range(n)],
                "volume": [10.0 for _ in range(n)],
                "t": t,
            }
        }
    }


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(codex, "DB_PATH", str(tmp_path / "codex_test.db"))
    yield


@pytest.fixture(autouse=True)
def _no_throttle_wait(monkeypatch):
    async def _instant(*args, **kwargs):
        return None

    monkeypatch.setattr(codex, "_throttle", _instant)


# ── codex_configured ─────────────────────────────────────────────────────────

def test_not_configured_by_default(monkeypatch):
    monkeypatch.delenv("CODEX_IO_API_KEY", raising=False)
    assert codex.codex_configured() is False


def test_configured_when_key_present(monkeypatch):
    monkeypatch.setenv("CODEX_IO_API_KEY", "fake-key-value")
    assert codex.codex_configured() is True


# ── get_ohlcv gating -- never a real network call when it can't succeed ────

@pytest.mark.asyncio
async def test_get_ohlcv_skips_network_when_not_configured(monkeypatch):
    monkeypatch.delenv("CODEX_IO_API_KEY", raising=False)
    fake = _patch_client(monkeypatch, [])
    result = await codex.get_ohlcv(POOL, network="base")
    assert result.available is False
    assert fake.calls == []


@pytest.mark.asyncio
async def test_get_ohlcv_rejects_unmapped_chain(monkeypatch):
    monkeypatch.setenv("CODEX_IO_API_KEY", "fake-key-value")
    fake = _patch_client(monkeypatch, [])
    result = await codex.get_ohlcv(POOL, network="solana")
    assert result.available is False
    assert fake.calls == []


@pytest.mark.asyncio
async def test_get_ohlcv_skips_network_when_monthly_cap_reached(monkeypatch):
    monkeypatch.setenv("CODEX_IO_API_KEY", "fake-key-value")

    async def _cap_reached():
        return True

    monkeypatch.setattr(codex, "_monthly_cap_reached", _cap_reached)
    fake = _patch_client(monkeypatch, [])
    result = await codex.get_ohlcv(POOL, network="base")
    assert result.available is False
    assert fake.calls == []


# ── get_ohlcv success path ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_ohlcv_succeeds_on_first_rung(monkeypatch):
    monkeypatch.setenv("CODEX_IO_API_KEY", "fake-key-value")
    _patch_client(monkeypatch, FakeResponse(200, _bars_payload(30)))
    result = await codex.get_ohlcv(POOL, network="base")
    assert result.available is True
    assert len(result.candles) == 30
    assert result.candles[0].ts < result.candles[-1].ts


@pytest.mark.asyncio
async def test_get_ohlcv_uses_network_id_and_symbol_format(monkeypatch):
    monkeypatch.setenv("CODEX_IO_API_KEY", "fake-key-value")
    fake = _patch_client(monkeypatch, FakeResponse(200, _bars_payload(30)))
    await codex.get_ohlcv(POOL, network="base")
    variables = fake.calls[0]["json"]["variables"]
    assert variables["symbol"] == f"{POOL}:8453"


@pytest.mark.asyncio
async def test_get_ohlcv_sends_raw_api_key_in_authorization_header(monkeypatch):
    monkeypatch.setenv("CODEX_IO_API_KEY", "fake-key-value")
    fake = _patch_client(monkeypatch, FakeResponse(200, _bars_payload(30)))
    await codex.get_ohlcv(POOL, network="base")
    assert fake.calls[0]["headers"]["Authorization"] == "fake-key-value"


@pytest.mark.asyncio
async def test_get_ohlcv_falls_through_ladder_when_first_rung_too_thin(monkeypatch):
    monkeypatch.setenv("CODEX_IO_API_KEY", "fake-key-value")
    _patch_client(
        monkeypatch,
        [FakeResponse(200, _bars_payload(3)), FakeResponse(200, _bars_payload(25))],
    )
    result = await codex.get_ohlcv(POOL, network="base")
    assert result.available is True
    assert len(result.candles) == 25


@pytest.mark.asyncio
async def test_get_ohlcv_returns_best_partial_if_every_rung_stays_thin(monkeypatch):
    monkeypatch.setenv("CODEX_IO_API_KEY", "fake-key-value")
    _patch_client(
        monkeypatch,
        [FakeResponse(200, _bars_payload(2)), FakeResponse(200, _bars_payload(5)), FakeResponse(200, _bars_payload(1))],
    )
    result = await codex.get_ohlcv(POOL, network="base")
    assert result.available is True
    assert len(result.candles) == 5


@pytest.mark.asyncio
async def test_get_ohlcv_unavailable_when_every_rung_empty(monkeypatch):
    monkeypatch.setenv("CODEX_IO_API_KEY", "fake-key-value")
    empty = {"data": {"getBars": {"o": [], "h": [], "l": [], "c": [], "volume": [], "t": []}}}
    _patch_client(monkeypatch, [FakeResponse(200, empty)] * 3)
    result = await codex.get_ohlcv(POOL, network="base")
    assert result.available is False


# ── GraphQL-level errors (HTTP 200 but the query itself failed) ─────────────

@pytest.mark.asyncio
async def test_get_ohlcv_treats_graphql_errors_as_failure(monkeypatch):
    monkeypatch.setenv("CODEX_IO_API_KEY", "fake-key-value")
    payload = {"errors": [{"message": "invalid symbol"}]}
    _patch_client(monkeypatch, [FakeResponse(200, payload)] * 3)
    result = await codex.get_ohlcv(POOL, network="base")
    assert result.available is False


# ── dome retry policy ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_ohlcv_retries_429_three_times_then_gives_up(monkeypatch):
    monkeypatch.setenv("CODEX_IO_API_KEY", "fake-key-value")
    _patch_no_sleep(monkeypatch)
    fake = _patch_client(monkeypatch, [FakeResponse(429)] * 9)  # 3 rungs x 3 attempts
    result = await codex.get_ohlcv(POOL, network="base")
    assert result.available is False
    assert len(fake.calls) == 9


@pytest.mark.asyncio
async def test_get_ohlcv_recovers_from_a_single_429(monkeypatch):
    monkeypatch.setenv("CODEX_IO_API_KEY", "fake-key-value")
    _patch_no_sleep(monkeypatch)
    _patch_client(monkeypatch, [FakeResponse(429), FakeResponse(200, _bars_payload(30))])
    result = await codex.get_ohlcv(POOL, network="base")
    assert result.available is True
    assert len(result.candles) == 30


# ── _parse_bars -- parallel-array shape, never a list of row objects ───────

def test_parse_bars_builds_candles_from_parallel_arrays():
    data = _bars_payload(5)["data"]
    candles = codex._parse_bars(data)
    assert len(candles) == 5
    assert candles[0].open == 1.0
    assert candles[0].high == 2.0
    assert candles[0].low == 0.5
    assert candles[0].close == 1.5
    assert candles[0].volume == 10.0


def test_parse_bars_skips_null_entries():
    data = _bars_payload(3)["data"]
    data["getBars"]["c"][1] = None
    candles = codex._parse_bars(data)
    assert len(candles) == 2


def test_parse_bars_rejects_mismatched_array_lengths():
    data = _bars_payload(3)["data"]
    data["getBars"]["o"].append(999.0)  # now length 4 vs 3 everywhere else
    assert codex._parse_bars(data) == []


def test_parse_bars_empty_on_malformed_shape():
    assert codex._parse_bars({"getBars": "not-a-dict"}) == []
    assert codex._parse_bars({}) == []
    assert codex._parse_bars("not-a-dict-at-all") == []


# ── monthly request counter -- real DB round-trip, isolated per test ───────

@pytest.mark.asyncio
async def test_monthly_counter_starts_at_zero():
    assert await codex._requests_this_month() == 0


@pytest.mark.asyncio
async def test_monthly_counter_increments_on_record():
    await codex._record_request()
    await codex._record_request()
    assert await codex._requests_this_month() == 2


@pytest.mark.asyncio
async def test_monthly_cap_not_reached_below_threshold():
    await codex._record_request()
    assert await codex._monthly_cap_reached() is False


@pytest.mark.asyncio
async def test_monthly_cap_reached_at_threshold(monkeypatch):
    monkeypatch.setattr(codex, "_MONTHLY_REQUEST_CAP", 2)
    await codex._record_request()
    await codex._record_request()
    assert await codex._monthly_cap_reached() is True


@pytest.mark.asyncio
async def test_successful_call_records_a_request(monkeypatch):
    monkeypatch.setenv("CODEX_IO_API_KEY", "fake-key-value")
    _patch_client(monkeypatch, FakeResponse(200, _bars_payload(30)))
    await codex.get_ohlcv(POOL, network="base")
    assert await codex._requests_this_month() == 1
