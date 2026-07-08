"""Tests du client OHLCV GeckoTerminal (lecture seule) — aucun appel réseau réel.

Vérifie : parsing robuste (lignes malformées ignorées), l'échelle de repli
1D → 4H → 1H, la dégradation gracieuse (aucune bougie inventée), et le tri
chronologique. Motif de mock identique à test_coingecko_client.py (FakeClient).
"""

import pytest

from aria_core.services.ohlcv import (
    DEFAULT_NETWORK,
    OHLCVClient,
    _parse_candles,
)

POOL = "0x" + "ab" * 20


def _rows(n: int, *, start_ts: int = 1_000) -> list[list[float]]:
    """n bougies [ts, open, high, low, close, volume] cohérentes et croissantes."""
    out = []
    for i in range(n):
        base = 100.0 + i
        out.append([start_ts + i * 3600, base, base + 2, base - 2, base + 1, 10.0 + i])
    return out


def _payload(rows: list) -> dict:
    return {"data": {"attributes": {"ohlcv_list": rows}}}


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
        self._responses = responses

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def get(self, url, params=None, headers=None):
        queue = self._responses[url]
        if isinstance(queue, list):
            return queue.pop(0)
        return queue


def _patch(monkeypatch, responses: dict):
    monkeypatch.setattr(
        "aria_core.services.ohlcv.httpx.AsyncClient",
        lambda **kw: FakeClient(responses),
    )

    async def _no_sleep(_):
        return None

    monkeypatch.setattr("aria_core.services.ohlcv.asyncio.sleep", _no_sleep)


def _url(period: str, base: str = "https://gt.test") -> str:
    return f"{base}/networks/{DEFAULT_NETWORK}/pools/{POOL}/ohlcv/{period}"


# ── parsing pur ───────────────────────────────────────────────────────────────

def test_parse_candles_ignores_malformed():
    payload = _payload([
        [1000, 1, 2, 0.5, 1.5, 10],   # ok
        [1001, "x", 2, 1, 2, 5],       # open non numérique -> ignoré
        [1002, 2, 3],                  # trop court -> ignoré
        "pas une ligne",               # type invalide -> ignoré
        [999, 3, 4, 2, 3, 7],          # ok, plus ancien -> doit être trié devant
    ])
    candles = _parse_candles(payload)
    assert len(candles) == 2
    assert [c.ts for c in candles] == [999, 1000]  # tri chronologique


def test_parse_candles_empty_shapes():
    assert _parse_candles({}) == []
    assert _parse_candles({"data": {"attributes": {}}}) == []
    assert _parse_candles("bogus") == []


# ── échelle de repli & dégradation ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_daily_enough_candles_stops_ladder(monkeypatch):
    client = OHLCVClient(base_url="https://gt.test", min_interval=0.0)
    _patch(monkeypatch, {_url("day"): FakeResponse(200, _payload(_rows(40)))})
    res = await client.get_ohlcv(POOL)
    assert res.available is True
    assert res.timeframe == "1D"
    assert len(res.candles) == 40
    assert res.error is None


@pytest.mark.asyncio
async def test_falls_back_to_hourly_when_daily_thin(monkeypatch):
    client = OHLCVClient(base_url="https://gt.test", min_interval=0.0)
    # 1D vide -> on descend ; 4H (period=hour) fournit assez de bougies.
    _patch(
        monkeypatch,
        {
            _url("day"): FakeResponse(200, _payload([])),
            _url("hour"): FakeResponse(200, _payload(_rows(30))),
        },
    )
    res = await client.get_ohlcv(POOL)
    assert res.available is True
    assert res.timeframe == "4H"
    assert len(res.candles) == 30


@pytest.mark.asyncio
async def test_all_unavailable_is_graceful(monkeypatch):
    client = OHLCVClient(base_url="https://gt.test", min_interval=0.0)
    # day (1x) + hour (2x : 4H puis 1H) tous en 404.
    _patch(
        monkeypatch,
        {
            _url("day"): FakeResponse(404),
            _url("hour"): [FakeResponse(404), FakeResponse(404)],
        },
    )
    res = await client.get_ohlcv(POOL)
    assert res.available is False
    assert res.candles == []
    assert res.error  # message explicite, jamais une bougie inventée


@pytest.mark.asyncio
async def test_empty_pool_address():
    client = OHLCVClient(base_url="https://gt.test", min_interval=0.0)
    res = await client.get_ohlcv("   ")
    assert res.available is False
    assert res.error
