"""Journal des prédictions VC — track record + calibration (mesure de pertinence).

DB isolée par test (DB_PATH sur tmp_path). compute_metrics testée comme fonction pure.
"""
from __future__ import annotations

import pytest

from aria_core import vc_predictions
from aria_core.vc_predictions import compute_metrics


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(vc_predictions, "DB_PATH", str(tmp_path / "aria_test.db"))
    yield


async def _record(**kw):
    base = dict(
        contract="0xabc", recommandation="BUY", potentiel=7, risque="MODÉRÉ",
        taille_pct=5.0, security_score=60, llm_used=True,
    )
    base.update(kw)
    return await vc_predictions.record_prediction(**base)


@pytest.mark.asyncio
async def test_record_and_get():
    pid = await _record()
    row = await vc_predictions.get_prediction(pid)
    assert row["recommandation"] == "BUY"
    assert row["status"] == "open"
    assert row["llm_used"] == 1


@pytest.mark.asyncio
async def test_close_prediction_atomic():
    pid = await _record()
    first = await vc_predictions.close_prediction(pid, outcome_pct=18.0, note="listing")
    second = await vc_predictions.close_prediction(pid, outcome_pct=-5.0, note="rewrite")
    assert first is not None
    assert first["outcome_pct"] == 18.0
    assert second is None  # jamais de double clôture
    row = await vc_predictions.get_prediction(pid)
    assert row["outcome_pct"] == 18.0


@pytest.mark.asyncio
async def test_close_unknown_returns_none():
    assert await vc_predictions.close_prediction(999, outcome_pct=1.0) is None


@pytest.mark.asyncio
async def test_list_recently_closed_excludes_open_and_old():
    import aiosqlite
    from datetime import datetime, timedelta, timezone

    old_id = await _record(contract="0xold")
    await vc_predictions.close_prediction(old_id, outcome_pct=5.0)
    old_closed_at = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    async with aiosqlite.connect(vc_predictions.DB_PATH) as db:
        await db.execute(
            "UPDATE vc_prediction SET closed_at = ? WHERE id = ?", (old_closed_at, old_id),
        )
        await db.commit()

    still_open_id = await _record(contract="0xstillopen")

    recent_id = await _record(contract="0xrecent")
    await vc_predictions.close_prediction(recent_id, outcome_pct=-10.0)

    since = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    closed = await vc_predictions.list_recently_closed(since)

    ids = {row["id"] for row in closed}
    assert recent_id in ids
    assert still_open_id not in ids  # jamais un pronostic encore ouvert
    assert old_id not in ids  # trop ancien pour la fenêtre demandée


@pytest.mark.asyncio
async def test_count_predictions_for_contract_increments():
    assert await vc_predictions.count_predictions_for_contract("0xabc") == 0
    await _record(contract="0xabc")
    assert await vc_predictions.count_predictions_for_contract("0xabc") == 1
    await _record(contract="0xabc")
    assert await vc_predictions.count_predictions_for_contract("0xabc") == 2
    # Autre contrat : compteur indépendant.
    assert await vc_predictions.count_predictions_for_contract("0xdef") == 0


@pytest.mark.asyncio
async def test_count_predictions_for_contract_case_insensitive():
    await _record(contract="0xABCDEF")
    assert await vc_predictions.count_predictions_for_contract("0xabcdef") == 1


@pytest.mark.asyncio
async def test_metrics_end_to_end():
    p1 = await _record(potentiel=8)
    p2 = await _record(potentiel=8)
    p3 = await _record(potentiel=4)
    await _record(potentiel=9)  # reste ouverte
    await vc_predictions.close_prediction(p1, outcome_pct=20.0)
    await vc_predictions.close_prediction(p2, outcome_pct=-10.0)
    await vc_predictions.close_prediction(p3, outcome_pct=-30.0)

    m = await vc_predictions.metrics()
    assert m["total"] == 4
    assert m["closed"] == 3
    assert m["open"] == 1
    assert m["buy_count"] == 3
    assert m["hit_rate"] == pytest.approx(1 / 3)  # 1 gagnant sur 3 BUY
    assert m["avg_pnl_buy"] == pytest.approx((20 - 10 - 30) / 3)


def test_compute_metrics_calibration_buckets():
    preds = [
        {"status": "closed", "recommandation": "BUY", "potentiel": 8, "outcome_pct": 30.0},
        {"status": "closed", "recommandation": "BUY", "potentiel": 7, "outcome_pct": 10.0},
        {"status": "closed", "recommandation": "BUY", "potentiel": 4, "outcome_pct": -20.0},
        {"status": "closed", "recommandation": "WATCH", "potentiel": 2, "outcome_pct": -5.0},
        {"status": "open", "recommandation": "BUY", "potentiel": 9, "outcome_pct": None},
    ]
    m = compute_metrics(preds)
    assert m["open"] == 1
    assert m["closed"] == 4
    buckets = {b["bucket"]: b for b in m["calibration"]}
    assert buckets["7-8"]["avg_pnl"] == pytest.approx(20.0)  # (30+10)/2
    assert buckets["4-6"]["avg_pnl"] == pytest.approx(-20.0)
    assert buckets["0-3"]["avg_pnl"] == pytest.approx(-5.0)


def test_compute_metrics_empty_is_safe():
    m = compute_metrics([])
    assert m["total"] == 0
    assert m["hit_rate"] is None
    assert m["calibration"] == []


def test_compute_metrics_ignores_open_for_rates():
    preds = [
        {"status": "open", "recommandation": "BUY", "potentiel": 8, "outcome_pct": None},
        {"status": "closed", "recommandation": "BUY", "potentiel": 8, "outcome_pct": 5.0},
    ]
    m = compute_metrics(preds)
    assert m["buy_count"] == 1  # seule la clôturée compte
    assert m["hit_rate"] == 1.0
