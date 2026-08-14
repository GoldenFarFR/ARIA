"""Tests for calibrate_skill.py -- /calibrate command + LanceDB "lesson" write (#169)."""
from __future__ import annotations

import pytest

from aria_core.skills import calibrate_skill


def _stub_common(monkeypatch, *, calibration=None):
    cal = calibration or {"id": "abc1234567", "verdict": "vrai", "brier": None}
    monkeypatch.setattr(calibrate_skill, "record_calibration", lambda *a, **k: cal)
    monkeypatch.setattr(calibrate_skill, "check_contradiction", lambda *a, **k: (False, ""))
    monkeypatch.setattr(calibrate_skill, "queue_promotion", lambda *a, **k: None)
    monkeypatch.setattr(calibrate_skill, "format_stats_summary", lambda *a, **k: "stats")
    monkeypatch.setattr(calibrate_skill, "append_memory", lambda *a, **k: None)

    async def _fake_triaged_add_knowledge(*a, **k):
        return None, None

    monkeypatch.setattr(calibrate_skill, "triaged_add_knowledge", _fake_triaged_add_knowledge)
    return cal


@pytest.mark.asyncio
async def test_execute_calibrate_stores_lancedb_lesson(monkeypatch):
    cal = _stub_common(monkeypatch, calibration={"id": "cal-001", "verdict": "vrai", "brier": None})
    calls = []

    async def _fake_store(entry_type, content, *, metadata=None):
        calls.append((entry_type, content, metadata))
        return "doc-id"

    monkeypatch.setattr("aria_core.memory.vector.lancedb_store.store", _fake_store)

    await calibrate_skill.execute_calibrate("DEXPulse est une filiale | vrai | holding", lang="fr")

    assert len(calls) == 1
    entry_type, content, metadata = calls[0]
    assert entry_type == "lesson"
    assert "DEXPulse est une filiale" in content
    assert "[vrai]" in content
    assert metadata["topic"] == "epistemic"
    assert metadata["confidence"] == pytest.approx(0.95)
    assert metadata["source"] == "holding"
    assert metadata["source_id"] == "calibration-cal-001"


@pytest.mark.asyncio
async def test_execute_calibrate_confidence_matches_verdict(monkeypatch):
    _stub_common(monkeypatch, calibration={"id": "cal-002", "verdict": "faux", "brier": None})
    calls = []

    async def _fake_store(entry_type, content, *, metadata=None):
        calls.append(metadata)
        return "doc-id"

    monkeypatch.setattr("aria_core.memory.vector.lancedb_store.store", _fake_store)

    await calibrate_skill.execute_calibrate("Un token X n'a pas de liquidite | faux", lang="fr")

    assert calls[0]["confidence"] == pytest.approx(0.05)


@pytest.mark.asyncio
async def test_execute_calibrate_missing_pipe_never_calls_lancedb(monkeypatch):
    _stub_common(monkeypatch)
    calls = []

    async def _fake_store(*a, **k):
        calls.append(1)
        return "doc-id"

    monkeypatch.setattr("aria_core.memory.vector.lancedb_store.store", _fake_store)

    reply, meta = await calibrate_skill.execute_calibrate("no pipe here", lang="fr")

    assert meta["ok"] is False
    assert calls == []
