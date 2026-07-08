"""Cycles Bitcoin — segmentation déterministe (pure) + analyse (hors-ligne, injectée)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from aria_core.skills import btc_cycles as bc

SYNTHETIC_HALVINGS = (
    ("cycle A", "2020-01-01"),
    ("cycle B", "2021-01-01"),
)


@pytest.fixture(autouse=True)
def _synthetic_halvings(monkeypatch):
    monkeypatch.setattr(bc, "HALVING_DATES", SYNTHETIC_HALVINGS)
    yield


def _ts(date_str: str) -> int:
    return int(datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()) * 1000


def test_segment_cycles_empty_on_insufficient_data():
    assert bc.segment_cycles([]) == []
    assert bc.segment_cycles([(_ts("2020-01-01"), 100.0)]) == []


def test_segment_cycles_identifies_four_phases():
    prices = [
        (_ts("2020-01-01"), 100.0),   # plus bas -> debut d'accumulation
        (_ts("2020-02-01"), 110.0),   # encore en accumulation (< +30%)
        (_ts("2020-03-01"), 135.0),   # franchit +30% -> fin d'accumulation / debut hausse
        (_ts("2020-06-01"), 300.0),   # plus haut du cycle
        (_ts("2020-06-15"), 285.0),   # encore dans la bande de distribution (>=90% du haut)
        (_ts("2020-07-01"), 200.0),   # baisse
        (_ts("2020-12-31"), 150.0),   # fin de fenetre (juste avant le halving suivant)
    ]
    stats = bc.segment_cycles(prices)
    assert len(stats) == 1  # aucune donnee dans la fenetre "cycle B" -> ignoree
    cycle = stats[0]
    assert cycle.name == "cycle A"
    assert cycle.low_price == 100.0 and cycle.low_date == "2020-01-01"
    assert cycle.high_price == 300.0 and cycle.high_date == "2020-06-01"
    assert cycle.gain_low_to_high_pct == pytest.approx(200.0)

    labels = [p.label for p in cycle.phases]
    assert labels == ["accumulation", "hausse (markup)", "distribution", "baisse (markdown)"]

    acc, markup, dist, markdown = cycle.phases
    assert acc.start_price == 100.0 and acc.end_price == 135.0
    assert markup.start_price == 135.0 and markup.end_price == 300.0
    assert dist.start_date == "2020-06-01" and dist.end_date == "2020-06-15"
    assert markdown.start_date == "2020-06-15" and markdown.end_date == "2020-12-31"


def test_segment_cycles_never_fabricates_values():
    """Les seuils (ACCUMULATION_EXIT_GAIN, DISTRIBUTION_BAND) sont deterministes : une
    serie plate (aucun mouvement) ne doit produire aucun pourcentage invente : le prix
    bas/haut/fin restent 100.0 partout, gain a 0% (jamais de +30%/-X% fabrique)."""
    prices = [(_ts("2020-01-01"), 100.0), (_ts("2020-06-01"), 100.0), (_ts("2020-12-31"), 100.0)]
    stats = bc.segment_cycles(prices)
    assert len(stats) == 1
    assert stats[0].gain_low_to_high_pct == 0.0
    assert all(p.change_pct == 0.0 for p in stats[0].phases)


class _FakeChartResult:
    def __init__(self, prices, available=True, error=None):
        self.prices = prices
        self.available = available
        self.error = error


class _FakeClient:
    def __init__(self, result):
        self._result = result
        self.calls = []

    async def get_market_chart_range(self, coin_id, start_ts, end_ts, *, vs_currency="usd"):
        self.calls.append((coin_id, start_ts, end_ts, vs_currency))
        return self._result


@pytest.mark.asyncio
async def test_fetch_btc_history_returns_none_when_unavailable():
    client = _FakeClient(_FakeChartResult([], available=False, error="rate limit"))
    assert await bc.fetch_btc_history(client=client) is None


@pytest.mark.asyncio
async def test_analyze_btc_cycles_fails_closed_without_history():
    client = _FakeClient(_FakeChartResult([], available=False, error="rate limit"))
    result = await bc.analyze_btc_cycles(client=client)
    assert result["available"] is False
    assert "indisponible" in result["error"]


@pytest.mark.asyncio
async def test_analyze_btc_cycles_grounds_llm_prompt_in_real_numbers():
    prices = [
        (_ts("2020-01-01"), 100.0), (_ts("2020-03-01"), 135.0),
        (_ts("2020-06-01"), 300.0), (_ts("2020-12-31"), 150.0),
    ]
    client = _FakeClient(_FakeChartResult(prices))
    captured = {}

    async def fake_llm(prompt, system, max_tokens=900):
        captured["prompt"] = prompt
        captured["system"] = system
        return "Récit factuel ancré sur les chiffres fournis."

    result = await bc.analyze_btc_cycles(client=client, llm=fake_llm)
    assert result["available"] is True
    assert result["narrative"] == "Récit factuel ancré sur les chiffres fournis."
    assert "300" in captured["prompt"]  # les vrais chiffres calcules sont bien injectes au LLM
    assert "cadre de lecture" in captured["system"].lower() or "loi de marché" in captured["system"].lower()


@pytest.mark.asyncio
async def test_analyze_btc_cycles_llm_failure_keeps_raw_stats():
    prices = [(_ts("2020-01-01"), 100.0), (_ts("2020-06-01"), 300.0), (_ts("2020-12-31"), 150.0)]
    client = _FakeClient(_FakeChartResult(prices))

    async def broken_llm(prompt, system, max_tokens=900):
        return None

    result = await bc.analyze_btc_cycles(client=client, llm=broken_llm)
    assert result["available"] is True
    assert "indisponible" in result["narrative"]


def test_format_cycles_report_unavailable():
    report = bc.format_cycles_report({"available": False, "error": "historique BTC indisponible"})
    assert "indisponible" in report


def test_format_cycles_report_includes_disclaimer_and_numbers():
    stats = bc.CycleStats(
        name="cycle A", window_start="2020-01-01", window_end="2020-12-31",
        low_price=100.0, low_date="2020-01-01", high_price=300.0, high_date="2020-06-01",
        gain_low_to_high_pct=200.0, drawdown_high_to_end_pct=-50.0, phases=[],
    )
    report = bc.format_cycles_report({"available": True, "cycles": [stats], "narrative": "Récit."})
    assert "300" in report
    assert "pas une loi de marché" in report.lower()
