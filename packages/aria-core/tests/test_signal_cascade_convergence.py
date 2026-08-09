"""Multi-source signal cascade -- stages 3 (convergence) + 4 (persistent
triage queue). Never a trigger, never blocks a source column's own cycle."""
from __future__ import annotations

import pytest

from aria_core import signal_cascade_convergence as scc

CONTRACT = "0x" + "a" * 40


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "signal_cascade_convergence.db")
    monkeypatch.setattr(scc, "DB_PATH", db_path)
    monkeypatch.setattr(scc, "_table_ready", False)
    yield


@pytest.mark.asyncio
async def test_positive_signal_queues_for_triage():
    await scc.record_source_signal(CONTRACT, "base", "github", "positive", detail="repo x", symbol="TP")
    pending = await scc.list_pending_triage()
    assert len(pending) == 1
    assert pending[0]["convergence_count"] == 1
    assert pending[0]["symbol"] == "TP"


@pytest.mark.asyncio
async def test_weak_signal_never_queues():
    await scc.record_source_signal(CONTRACT, "base", "github", "weak", detail="repo x")
    assert await scc.list_pending_triage() == []


@pytest.mark.asyncio
async def test_two_sources_agreeing_raises_convergence_count():
    await scc.record_source_signal(CONTRACT, "base", "github", "positive", detail="repo x")
    await scc.record_source_signal(CONTRACT, "base", "farcaster", "positive", detail="cast y")
    pending = await scc.list_pending_triage()
    assert len(pending) == 1
    assert pending[0]["convergence_count"] == 2
    sources = {s["source"] for s in pending[0]["sources"]}
    assert sources == {"github", "farcaster"}


@pytest.mark.asyncio
async def test_already_queued_token_never_duplicated():
    await scc.record_source_signal(CONTRACT, "base", "github", "positive", detail="repo x")
    await scc.record_source_signal(CONTRACT, "base", "github", "positive", detail="repo x updated")
    pending = await scc.list_pending_triage()
    assert len(pending) == 1  # one row, not two


@pytest.mark.asyncio
async def test_pending_sorted_by_convergence_count_then_oldest_first():
    await scc.record_source_signal("0x" + "1" * 40, "base", "github", "positive", detail="a")
    await scc.record_source_signal("0x" + "2" * 40, "base", "github", "positive", detail="b")
    await scc.record_source_signal("0x" + "2" * 40, "base", "farcaster", "positive", detail="c")
    pending = await scc.list_pending_triage()
    assert pending[0]["contract"] == "0x" + "2" * 40  # 2 sources beats 1
    assert pending[0]["convergence_count"] == 2


@pytest.mark.asyncio
async def test_record_triage_decision_requires_a_reasoning():
    await scc.record_source_signal(CONTRACT, "base", "github", "positive", detail="repo x")
    ok = await scc.record_triage_decision(CONTRACT, "base", "validated", "")
    assert ok is False
    pending = await scc.list_pending_triage()
    assert len(pending) == 1  # still pending -- empty reasoning rejected


@pytest.mark.asyncio
async def test_record_triage_decision_rejects_invalid_decision_value():
    await scc.record_source_signal(CONTRACT, "base", "github", "positive", detail="repo x")
    ok = await scc.record_triage_decision(CONTRACT, "base", "maybe", "un vrai raisonnement")
    assert ok is False


@pytest.mark.asyncio
async def test_record_triage_decision_removes_from_pending():
    await scc.record_source_signal(CONTRACT, "base", "github", "positive", detail="repo x")
    ok = await scc.record_triage_decision(
        CONTRACT, "base", "validated", "substance réelle confirmée, commits techniques réguliers",
    )
    assert ok is True
    assert await scc.list_pending_triage() == []


@pytest.mark.asyncio
async def test_decided_item_never_reopened_by_a_later_source():
    await scc.record_source_signal(CONTRACT, "base", "github", "positive", detail="repo x")
    await scc.record_triage_decision(CONTRACT, "base", "rejected", "substance faible, pas convaincant")
    assert await scc.list_pending_triage() == []

    await scc.record_source_signal(CONTRACT, "base", "farcaster", "positive", detail="cast y")
    assert await scc.list_pending_triage() == []  # still not reopened despite a 2nd source agreeing


@pytest.mark.asyncio
async def test_record_triage_decision_on_unknown_contract_returns_false():
    ok = await scc.record_triage_decision(CONTRACT, "base", "rejected", "raisonnement")
    assert ok is False


@pytest.mark.asyncio
async def test_signal_never_raises_even_on_broken_db(monkeypatch):
    monkeypatch.setattr(scc, "DB_PATH", "/nonexistent/dir/shadow.db")
    monkeypatch.setattr(scc, "_table_ready", False)
    await scc.record_source_signal(CONTRACT, "base", "github", "positive", detail="x")  # does not raise
