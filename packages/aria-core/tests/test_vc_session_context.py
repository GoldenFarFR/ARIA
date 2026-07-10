"""Mémoire de suivi /vc opérateur — tests hors réseau."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import pytest

from aria_core.skills import vc_session_context as vsc
from aria_core.skills.vc_analysis import VCResult

ADDR = "0x" + "b" * 40


@pytest.fixture
async def isolated_vc_session_db(tmp_path, monkeypatch):
    db_path = tmp_path / "vc_session.db"
    monkeypatch.setattr(vsc, "DB_PATH", str(db_path))
    yield db_path


def _sample_result(**overrides) -> VCResult:
    base = dict(
        contract=ADDR,
        potentiel=4,
        risque="ÉLEVÉ",
        these="Thèse test whale 57%.",
        recommandation="AVOID",
        taille_pct=0.0,
        entree="0.346",
        invalidation="0.336",
        cible="2.13",
        upside_pct=515.0,
        downside_pct=3.0,
        security_score=45,
        lite_verdict="CAUTION",
        symbol="VELVET",
        llm_used=True,
    )
    base.update(overrides)
    return VCResult(**base)


@pytest.mark.asyncio
async def test_record_and_load_within_ttl(isolated_vc_session_db):
    result = _sample_result()
    await vsc.record_operator_vc(result, prediction_id=5, telegram_summary="ordre telegram")

    loaded = await vsc.load_operator_vc()
    assert loaded is not None
    assert loaded["contract"] == ADDR
    assert loaded["prediction_id"] == 5
    assert loaded["upside_pct"] == 515.0
    assert loaded["recommandation"] == "AVOID"


@pytest.mark.asyncio
async def test_load_returns_none_after_ttl(isolated_vc_session_db):
    await vsc.record_operator_vc(_sample_result())
    old = (datetime.now(timezone.utc) - timedelta(seconds=vsc.TTL_SECONDS + 60)).isoformat()
    import aiosqlite

    async with aiosqlite.connect(str(isolated_vc_session_db)) as db:
        await db.execute("UPDATE vc_operator_last SET recorded_at = ? WHERE id = 1", (old,))
        await db.commit()
    assert await vsc.load_operator_vc() is None


def test_is_vc_followup_detects_plus515_pourquoi():
    assert vsc.is_vc_followup_question("+515 pourquoi ?")
    assert vsc.is_vc_followup_question("+515% sur ton analyse c'est énorme")


def test_is_vc_followup_ignores_unrelated_chat():
    assert not vsc.is_vc_followup_question("salut ça va ?")
    assert not vsc.is_vc_followup_question("quelle est la capitale de la France ?")


def test_build_followup_block_contains_rr_levels():
    data = {
        "contract": ADDR,
        "symbol": "VELVET",
        "recommandation": "AVOID",
        "potentiel": 4,
        "risque": "ÉLEVÉ",
        "upside_pct": 515.0,
        "downside_pct": 3.0,
        "rr": 171.7,
        "entree": "0.346",
        "invalidation": "0.336",
        "cible": "2.13",
        "these": "Whale 57%.",
        "security_score": 45,
        "lite_verdict": "CAUTION",
        "prediction_id": 5,
    }
    block = vsc.build_followup_context_block(data, lang="fr")
    assert "515" in block
    assert "0.346" in block
    assert "2.13" in block
    assert "AVOID" in block
    assert "Ne cherche pas sur le web" in block
