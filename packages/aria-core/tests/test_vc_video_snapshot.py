"""Capture du verdict /vc pour la vidéo marketing (tâche #23) -- sans recalcul,
juste une sérialisation du VCResult déjà en mémoire."""
from __future__ import annotations

import pytest

from aria_core.skills import vc_session_context as vsc
from aria_core.skills.vc_analysis import VCResult


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(vsc, "DB_PATH", str(tmp_path / "vc_video.db"))
    yield


def _result(contract="0xABC", symbol="TEST") -> VCResult:
    return VCResult(
        contract=contract,
        potentiel=70,
        risque="modere",
        these="These de test",
        recommandation="BUY",
        taille_pct=2.0,
        entree="0.01",
        invalidation="0.005",
        cible="0.05",
        symbol=symbol,
        scenarios=[{"nom": "bull", "cible": "0.10", "probabilite": 0.2}],
        chart_data_uri="data:image/png;base64,AAA",
    )


@pytest.mark.asyncio
async def test_queue_then_load_returns_full_snapshot_no_recompute():
    result = _result()
    candidate_id = await vsc.queue_video_candidate(result)
    assert isinstance(candidate_id, int)

    snapshot = await vsc.load_next_video_candidate()
    assert snapshot is not None
    assert snapshot["id"] == candidate_id
    assert snapshot["contract"] == "0xABC"
    assert snapshot["these"] == "These de test"
    assert snapshot["cible"] == "0.05"
    assert snapshot["invalidation"] == "0.005"
    assert snapshot["scenarios"] == [{"nom": "bull", "cible": "0.10", "probabilite": 0.2}]
    assert snapshot["chart_data_uri"] == "data:image/png;base64,AAA"


@pytest.mark.asyncio
async def test_load_returns_none_when_queue_empty():
    assert await vsc.load_next_video_candidate() is None


@pytest.mark.asyncio
async def test_fifo_order_oldest_pending_first():
    id1 = await vsc.queue_video_candidate(_result(contract="0x1"))
    await vsc.queue_video_candidate(_result(contract="0x2"))

    first = await vsc.load_next_video_candidate()
    assert first["id"] == id1
    assert first["contract"] == "0x1"


@pytest.mark.asyncio
async def test_mark_done_removes_from_pending_queue():
    candidate_id = await vsc.queue_video_candidate(_result())
    await vsc.mark_video_candidate_done(candidate_id, status="ready_for_review")

    assert await vsc.load_next_video_candidate() is None


@pytest.mark.asyncio
async def test_mark_done_never_deletes_row_only_updates_status():
    import aiosqlite

    candidate_id = await vsc.queue_video_candidate(_result())
    await vsc.mark_video_candidate_done(candidate_id, status="error")

    async with aiosqlite.connect(vsc.DB_PATH) as db:
        row = await (
            await db.execute(
                "SELECT status, processed_at FROM vc_video_snapshot WHERE id = ?",
                (candidate_id,),
            )
        ).fetchone()
    assert row is not None
    assert row[0] == "error"
    assert row[1] is not None
