"""Tests du client DexPaprika (lecture seule, dernier maillon de la cascade
OHLCV, Item #130, 26/07) -- aucun appel réseau réel, tout est mocké."""

from __future__ import annotations

import httpx
import pytest

from aria_core.services import dexpaprika as dp

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
    def __init__(self, responses: dict):
        self._responses = responses

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, params=None, **kwargs):
        queue = self._responses[url]
        if isinstance(queue, list):
            return queue.pop(0)
        return queue


def _patch_client(monkeypatch, responses: dict):
    monkeypatch.setattr("aria_core.services.dexpaprika.httpx.AsyncClient", lambda **kw: FakeClient(responses))


def _patch_no_sleep(monkeypatch):
    async def _fake_sleep(_seconds):
        return None

    monkeypatch.setattr("aria_core.services.dexpaprika.asyncio.sleep", _fake_sleep)


def _row(time_open: str, o: float, h: float, low: float, c: float, v: float = 10.0) -> dict:
    return {"time_open": time_open, "time_close": time_open, "open": o, "high": h, "low": low, "close": c, "volume": v}


def _rows(n: int) -> list[dict]:
    return [_row(f"2026-07-{(i % 28) + 1:02d}T00:00:00Z", 1.0 + i, 2.0 + i, 0.5 + i, 1.5 + i) for i in range(n)]


@pytest.fixture(autouse=True)
def _reset_circuit_breaker():
    """Same trap as every other dome client's tests -- module-level throttle
    timer shared across tests in this file (harmless here since ``_MIN_INTERVAL``
    is only a proactive delay, never a hard state to reset)."""
    dp._key_marked_invalid = False
    yield
    dp._key_marked_invalid = False


# ── _compute_start -- the ONE thing that must never regress (26/07 diligence:
#    a malformed start date is never rejected by DexPaprika, it silently
#    serves the wrong window) ────────────────────────────────────────────────

def test_compute_start_returns_a_valid_iso_datetime():
    start = dp._compute_start("1h", 120)
    # must parse as a real datetime -- this is the whole point of the guardrail
    from datetime import datetime

    parsed = datetime.strptime(start, "%Y-%m-%dT%H:%M:%SZ")
    assert parsed.year >= 2020


def test_compute_start_carries_full_second_precision():
    """17/08 -- real bug found live: a date-ONLY start (midnight, no time-
    of-day) introduced up to ~24h of slack that could push the computed
    [start, start+window] range entirely into the past without ever
    reaching "now", silently missing all recent activity depending on what
    hour of day the call happened to run at (isolated by comparing date-only
    vs full-ISO at the identical lookback distance against the real API --
    only the date-only form returned zero candles for an actively-traded
    pool). The guardrail: never regress back to a bare date."""
    start = dp._compute_start("5m", 240)
    assert "T" in start and start.endswith("Z")
    from datetime import datetime

    parsed = datetime.strptime(start, "%Y-%m-%dT%H:%M:%SZ")
    assert (parsed.hour, parsed.minute, parsed.second) != (0, 0, 0)


def test_compute_start_goes_further_back_for_coarser_intervals():
    start_fine = dp._compute_start("1m", 120)
    start_coarse = dp._compute_start("24h", 120)
    assert start_coarse < start_fine  # coarser interval -> further in the past


# ── _parse_candles ───────────────────────────────────────────────────────────

def test_parse_candles_real_shape():
    rows = [_row("2026-07-01T00:00:00Z", 1.0, 1.2, 0.9, 1.1, 42.0)]
    candles = dp._parse_candles(rows)
    assert len(candles) == 1
    c = candles[0]
    assert c.open == 1.0 and c.high == 1.2 and c.low == 0.9 and c.close == 1.1 and c.volume == 42.0


def test_parse_candles_ignores_malformed_rows():
    rows = [
        _row("2026-07-01T00:00:00Z", 1.0, 1.2, 0.9, 1.1),
        {"time_open": "not-a-date", "open": 1, "high": 1, "low": 1, "close": 1},
        {"time_open": "2026-07-02T00:00:00Z", "open": "x", "high": 1, "low": 1, "close": 1},
        "not even a dict",
    ]
    candles = dp._parse_candles(rows)
    assert len(candles) == 1


def test_parse_candles_empty_shapes():
    assert dp._parse_candles({}) == []
    assert dp._parse_candles("bogus") == []
    assert dp._parse_candles([]) == []


# ── get_ohlcv -- standard ladder (24h -> 6h -> 1h) ──────────────────────────

@pytest.mark.asyncio
async def test_get_ohlcv_standard_uses_24h_when_enough_candles(monkeypatch):
    url = f"https://api.dexpaprika.com/networks/base/pools/{POOL}/ohlcv"
    _patch_client(monkeypatch, {url: FakeResponse(200, _rows(30))})
    _patch_no_sleep(monkeypatch)

    result = await dp.get_ohlcv(POOL, network="base")

    assert result.available is True
    assert len(result.candles) == 30
    assert result.error is None


@pytest.mark.asyncio
async def test_get_ohlcv_standard_falls_back_to_6h_when_24h_thin(monkeypatch):
    url = f"https://api.dexpaprika.com/networks/base/pools/{POOL}/ohlcv"
    _patch_client(monkeypatch, {url: [FakeResponse(200, _rows(2)), FakeResponse(200, _rows(25))]})
    _patch_no_sleep(monkeypatch)

    result = await dp.get_ohlcv(POOL, network="base")

    assert result.available is True
    assert len(result.candles) == 25


@pytest.mark.asyncio
async def test_get_ohlcv_standard_all_empty_returns_unavailable(monkeypatch):
    url = f"https://api.dexpaprika.com/networks/base/pools/{POOL}/ohlcv"
    _patch_client(monkeypatch, {url: FakeResponse(200, [])})
    _patch_no_sleep(monkeypatch)

    result = await dp.get_ohlcv(POOL, network="base")

    assert result.available is False
    assert result.candles == []
    assert result.error


@pytest.mark.asyncio
async def test_get_ohlcv_standard_keeps_best_thin_result_across_ladder(monkeypatch):
    """None of the 3 rungs reach _MIN_USEFUL_CANDLES -- the richest thin
    result must still be returned (available=True), never discarded."""
    url = f"https://api.dexpaprika.com/networks/base/pools/{POOL}/ohlcv"
    _patch_client(
        monkeypatch,
        {url: [FakeResponse(200, _rows(3)), FakeResponse(200, _rows(8)), FakeResponse(200, _rows(5))]},
    )
    _patch_no_sleep(monkeypatch)

    result = await dp.get_ohlcv(POOL, network="base")

    assert result.available is True
    assert len(result.candles) == 8  # the richest of the 3 thin rungs


# ── get_ohlcv -- scalping ladder (15m -> 30m) ───────────────────────────────

@pytest.mark.asyncio
async def test_get_ohlcv_scalping_uses_15m_when_available(monkeypatch):
    url = f"https://api.dexpaprika.com/networks/base/pools/{POOL}/ohlcv"
    _patch_client(monkeypatch, {url: FakeResponse(200, _rows(120))})
    _patch_no_sleep(monkeypatch)

    result = await dp.get_ohlcv(POOL, network="base", mode="scalping")

    assert result.available is True
    assert len(result.candles) == 120


@pytest.mark.asyncio
async def test_get_ohlcv_scalping_falls_back_to_30m_when_15m_empty(monkeypatch):
    url = f"https://api.dexpaprika.com/networks/base/pools/{POOL}/ohlcv"
    _patch_client(monkeypatch, {url: [FakeResponse(200, []), FakeResponse(200, _rows(50))]})
    _patch_no_sleep(monkeypatch)

    result = await dp.get_ohlcv(POOL, network="base", mode="scalping")

    assert result.available is True
    assert len(result.candles) == 50


@pytest.mark.asyncio
async def test_get_ohlcv_scalping_never_falls_back_to_standard_ladder(monkeypatch):
    """If both 15m and 30m fail, this must NEVER escalate to 24h/6h/1h --
    corrupting a scalping RSI read with day-scale candles."""
    url = f"https://api.dexpaprika.com/networks/base/pools/{POOL}/ohlcv"
    _patch_client(monkeypatch, {url: [FakeResponse(200, []), FakeResponse(200, [])]})
    _patch_no_sleep(monkeypatch)

    result = await dp.get_ohlcv(POOL, network="base", mode="scalping")

    assert result.available is False
    assert result.candles == []


# ── dome error policy (429/5xx/timeout) ─────────────────────────────────────

@pytest.mark.asyncio
async def test_get_ohlcv_degrades_gracefully_on_rate_limit(monkeypatch):
    """A confirmed rate limit on EVERY ladder rung must degrade honestly
    (never raise, never fabricate a candle) -- the precise cause is logged
    per-rung (see captured warnings), the final result stays a simple
    "no candle" verdict, same as an empty response."""
    url = f"https://api.dexpaprika.com/networks/base/pools/{POOL}/ohlcv"
    _patch_client(monkeypatch, {url: [FakeResponse(429)] * 9})  # 3 attempts x 3 ladder rungs
    _patch_no_sleep(monkeypatch)

    result = await dp.get_ohlcv(POOL, network="base")

    assert result.available is False
    assert result.candles == []
    assert result.error


@pytest.mark.asyncio
async def test_get_ohlcv_degrades_gracefully_on_invalid_interval_error(monkeypatch):
    """A real error response (400) must never raise -- graceful degradation,
    same as every other dome client."""
    url = f"https://api.dexpaprika.com/networks/base/pools/{POOL}/ohlcv"
    _patch_client(monkeypatch, {url: FakeResponse(400, {"message": "invalid query param"})})
    _patch_no_sleep(monkeypatch)

    result = await dp.get_ohlcv(POOL, network="base")

    assert result.available is False
    assert result.candles == []


class TestAuthHeaders:
    """04/08 -- optional free-tier key, read from the environment only.
    Uses a throwaway value, never a real key."""

    def test_no_key_env_var_returns_empty_headers(self, monkeypatch):
        monkeypatch.delenv("DEXPAPRIKA_API_KEY", raising=False)
        assert dp._auth_headers() == {}

    def test_key_present_returns_raw_authorization_header(self, monkeypatch):
        monkeypatch.setenv("DEXPAPRIKA_API_KEY", "test-key-123")
        assert dp._auth_headers() == {"Authorization": "test-key-123"}

    def test_key_marked_invalid_returns_empty_headers_even_if_env_var_present(self, monkeypatch):
        monkeypatch.setenv("DEXPAPRIKA_API_KEY", "test-key-123")
        dp._key_marked_invalid = True
        assert dp._auth_headers() == {}


class TestInvalidKeyFallback:
    """05/08 -- a configured key rejected with 401 must fall back to keyless
    access instead of degrading the whole tier (same anti-pattern as the
    07/20 Blockscout fix)."""

    @pytest.mark.asyncio
    async def test_401_falls_back_to_keyless_and_succeeds(self, monkeypatch):
        monkeypatch.setenv("DEXPAPRIKA_API_KEY", "invalid-key")
        url = f"https://api.dexpaprika.com/networks/base/pools/{POOL}/ohlcv"
        _patch_client(monkeypatch, {url: [FakeResponse(401), FakeResponse(200, _rows(30))]})
        _patch_no_sleep(monkeypatch)

        result = await dp.get_ohlcv(POOL, network="base")

        assert result.available is True
        assert len(result.candles) == 30

    @pytest.mark.asyncio
    async def test_401_marks_key_invalid_for_subsequent_calls(self, monkeypatch):
        monkeypatch.setenv("DEXPAPRIKA_API_KEY", "invalid-key")
        url = f"https://api.dexpaprika.com/networks/base/pools/{POOL}/ohlcv"
        _patch_client(monkeypatch, {url: [FakeResponse(401), FakeResponse(200, _rows(30))]})
        _patch_no_sleep(monkeypatch)

        await dp.get_ohlcv(POOL, network="base")

        assert dp._key_marked_invalid is True
        assert dp._auth_headers() == {}
