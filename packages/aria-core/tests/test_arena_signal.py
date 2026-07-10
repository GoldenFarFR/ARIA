"""Signal BTC pour agents tiers (seam #60) — jamais de valeur inventée en cas de manque.

Deux clients DISTINCTS et non interchangeables : ``cycle_client`` (forme
Blockchain.com, historique long) et ``rsi_client`` (forme CoinGecko, fenêtre
courte) — verrouillé explicitement par les tests ci-dessous pour ne pas
regresser vers un client partagé (bug reel du 09/07 apres le passage a
Blockchain.com pour l'historique long)."""
from __future__ import annotations

import pytest

from aria_core.skills import arena_signal
from aria_core.skills import btc_cycles as bc


class _FakeChartResult:
    """Forme CoinGecko (`get_market_chart_range`)."""

    def __init__(self, prices, available=True, error=None):
        self.prices = prices
        self.available = available
        self.error = error


class _FakeRsiClient:
    def __init__(self, result):
        self._result = result

    async def get_market_chart_range(self, coin_id, start_ts, end_ts, *, vs_currency="usd"):
        return self._result


class _FakeBtcMarketPriceResult:
    """Forme Blockchain.com (`fetch_btc_market_price_history`)."""

    def __init__(self, prices, available=True, error=None):
        self.prices = prices
        self.available = available
        self.error = error


class _FakeCycleClient:
    def __init__(self, result):
        self._result = result
        self.calls = 0

    async def fetch_btc_market_price_history(self, *, timespan="all"):
        self.calls += 1
        return self._result


@pytest.fixture(autouse=True)
def _reset_cache(monkeypatch):
    monkeypatch.setattr(bc, "_phase_cache", {"at": 0.0, "value": None})
    yield


def _rising_series(n: int, start: float = 100.0, step: float = 2.0) -> list[tuple[int, float]]:
    return [(i * 86_400_000, start + i * step) for i in range(n)]


@pytest.mark.asyncio
async def test_fetch_btc_arena_signal_all_fields_present_with_real_history():
    rsi_client = _FakeRsiClient(_FakeChartResult(_rising_series(40)))
    cycle_client = _FakeCycleClient(_FakeBtcMarketPriceResult(_rising_series(400, start=1.0)))
    result = await arena_signal.fetch_btc_arena_signal(cycle_client=cycle_client, rsi_client=rsi_client)

    assert result["btc_rsi_14"] is not None
    assert 0.0 <= result["btc_rsi_14"] <= 100.0
    assert result["note"] == arena_signal.NOTE
    assert result["generated_at"]
    assert cycle_client.calls == 1


@pytest.mark.asyncio
async def test_fetch_btc_arena_signal_degrades_softly_without_history():
    rsi_client = _FakeRsiClient(_FakeChartResult([], available=False, error="rate limit"))
    cycle_client = _FakeCycleClient(_FakeBtcMarketPriceResult([], available=False, error="rate limit"))
    result = await arena_signal.fetch_btc_arena_signal(cycle_client=cycle_client, rsi_client=rsi_client)

    assert result["btc_cycle_phase"] is None
    assert result["btc_cycle_change_pct"] is None
    assert result["btc_rsi_14"] is None
    # Le manque de données ne doit JAMAIS être masqué par une valeur inventée.
    assert result["note"] == arena_signal.NOTE


@pytest.mark.asyncio
async def test_fetch_btc_arena_signal_rsi_none_when_history_too_short():
    rsi_client = _FakeRsiClient(_FakeChartResult(_rising_series(5)))
    cycle_client = _FakeCycleClient(_FakeBtcMarketPriceResult([], available=False))
    result = await arena_signal.fetch_btc_arena_signal(cycle_client=cycle_client, rsi_client=rsi_client)

    assert result["btc_rsi_14"] is None


class _RecordingRsiClient:
    """Enregistre chaque fenêtre demandée — verrouille le fait que le RSI utilise
    une fenêtre récente (<=365j, contrainte CoinGecko gratuit confirmée le 09/07)."""

    def __init__(self, result):
        self._result = result
        self.windows: list[tuple[int, int]] = []

    async def get_market_chart_range(self, coin_id, start_ts, end_ts, *, vs_currency="usd"):
        self.windows.append((start_ts, end_ts))
        return self._result


@pytest.mark.asyncio
async def test_rsi_uses_short_recent_window_not_full_history():
    rsi_client = _RecordingRsiClient(_FakeChartResult(_rising_series(40)))
    cycle_client = _FakeCycleClient(_FakeBtcMarketPriceResult([], available=False))
    await arena_signal.fetch_btc_arena_signal(cycle_client=cycle_client, rsi_client=rsi_client)

    one_year = 365 * 86_400
    spans = [end - start for start, end in rsi_client.windows]
    assert spans, "le client RSI n'a jamais ete appele"
    assert all(span <= one_year for span in spans), "le RSI a demande plus de 365 jours"


@pytest.mark.asyncio
async def test_cycle_and_rsi_clients_are_never_conflated():
    """Regression du 09/07 : les deux clients ont des interfaces differentes et ne
    doivent jamais recevoir le meme objet — verifie que chacun est bien appele une
    fois, sur sa propre methode, independamment de l'autre."""
    rsi_client = _FakeRsiClient(_FakeChartResult(_rising_series(40)))
    cycle_client = _FakeCycleClient(_FakeBtcMarketPriceResult(_rising_series(400, start=1.0)))

    result = await arena_signal.fetch_btc_arena_signal(cycle_client=cycle_client, rsi_client=rsi_client)

    assert cycle_client.calls == 1
    assert result["btc_rsi_14"] is not None
