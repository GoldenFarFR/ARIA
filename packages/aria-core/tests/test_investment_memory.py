"""Boucle mémoire d'investissement — thèse → décision → résultat → leçon.

Tests isolés : DB_PATH pointe vers un SQLite jetable par test (tmp_path). Aucun
appel réseau, aucune action financière — pur journal de raisonnement.
"""
from __future__ import annotations

import pytest

from aria_core import investment_memory


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(investment_memory, "DB_PATH", str(tmp_path / "aria_test.db"))
    yield


@pytest.mark.asyncio
async def test_record_thesis_returns_incrementing_ids():
    first = await investment_memory.record_thesis(
        token_address="0xabc", thesis="holders solides", decision="WATCH"
    )
    second = await investment_memory.record_thesis(
        token_address="0xdef", thesis="liquidité en hausse", decision="BUY"
    )
    assert first == 1
    assert second == 2


@pytest.mark.asyncio
async def test_record_thesis_normalizes_decision_case():
    thesis_id = await investment_memory.record_thesis(
        token_address="0xabc", thesis="test", decision="buy"
    )
    row = await investment_memory.get_thesis(thesis_id)
    assert row is not None
    assert row["decision"] == "BUY"
    assert row["status"] == "open"


@pytest.mark.asyncio
async def test_record_thesis_rejects_invalid_decision():
    with pytest.raises(ValueError):
        await investment_memory.record_thesis(
            token_address="0xabc", thesis="test", decision="MOON"
        )


@pytest.mark.asyncio
async def test_close_thesis_attributes_outcome_and_lesson():
    thesis_id = await investment_memory.record_thesis(
        token_address="0xabc", thesis="test", decision="BUY"
    )
    closed = await investment_memory.close_thesis(
        thesis_id, outcome="+18% en 2 semaines", lesson="catalyseur listing sous-estimé"
    )
    assert closed is not None
    assert closed["status"] == "closed"
    assert closed["outcome"] == "+18% en 2 semaines"
    assert closed["lesson"] == "catalyseur listing sous-estimé"
    assert closed["closed_at"] is not None


@pytest.mark.asyncio
async def test_close_thesis_is_atomic_no_double_close():
    thesis_id = await investment_memory.record_thesis(
        token_address="0xabc", thesis="test", decision="BUY"
    )
    first = await investment_memory.close_thesis(thesis_id, outcome="win", lesson="a")
    second = await investment_memory.close_thesis(thesis_id, outcome="rewrite", lesson="b")
    assert first is not None
    assert second is None  # déjà clôturée — on ne réécrit pas l'historique
    row = await investment_memory.get_thesis(thesis_id)
    assert row["outcome"] == "win"


@pytest.mark.asyncio
async def test_close_unknown_thesis_returns_none():
    result = await investment_memory.close_thesis(999, outcome="x", lesson="y")
    assert result is None


@pytest.mark.asyncio
async def test_list_open_theses_excludes_closed_and_orders_desc():
    id1 = await investment_memory.record_thesis(token_address="0x1", thesis="a", decision="WATCH")
    id2 = await investment_memory.record_thesis(token_address="0x2", thesis="b", decision="BUY")
    id3 = await investment_memory.record_thesis(token_address="0x3", thesis="c", decision="AVOID")
    await investment_memory.close_thesis(id2, outcome="done", lesson="")

    open_theses = await investment_memory.list_open_theses()
    ids = [row["id"] for row in open_theses]
    assert ids == [id3, id1]  # DESC, sans la clôturée


@pytest.mark.asyncio
async def test_get_thesis_unknown_returns_none():
    assert await investment_memory.get_thesis(1234) is None
