"""Boucle d'entraînement hebdo — tirage → pronostics → résolution → rapport (offline)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from aria_core import screened_pool, vc_predictions, weekly_training as wt
from aria_core.skills.acp_onchain_scan import PairSnapshot, TokenScanContext
from aria_core.skills.vc_analysis import VCResult


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    db = str(tmp_path / "weekly_test.db")
    monkeypatch.setattr(vc_predictions, "DB_PATH", db)
    monkeypatch.setattr(screened_pool, "DB_PATH", db)
    yield


def _fake_analyzer(price: float, reco: str = "BUY"):
    async def _analyze(contract: str):
        result = VCResult(
            contract=contract, potentiel=8, risque="MODÉRÉ", these="t",
            recommandation=reco, taille_pct=5.0, entree="marché", invalidation="x",
            cible="y", security_score=72, llm_used=True,
        )
        ctx = TokenScanContext(
            contract=contract, valid_address=True,
            best_pair=PairSnapshot(pair_address=f"pool-{contract}", price_usd=price, base_symbol="T"),
        )
        return result, ctx
    return _analyze


@pytest.mark.asyncio
async def test_run_weekly_forecasts_records_with_entry_price():
    async def drawer():
        return [{"contract": "0xAAA"}, {"contract": "0xBBB"}]

    ids = await wt.run_weekly_forecasts(n=2, drawer=drawer, analyzer=_fake_analyzer(1.0))
    assert len(ids) == 2
    row = await vc_predictions.get_prediction(ids[0])
    assert row["entry_price"] == pytest.approx(1.0)
    assert row["pool_address"] == "pool-0xAAA"
    assert row["strategy"] == "vc"
    assert row["status"] == "open"


@pytest.mark.asyncio
async def test_failing_analysis_is_skipped_not_fatal():
    async def drawer():
        return [{"contract": "0xGOOD"}, {"contract": "0xBAD"}]

    async def analyzer(contract):
        if contract == "0xBAD":
            raise RuntimeError("scan down")
        return await _fake_analyzer(2.0)(contract)

    ids = await wt.run_weekly_forecasts(drawer=drawer, analyzer=analyzer)
    assert len(ids) == 1  # le token qui échoue est ignoré, pas de crash


@pytest.mark.asyncio
async def test_resolve_due_closes_at_horizon_via_price():
    # Un pronostic entré à 1.0, prix courant 1.5 -> +50%.
    pid = await vc_predictions.record_prediction(
        contract="0xAAA", recommandation="BUY", potentiel=8, risque="MODÉRÉ",
        taille_pct=5.0, security_score=72, llm_used=True,
        entry_price=1.0, pool_address="pool-A", strategy="vc",
    )

    async def price_fn(pool, network):
        return 1.5

    # Horloge très loin dans le futur -> horizon dépassé.
    future = datetime(2100, 1, 1, tzinfo=timezone.utc)
    summary = await wt.resolve_due(now=future, price_fn=price_fn)
    assert summary["resolved"] == 1
    row = await vc_predictions.get_prediction(pid)
    assert row["status"] == "closed"
    assert row["outcome_pct"] == pytest.approx(50.0)


@pytest.mark.asyncio
async def test_resolve_due_leaves_immature_open():
    await vc_predictions.record_prediction(
        contract="0xAAA", recommandation="BUY", potentiel=8, risque="MODÉRÉ",
        taille_pct=5.0, security_score=72, llm_used=True,
        entry_price=1.0, pool_address="pool-A", strategy="vc",
    )

    async def price_fn(pool, network):
        return 2.0

    # Maintenant = tout juste créé -> horizon PAS atteint -> rien résolu.
    summary = await wt.resolve_due(now=datetime.now(timezone.utc), price_fn=price_fn)
    assert summary["resolved"] == 0


@pytest.mark.asyncio
async def test_resolve_due_skips_without_entry_or_pool():
    # Pas de prix d'entrée -> non résolvable (jamais inventé).
    await vc_predictions.record_prediction(
        contract="0xAAA", recommandation="BUY", potentiel=8, risque="MODÉRÉ",
        taille_pct=5.0, security_score=72, llm_used=True,
    )

    async def price_fn(pool, network):
        return 9.9

    future = datetime(2100, 1, 1, tzinfo=timezone.utc)
    summary = await wt.resolve_due(now=future, price_fn=price_fn)
    assert summary["resolved"] == 0


@pytest.mark.asyncio
async def test_weekly_report_shape():
    await screened_pool.upsert_screened(contract="0xP", verdict="SAFE", security_score=75)
    rep = await wt.weekly_report()
    assert "calibration" in rep and "wallet" in rep
    assert rep["pool_active"] == 1
    assert rep["wallet"]["index"] == 100.0  # aucune position -> neutre
