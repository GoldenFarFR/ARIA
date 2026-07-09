"""Signal BTC pour agents tiers (seam #60) — jamais de valeur inventée en cas de manque."""
from __future__ import annotations

import pytest

from aria_core.skills import arena_signal
from aria_core.skills import btc_cycles as bc


class _FakeChartResult:
    def __init__(self, prices, available=True, error=None):
        self.prices = prices
        self.available = available
        self.error = error


class _FakeClient:
    def __init__(self, result):
        self._result = result

    async def get_market_chart_range(self, coin_id, start_ts, end_ts, *, vs_currency="usd"):
        return self._result


@pytest.fixture(autouse=True)
def _reset_cache(monkeypatch):
    monkeypatch.setattr(bc, "_phase_cache", {"at": 0.0, "value": None})
    yield


def _rising_series(n: int, start: float = 100.0, step: float = 2.0) -> list[tuple[int, float]]:
    return [(i * 86_400_000, start + i * step) for i in range(n)]


@pytest.mark.asyncio
async def test_fetch_btc_arena_signal_all_fields_present_with_real_history():
    client = _FakeClient(_FakeChartResult(_rising_series(40)))
    result = await arena_signal.fetch_btc_arena_signal(client=client)

    assert result["btc_rsi_14"] is not None
    assert 0.0 <= result["btc_rsi_14"] <= 100.0
    assert result["note"] == arena_signal.NOTE
    assert result["generated_at"]


@pytest.mark.asyncio
async def test_fetch_btc_arena_signal_degrades_softly_without_history():
    client = _FakeClient(_FakeChartResult([], available=False, error="rate limit"))
    result = await arena_signal.fetch_btc_arena_signal(client=client)

    assert result["btc_cycle_phase"] is None
    assert result["btc_cycle_change_pct"] is None
    assert result["btc_rsi_14"] is None
    # Le manque de données ne doit JAMAIS être masqué par une valeur inventée.
    assert result["note"] == arena_signal.NOTE


@pytest.mark.asyncio
async def test_fetch_btc_arena_signal_rsi_none_when_history_too_short():
    client = _FakeClient(_FakeChartResult(_rising_series(5)))
    result = await arena_signal.fetch_btc_arena_signal(client=client)

    assert result["btc_rsi_14"] is None


class _RecordingClient:
    """Enregistre chaque fenêtre demandée — verrouille le fait que le RSI utilise
    une fenêtre récente (<=365j, contrainte CoinGecko gratuit confirmée le 09/07),
    distincte de l'historique complet 10 ans utilisé pour le cycle macro."""

    def __init__(self, result):
        self._result = result
        self.windows: list[tuple[int, int]] = []

    async def get_market_chart_range(self, coin_id, start_ts, end_ts, *, vs_currency="usd"):
        self.windows.append((start_ts, end_ts))
        return self._result


@pytest.mark.asyncio
async def test_rsi_uses_short_recent_window_not_full_history():
    client = _RecordingClient(_FakeChartResult(_rising_series(40)))
    await arena_signal.fetch_btc_arena_signal(client=client)

    spans = [end - start for start, end in client.windows]
    one_year = 365 * 86_400
    # Le fetch de cycle (btc_cycles) demande l'historique complet depuis 2015 -> bien > 1 an.
    assert any(span > one_year for span in spans), "aucun appel pour l'historique complet du cycle"
    # Le fetch RSI doit rester dans la fenêtre gratuite CoinGecko (<=365 jours).
    assert any(span <= one_year for span in spans), "aucun appel RSI en fenêtre courte"
