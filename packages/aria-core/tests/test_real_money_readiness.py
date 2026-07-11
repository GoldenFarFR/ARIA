"""Scorecard « feu vert argent réel » (docs/protocole-argent-reel.md) — chaque case
doit rester honnête : ok seulement si calculé ET satisfait, jamais par optimisme."""
from __future__ import annotations

import pytest

from aria_core import vc_predictions
from aria_core.skills.real_money_readiness import (
    REQUIRED_SAMPLE_SIZE,
    REQUIRED_SPAN_DAYS,
    _hit_rate_credible_interval,
    compute_readiness_scorecard,
    format_readiness_report,
)


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(vc_predictions, "DB_PATH", str(tmp_path / "aria_test.db"))
    yield


async def _record_and_close(*, outcome_pct, recommandation="BUY", potentiel=7, created_at=None):
    pid = await vc_predictions.record_prediction(
        contract="0xabc", recommandation=recommandation, potentiel=potentiel,
        risque="MODÉRÉ", taille_pct=5.0, security_score=60, llm_used=True,
    )
    if created_at is not None:
        # Force la date de création pour simuler un étalement dans le temps (test uniquement).
        import aiosqlite

        async with aiosqlite.connect(vc_predictions.DB_PATH) as db:
            await db.execute(
                "UPDATE vc_prediction SET created_at = ? WHERE id = ?", (created_at, pid),
            )
            await db.commit()
    return await vc_predictions.close_prediction(pid, outcome_pct=outcome_pct)


@pytest.mark.asyncio
async def test_empty_journal_is_all_unknown_or_fail_never_ok_verdict():
    scorecard = await compute_readiness_scorecard()
    assert scorecard["all_ok"] is False
    assert scorecard["verdict"].startswith("NON")
    sample = next(c for c in scorecard["checks"] if c.id == "sample_size")
    assert sample.status == "fail"


@pytest.mark.asyncio
async def test_sample_size_fails_below_threshold():
    await _record_and_close(outcome_pct=10.0, created_at="2020-01-01T00:00:00+00:00")
    scorecard = await compute_readiness_scorecard()
    sample = next(c for c in scorecard["checks"] if c.id == "sample_size")
    assert sample.status == "fail"
    assert f"1/{REQUIRED_SAMPLE_SIZE}" in sample.detail


@pytest.mark.asyncio
async def test_sample_size_ok_when_count_and_span_both_satisfied():
    for i in range(REQUIRED_SAMPLE_SIZE):
        await _record_and_close(outcome_pct=5.0, created_at="2020-01-01T00:00:00+00:00")
    # Une seule prédiction plus récente pour créer l'étalement requis (span >= 180j).
    await _record_and_close(outcome_pct=5.0, created_at="2020-08-01T00:00:00+00:00")
    scorecard = await compute_readiness_scorecard()
    sample = next(c for c in scorecard["checks"] if c.id == "sample_size")
    assert sample.status == "ok"


@pytest.mark.asyncio
async def test_sample_size_fails_if_count_ok_but_span_too_short():
    for _ in range(REQUIRED_SAMPLE_SIZE + 1):
        await _record_and_close(outcome_pct=5.0, created_at="2020-01-01T00:00:00+00:00")
    scorecard = await compute_readiness_scorecard()
    sample = next(c for c in scorecard["checks"] if c.id == "sample_size")
    assert sample.status == "fail"
    assert "0/" in sample.detail.split(", ")[1]


@pytest.mark.asyncio
async def test_integrity_always_ok_structural_guarantee():
    scorecard = await compute_readiness_scorecard()
    integrity = next(c for c in scorecard["checks"] if c.id == "integrity")
    assert integrity.status == "ok"


@pytest.mark.asyncio
async def test_benchmark_lawyer_judge_are_always_unknown_never_fabricated():
    for i in range(200):
        await _record_and_close(outcome_pct=5.0, created_at="2020-01-01T00:00:00+00:00")
    scorecard = await compute_readiness_scorecard()
    for check_id in ("benchmark", "lawyer", "judge"):
        check = next(c for c in scorecard["checks"] if c.id == check_id)
        assert check.status == "unknown", f"{check_id} ne doit jamais être auto-validé"


@pytest.mark.asyncio
async def test_robustness_unknown_with_too_few_buys():
    await _record_and_close(outcome_pct=10.0)
    await _record_and_close(outcome_pct=-5.0)
    scorecard = await compute_readiness_scorecard()
    robustness = next(c for c in scorecard["checks"] if c.id == "robustness")
    assert robustness.status == "unknown"


@pytest.mark.asyncio
async def test_robustness_fails_when_average_turns_negative_without_top_2():
    # 2 gros gagnants qui portent tout le reste -> sans eux, la moyenne devient négative.
    await _record_and_close(outcome_pct=500.0)
    await _record_and_close(outcome_pct=300.0)
    await _record_and_close(outcome_pct=-10.0)
    await _record_and_close(outcome_pct=-8.0)
    scorecard = await compute_readiness_scorecard()
    robustness = next(c for c in scorecard["checks"] if c.id == "robustness")
    assert robustness.status == "fail"


@pytest.mark.asyncio
async def test_robustness_ok_when_still_positive_without_top_2():
    await _record_and_close(outcome_pct=50.0)
    await _record_and_close(outcome_pct=40.0)
    await _record_and_close(outcome_pct=10.0)
    await _record_and_close(outcome_pct=5.0)
    scorecard = await compute_readiness_scorecard()
    robustness = next(c for c in scorecard["checks"] if c.id == "robustness")
    assert robustness.status == "ok"


@pytest.mark.asyncio
async def test_calibration_unknown_without_enough_scored_buckets():
    await _record_and_close(outcome_pct=10.0, potentiel=None)
    scorecard = await compute_readiness_scorecard()
    calib = next(c for c in scorecard["checks"] if c.id == "calibration")
    assert calib.status == "unknown"


def test_credible_interval_defined_at_zero_buys_and_wide():
    # Aucune donnée -> retombe sur le prior Jeffreys Beta(0.5, 0.5), symétrique autour de
    # 50% et très large (contrairement à un intervalle fréquentiste, non défini à n=0).
    lo, hi = _hit_rate_credible_interval(0, 0)
    assert 0.0 < lo < 0.02
    assert 0.98 < hi < 1.0
    assert lo < 0.5 < hi


def test_credible_interval_one_win_out_of_one_still_wide():
    # 1/1 -> hit-rate observé 100%, mais l'intervalle doit rester large (pas de fausse
    # certitude sur un seul point de données) et ne pas coller à [100%, 100%].
    lo, hi = _hit_rate_credible_interval(1, 1)
    assert lo < 0.30
    assert hi > 0.95


def test_credible_interval_narrows_with_more_samples():
    # Même hit-rate observé (50%), mais l'intervalle doit se resserrer avec n croissant.
    lo_small, hi_small = _hit_rate_credible_interval(1, 2)
    lo_large, hi_large = _hit_rate_credible_interval(40, 80)
    assert (hi_large - lo_large) < (hi_small - lo_small)


@pytest.mark.asyncio
async def test_calibration_detail_includes_credible_interval_even_with_zero_buys():
    # Aucun BUY clôturé du tout -> la case reste "unknown" (logique de statut inchangée),
    # mais le detail doit quand même porter l'intervalle bayésien (0 BUY, prior seul).
    scorecard = await compute_readiness_scorecard()
    calib = next(c for c in scorecard["checks"] if c.id == "calibration")
    assert calib.status == "unknown"
    assert "intervalle de crédibilité" in calib.detail
    assert "sur 0 BUY" in calib.detail


@pytest.mark.asyncio
async def test_calibration_detail_includes_credible_interval_with_one_buy():
    await _record_and_close(outcome_pct=10.0, potentiel=None)
    scorecard = await compute_readiness_scorecard()
    calib = next(c for c in scorecard["checks"] if c.id == "calibration")
    # Statut inchangé par rapport à l'ancien comportement (verrouillé par le test existant
    # test_calibration_unknown_without_enough_scored_buckets) -- jamais promu à "ok".
    assert calib.status == "unknown"
    assert "intervalle de crédibilité" in calib.detail
    assert "sur 1 BUY" in calib.detail
    assert "échantillon encore trop petit pour trancher" in calib.detail


@pytest.mark.asyncio
async def test_calibration_credible_interval_never_flips_unknown_to_ok():
    # Peu de BUY, hit-rate observé favorable (100%) -- l'intervalle bayésien large ne doit
    # jamais faire basculer le statut en "ok" : reste gouverné par la logique existante
    # (buckets de calibration notés, ici aucun car potentiel=None).
    for _ in range(3):
        await _record_and_close(outcome_pct=20.0, potentiel=None)
    scorecard = await compute_readiness_scorecard()
    calib = next(c for c in scorecard["checks"] if c.id == "calibration")
    assert calib.status == "unknown"
    assert "sur 3 BUY" in calib.detail


def test_format_readiness_report_includes_verdict_and_all_checks():
    from aria_core.skills.real_money_readiness import ReadinessCheck

    scorecard = {
        "checks": [ReadinessCheck(id="x", label="Label X", status="fail", detail="detail X")],
        "all_ok": False,
        "verdict": "NON — 0/8 cases cochées, argent réel toujours hors de portée.",
    }
    report = format_readiness_report(scorecard)
    assert "Label X" in report
    assert "detail X" in report
    assert "NON" in report
    assert "fonctionnalité, pas un défaut" in report
