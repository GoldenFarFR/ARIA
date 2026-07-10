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
    monkeypatch.setattr(bc, "_phase_cache", {"at": 0.0, "value": None})
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

    async def fetch_btc_market_price_history(self, *, timespan="all"):
        self.calls.append(timespan)
        return self._result


@pytest.mark.asyncio
async def test_fetch_btc_history_returns_none_when_unavailable():
    client = _FakeClient(_FakeChartResult([], available=False, error="rate limit"))
    assert await bc.fetch_btc_history(client=client) is None


@pytest.mark.asyncio
async def test_fetch_btc_history_filters_out_points_before_history_start():
    # Blockchain.com renvoie l'historique depuis 2009 -- tout ce qui precede
    # HISTORY_START (2015-06-01, marge avant le premier halving) doit etre exclu.
    prices = [(_ts("2012-01-01"), 5.0), (_ts("2016-01-01"), 400.0), (_ts("2020-01-01"), 7000.0)]
    client = _FakeClient(_FakeChartResult(prices))
    result = await bc.fetch_btc_history(client=client)
    assert result == [(_ts("2016-01-01"), 400.0), (_ts("2020-01-01"), 7000.0)]


@pytest.mark.asyncio
async def test_fetch_btc_history_none_when_everything_filtered_out():
    prices = [(_ts("2010-01-01"), 1.0), (_ts("2012-01-01"), 5.0)]
    client = _FakeClient(_FakeChartResult(prices))
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


# ----------------------- phase actuelle compacte (overlay macro, tâche #14) -----------------------

def test_current_phase_summary_empty_without_stats_or_phases():
    assert bc.current_phase_summary([]) is None
    empty_phases = bc.CycleStats(
        name="cycle A", window_start="2020-01-01", window_end="2020-12-31",
        low_price=100.0, low_date="2020-01-01", high_price=300.0, high_date="2020-06-01",
        gain_low_to_high_pct=200.0, drawdown_high_to_end_pct=-50.0, phases=[],
    )
    assert bc.current_phase_summary([empty_phases]) is None


def test_current_phase_summary_returns_last_phase_of_last_cycle():
    phase1 = bc.CyclePhase(
        label="accumulation", start_date="2020-01-01", end_date="2020-03-01",
        start_price=100.0, end_price=135.0, change_pct=35.0,
    )
    phase2 = bc.CyclePhase(
        label="hausse (markup)", start_date="2020-03-01", end_date="2020-06-01",
        start_price=135.0, end_price=300.0, change_pct=122.0,
    )
    old_cycle = bc.CycleStats(
        name="cycle A", window_start="2019-01-01", window_end="2019-12-31",
        low_price=50.0, low_date="2019-01-01", high_price=80.0, high_date="2019-06-01",
        gain_low_to_high_pct=60.0, drawdown_high_to_end_pct=-10.0,
        phases=[bc.CyclePhase(label="baisse (markdown)", start_date="2019-06-01", end_date="2019-12-31",
                               start_price=80.0, end_price=70.0, change_pct=-12.5)],
    )
    current_cycle = bc.CycleStats(
        name="cycle en cours", window_start="2020-01-01", window_end="2020-12-31",
        low_price=100.0, low_date="2020-01-01", high_price=300.0, high_date="2020-06-01",
        gain_low_to_high_pct=200.0, drawdown_high_to_end_pct=-50.0, phases=[phase1, phase2],
    )
    summary = bc.current_phase_summary([old_cycle, current_cycle])
    assert summary == {
        "label": "hausse (markup)", "since": "2020-03-01", "change_pct": 122.0,
        "cycle_name": "cycle en cours",
    }


@pytest.mark.asyncio
async def test_fetch_current_macro_phase_none_without_history_or_cache():
    client = _FakeClient(_FakeChartResult([], available=False))
    assert await bc.fetch_current_macro_phase(client=client) is None


@pytest.mark.asyncio
async def test_fetch_current_macro_phase_uses_cache_without_refetch():
    prices = [(_ts("2020-01-01"), 100.0), (_ts("2020-06-01"), 300.0)]
    client = _FakeClient(_FakeChartResult(prices))

    first = await bc.fetch_current_macro_phase(client=client)
    assert first is not None
    assert len(client.calls) == 1

    second = await bc.fetch_current_macro_phase(client=client)
    assert second == first
    assert len(client.calls) == 1  # cache valide -> pas de second appel réseau


@pytest.mark.asyncio
async def test_fetch_current_macro_phase_degrades_softly_using_stale_cache_on_failure():
    prices = [(_ts("2020-01-01"), 100.0), (_ts("2020-03-01"), 135.0), (_ts("2020-06-01"), 300.0)]
    ok_client = _FakeClient(_FakeChartResult(prices))
    first = await bc.fetch_current_macro_phase(client=ok_client, force_refresh=True)
    assert first is not None

    broken_client = _FakeClient(_FakeChartResult([], available=False))
    second = await bc.fetch_current_macro_phase(client=broken_client, force_refresh=True)
    assert second == first  # dégradation douce : garde la dernière valeur connue, jamais None
