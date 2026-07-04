"""Tests registre suggestions + auto-rate worker [done]."""
from __future__ import annotations

from pathlib import Path

import pytest

from aria_core.aria_worker_queue import (
    WorkerTask,
    _append_task_to_markdown,
    mark_worker_task_done,
    mark_task_done_in_markdown,
)
from aria_core.suggestion_feedback import (
    auto_rate_worker_done,
    load_worker_done_task_ids,
    rate_suggestion,
    suggestion_id,
    sync_worker_done_ratings,
)


@pytest.fixture
def ledger_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = tmp_path / "suggestion-ledger.jsonl"
    monkeypatch.setenv("ARIA_OPS_ROOT", str(tmp_path))
    monkeypatch.setattr("aria_core.suggestion_feedback.LEDGER_REL", Path("suggestion-ledger.jsonl"))
    return path


def test_auto_rate_worker_done_matches_title(ledger_tmp: Path):
    fact = "Regression health Render timeout"
    sid = suggestion_id(fact, "improvement")
    from aria_core.suggestion_feedback import append_event, LedgerEntry, _now_iso

    append_event(
        ledger_tmp,
        {
            "event": "shown",
            "at": _now_iso(),
            "entry": {
                "id": sid,
                "fact": fact,
                "kind": "improvement",
                "status": "open",
                "first_shown": _now_iso(),
                "last_shown": _now_iso(),
                "show_count": 1,
                "ratings": [],
                "avg_score": None,
            },
        },
    )
    out = auto_rate_worker_done(
        {
            "task_id": "cap-gap-health_render_regression",
            "title": "Incident: regression health Render",
            "source": "capability_gap",
            "priority": "high",
        },
        path=ledger_tmp,
    )
    assert out["score"] == 5
    assert sid in out["rated"]
    assert "cap-gap-health_render_regression" in load_worker_done_task_ids(path=ledger_tmp)


def test_sync_worker_done_skips_already_rated(ledger_tmp: Path, monkeypatch: pytest.MonkeyPatch):
    task = WorkerTask(
        task_id="cap-gap-test-v2",
        title="Test worker sync",
        source="capability_gap",
        problem="blocked",
        action="fix",
    )
    body = mark_task_done_in_markdown(_append_task_to_markdown("", task), "cap-gap-test-v2")
    first = sync_worker_done_ratings(body, path=ledger_tmp)
    second = sync_worker_done_ratings(body, path=ledger_tmp)
    assert len(first) == 1
    assert len(second) == 0


def test_mark_worker_task_done_writes_and_rates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    worker_md = tmp_path / "sessions" / "ARIA-WORKER.md"
    ledger = tmp_path / "suggestion-ledger.jsonl"
    monkeypatch.setenv("ARIA_OPS_ROOT", str(tmp_path))
    monkeypatch.setattr("aria_core.suggestion_feedback.LEDGER_REL", Path("suggestion-ledger.jsonl"))
    monkeypatch.setattr("aria_core.aria_worker_queue.resolve_local_worker_md", lambda: worker_md)
    monkeypatch.setattr("aria_core.aria_worker_queue._queue_dir", lambda: tmp_path)
    monkeypatch.setattr("aria_core.aria_worker_queue._local_jsonl", lambda: tmp_path / "tasks.jsonl")
    monkeypatch.setattr("aria_core.aria_worker_queue.append_memory", lambda *a, **k: None)

    task = WorkerTask(
        task_id="cap-gap-demo",
        title="Demo capability gap",
        source="capability_gap",
        problem="x",
        action="y",
        priority="high",
    )
    worker_md.parent.mkdir(parents=True, exist_ok=True)
    worker_md.write_text(_append_task_to_markdown("", task), encoding="utf-8")

    from aria_core.suggestion_feedback import append_event, _now_iso

    fact = "Demo capability gap"
    sid = suggestion_id(fact, "workflow_fix")
    now = _now_iso()
    append_event(
        ledger,
        {
            "event": "shown",
            "at": now,
            "entry": {
                "id": sid,
                "fact": fact,
                "kind": "workflow_fix",
                "status": "open",
                "first_shown": now,
                "last_shown": now,
                "show_count": 1,
                "ratings": [],
                "avg_score": None,
            },
        },
    )

    out = mark_worker_task_done("cap-gap-demo", note="pytest", score=5)
    assert out["status"] == "done"
    assert "[done] cap-gap-demo" in worker_md.read_text(encoding="utf-8")