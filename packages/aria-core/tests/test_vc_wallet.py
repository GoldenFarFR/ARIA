"""Portefeuille SUIVI d'ARIA (paper) — valorisation 85/15 + migration de schéma.

Tout est hors-ligne : `portfolio_value` est pure (prix injectés). La migration
à chaud est vérifiée sur une DB à l'ancien schéma (aucune colonne wallet).
"""
from __future__ import annotations

import aiosqlite
import pytest

from aria_core import vc_predictions
from aria_core.vc_predictions import STRATEGY_ALLOCATION, portfolio_value


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(vc_predictions, "DB_PATH", str(tmp_path / "wallet_test.db"))
    yield


# ── valorisation pure (85/15) ───────────────────────────────────────────────

def test_portfolio_value_85_15_split():
    preds = [
        {"id": 1, "recommandation": "BUY", "strategy": "vc", "entry_price": 100.0},
        {"id": 2, "recommandation": "BUY", "strategy": "vc", "entry_price": 100.0},
        {"id": 3, "recommandation": "BUY", "strategy": "spec", "entry_price": 10.0},
    ]
    prices = {1: 150.0, 2: 50.0, 3: 20.0}  # vc: +50% & -50% => 0 ; spec: +100%
    v = portfolio_value(preds, prices)
    assert v["vc_return_pct"] == pytest.approx(0.0)
    assert v["spec_return_pct"] == pytest.approx(100.0)
    # total = 0.85*0 + 0.15*1.0 = 0.15
    assert v["total_return_pct"] == pytest.approx(15.0)
    assert v["index"] == pytest.approx(115.0)
    assert v["vc_positions"] == 2 and v["spec_positions"] == 1
    assert v["allocation"] == STRATEGY_ALLOCATION


def test_portfolio_value_excludes_non_buy_and_missing():
    preds = [
        {"id": 1, "recommandation": "AVOID", "strategy": "vc", "entry_price": 100.0},
        {"id": 2, "recommandation": "WATCH", "strategy": "vc", "entry_price": 100.0},
        {"id": 3, "recommandation": "BUY", "strategy": "vc", "entry_price": None},   # pas de prix d'entrée
        {"id": 4, "recommandation": "BUY", "strategy": "vc", "entry_price": 100.0},  # pas de prix courant
    ]
    v = portfolio_value(preds, {1: 999, 2: 999})  # aucun prix pour la position 4
    assert v["positions_valued"] == 0
    assert v["index"] == pytest.approx(100.0)  # neutre, jamais gonflé
    assert v["total_return_pct"] == pytest.approx(0.0)


def test_portfolio_value_empty_is_neutral():
    v = portfolio_value([], {})
    assert v["index"] == 100.0
    assert v["positions_valued"] == 0


# ── persistance des champs wallet + migration à chaud ────────────────────────

@pytest.mark.asyncio
async def test_record_stores_wallet_fields():
    pid = await vc_predictions.record_prediction(
        contract="0xabc", recommandation="BUY", potentiel=7, risque="MODÉRÉ",
        taille_pct=5.0, security_score=60, llm_used=True,
        strategy="spec", entry_price=0.042, pool_address="0xpool",
        network="base", target_price=0.089, invalidation_price=0.031,
    )
    row = await vc_predictions.get_prediction(pid)
    assert row["strategy"] == "spec"
    assert row["entry_price"] == pytest.approx(0.042)
    assert row["pool_address"] == "0xpool"
    assert row["target_price"] == pytest.approx(0.089)
    assert row["invalidation_price"] == pytest.approx(0.031)


@pytest.mark.asyncio
async def test_unknown_strategy_defaults_to_vc():
    pid = await vc_predictions.record_prediction(
        contract="0xabc", recommandation="BUY", potentiel=7, risque="MODÉRÉ",
        taille_pct=5.0, security_score=60, llm_used=True, strategy="degen",
    )
    row = await vc_predictions.get_prediction(pid)
    assert row["strategy"] == "vc"  # allowlist fermée


@pytest.mark.asyncio
async def test_migration_adds_columns_to_legacy_db(tmp_path, monkeypatch):
    """Une DB à l'ancien schéma (sans colonnes wallet) est migrée sans rien casser."""
    db_path = str(tmp_path / "legacy.db")
    monkeypatch.setattr(vc_predictions, "DB_PATH", db_path)
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """CREATE TABLE vc_prediction (
                id INTEGER PRIMARY KEY AUTOINCREMENT, contract TEXT NOT NULL,
                recommandation TEXT NOT NULL, potentiel INTEGER, risque TEXT,
                taille_pct REAL, security_score INTEGER, llm_used INTEGER,
                report_ref TEXT, traded INTEGER, status TEXT NOT NULL DEFAULT 'open',
                outcome_pct REAL, outcome_note TEXT, created_at TEXT NOT NULL, closed_at TEXT
            )"""
        )
        await db.execute(
            "INSERT INTO vc_prediction (contract, recommandation, status, created_at) "
            "VALUES ('0xold','BUY','open','2026-01-01T00:00:00+00:00')"
        )
        await db.commit()

    # Le prochain record déclenche _ensure_table -> migration.
    pid = await vc_predictions.record_prediction(
        contract="0xnew", recommandation="BUY", potentiel=7, risque="MODÉRÉ",
        taille_pct=5.0, security_score=60, llm_used=True, entry_price=1.0, strategy="vc",
    )
    new_row = await vc_predictions.get_prediction(pid)
    assert new_row["entry_price"] == pytest.approx(1.0)
    # L'ancienne ligne reste lisible ; strategy par défaut appliquée.
    old_row = await vc_predictions.get_prediction(1)
    assert old_row["contract"] == "0xold"
    assert old_row["strategy"] == "vc"
