import pytest

from aria_core import capability_levels as cl
from aria_core import revenue_goals as rg


@pytest.fixture(autouse=True)
def isolated_progress(tmp_path, monkeypatch):
    path = tmp_path / "capability_progress.json"
    monkeypatch.setattr(cl, "PROGRESS_PATH", path)
    ledger = tmp_path / "revenue_ledger.json"
    monkeypatch.setattr(rg, "LEDGER_PATH", ledger)


def test_starts_at_zero():
    assert cl.global_index() == 0.0
    status = cl.full_status("fr")
    assert status["categories"]["codage"]["completed_level"] == 0
    assert status["categories"]["codage"]["next_level"] == 1


def test_level_one_objective_is_one_day():
    defn = cl.get_level_definition("codage", 1, "fr")
    assert "commit" in defn["objective"].lower()
    assert "jour" in defn["days"].lower() or "<" in defn["days"]


def test_complete_level_advances():
    result = cl.complete_level("codage", note="test")
    assert result["ok"]
    assert result["new_level"] == 1
    assert cl.global_index() == pytest.approx(1 / 6, abs=0.1)


def test_procedural_level_500_is_harder_than_13():
    early = cl.get_level_definition("business", 13, "fr")
    late = cl.get_level_definition("business", 500, "fr")
    assert cl._estimate_days_numeric(500) > cl._estimate_days_numeric(13)
    assert late["handcrafted"] is False
    assert early["handcrafted"] is False or early["level"] == 13


def test_business_level_no_longer_auto_promotes_on_revenue():
    """Aucun produit payant aujourd'hui : la rubrique business suit le barème du pacte
    (docs/protocole-argent-reel.md), pas un compteur de dollars -- plus de metric/target
    automatique, la promotion reste manuelle (complete_level)."""
    cl.complete_level("business", note="pact bar documented")
    rg.record_revenue(1.0, source="test", note="no longer wired to a level")
    state = cl.category_state("business", "fr")
    assert state["next_level"] == 2
    assert state["auto_ready"] is False
    events = cl.check_auto_completions()
    assert not any(e.get("category") == "business" for e in events)
    assert cl.load_progress()["categories"]["business"]["level"] == 1


def test_format_summary_mentions_axes():
    text = cl.format_summary("fr")
    assert "Indice ARIA" in text
    assert "Codage" in text
    assert "Business" in text