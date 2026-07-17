"""Journal de calibration (score Brier) -- aucune couverture jusqu'ici."""
from __future__ import annotations

import pytest

from aria_core.knowledge import calibration_ledger as cl


@pytest.fixture(autouse=True)
def _isolated_ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(cl, "LEDGER_PATH", tmp_path / "calibration_ledger.json")
    yield


def test_load_returns_empty_structure_when_file_absent():
    data = cl._load()
    assert data == {"predictions": [], "calibrations": [], "stats": {}}


def test_load_degrades_on_corrupt_json(tmp_path):
    cl.LEDGER_PATH.write_text("{not valid json", encoding="utf-8")
    data = cl._load()
    assert data == {"predictions": [], "calibrations": [], "stats": {}}


def test_record_prediction_persists_and_returns_id():
    entry_id = cl.record_prediction(
        "Est-ce que X est vrai ?", "Oui, probablement.", p_true=0.8, p_false=0.2,
        source="groq", skill="vc_analysis",
    )
    data = cl._load()
    assert len(data["predictions"]) == 1
    pred = data["predictions"][0]
    assert pred["id"] == entry_id
    assert pred["p_true"] == 0.8
    assert pred["resolved"] is None


def test_record_prediction_truncates_long_question_and_reply():
    long_q = "x" * 1000
    long_r = "y" * 2000
    cl.record_prediction(long_q, long_r)
    pred = cl._load()["predictions"][0]
    assert len(pred["question"]) == 500
    assert len(pred["reply"]) == 800


def test_record_prediction_caps_history_at_500():
    for i in range(505):
        cl.record_prediction(f"question {i}", "reply")
    data = cl._load()
    assert len(data["predictions"]) == 500
    # les plus ANCIENNES sont purgées, les plus récentes gardées
    assert data["predictions"][-1]["question"] == "question 504"


@pytest.mark.parametrize("verdict,expected", [
    ("vrai", "vrai"), ("VRAI", "vrai"), ("true", "vrai"),
    ("faux", "faux"), ("false", "faux"),
    ("incertain", "incertain"), ("uncertain", "incertain"),
    ("n'importe quoi", "incertain"),  # verdict inconnu -> dégrade honnêtement
])
def test_record_calibration_normalizes_verdict(verdict, expected):
    cal = cl.record_calibration("un claim", verdict)
    assert cal["verdict"] == expected


def test_record_calibration_actual_value_matches_verdict():
    assert cl.record_calibration("claim", "vrai")["actual"] == 1.0
    assert cl.record_calibration("claim", "faux")["actual"] == 0.0
    assert cl.record_calibration("claim", "incertain")["actual"] == 0.5


def test_record_calibration_computes_brier_score_against_prediction():
    pred_id = cl.record_prediction("Est-ce vrai ?", "oui", p_true=0.9)
    cal = cl.record_calibration("Est-ce vrai ?", "faux", prediction_id=pred_id)
    # Brier = (p - actual)^2 = (0.9 - 0.0)^2 = 0.81
    assert cal["brier"] == 0.81
    pred = cl._load()["predictions"][0]
    assert pred["resolved"] == "faux"
    assert pred["brier"] == 0.81


def test_record_calibration_perfect_prediction_zero_brier():
    pred_id = cl.record_prediction("Est-ce vrai ?", "oui", p_true=1.0)
    cal = cl.record_calibration("Est-ce vrai ?", "vrai", prediction_id=pred_id)
    assert cal["brier"] == 0.0


def test_record_calibration_without_prediction_id_no_brier():
    cal = cl.record_calibration("claim libre", "vrai")
    assert cal["brier"] is None
    assert cal["prediction_id"] is None


def test_record_calibration_unknown_prediction_id_no_crash():
    cal = cl.record_calibration("claim", "vrai", prediction_id="does-not-exist")
    assert cal["brier"] is None


def test_record_calibration_never_resolves_already_resolved_prediction_twice():
    pred_id = cl.record_prediction("Q", "R", p_true=0.5)
    cl.record_calibration("Q", "vrai", prediction_id=pred_id)
    second = cl.record_calibration("Q", "faux", prediction_id=pred_id)
    # deja "resolved" -> la deuxieme calibration ne retrouve pas la prediction resoluble
    assert second["brier"] is None


def test_record_calibration_caps_history_at_300():
    for i in range(305):
        cl.record_calibration(f"claim {i}", "vrai")
    data = cl._load()
    assert len(data["calibrations"]) == 300


def test_compute_stats_empty_ledger():
    stats = cl.compute_stats()
    assert stats["predictions"] == 0
    assert stats["avg_brier"] is None
    assert stats["reliability_hint"] == "pas encore mesuré"


def test_compute_stats_reliability_hint_excellent():
    pred_id = cl.record_prediction("Q", "R", p_true=1.0)
    cl.record_calibration("Q", "vrai", prediction_id=pred_id)  # brier = 0.0
    stats = cl.compute_stats()
    assert stats["reliability_hint"] == "excellent"


def test_compute_stats_reliability_hint_bon():
    # brier vise ~0.15 (entre 0.1 et 0.2) : p_true=0.6, actual=1.0 -> (0.6-1.0)^2 = 0.16
    pred_id = cl.record_prediction("Q", "R", p_true=0.6)
    cl.record_calibration("Q", "vrai", prediction_id=pred_id)
    stats = cl.compute_stats()
    assert stats["reliability_hint"] == "bon"


def test_compute_stats_reliability_hint_a_ameliorer():
    # p_true=0.1, actual=1.0 -> brier = 0.81 (>= 0.2)
    pred_id = cl.record_prediction("Q", "R", p_true=0.1)
    cl.record_calibration("Q", "vrai", prediction_id=pred_id)
    stats = cl.compute_stats()
    assert stats["reliability_hint"] == "à améliorer"


def test_get_uncertain_for_replay_filters_low_confidence_unresolved():
    cl.record_prediction("Certain vrai", "R", p_true=0.9)  # trop confiant -> exclu
    cl.record_prediction("Incertain", "R", p_true=0.4)  # candidat
    cl.record_prediction("Web verifie", "R", p_true=0.3, web_verified=True)  # deja verifie -> exclu

    candidates = cl.get_uncertain_for_replay()
    assert len(candidates) == 1
    assert candidates[0]["question"] == "Incertain"


def test_get_uncertain_for_replay_excludes_resolved():
    pred_id = cl.record_prediction("Incertain resolu", "R", p_true=0.3)
    cl.record_calibration("Incertain resolu", "vrai", prediction_id=pred_id)
    candidates = cl.get_uncertain_for_replay()
    assert candidates == []


def test_get_uncertain_for_replay_respects_limit():
    for i in range(10):
        cl.record_prediction(f"Q{i}", "R", p_true=0.2)
    candidates = cl.get_uncertain_for_replay(limit=3)
    assert len(candidates) == 3


def test_format_stats_summary_french_no_data():
    text = cl.format_stats_summary(lang="fr")
    assert "0 prédictions" in text
    assert "n/a" in text


def test_format_stats_summary_english_with_data():
    pred_id = cl.record_prediction("Q", "R", p_true=1.0)
    cl.record_calibration("Q", "vrai", prediction_id=pred_id)
    text = cl.format_stats_summary(lang="en")
    assert "1 predictions" in text
    assert "0.000" in text
    assert "excellent" in text
