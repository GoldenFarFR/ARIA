"""Tests apply pending-lessons → aria-core (Sprint 4)."""
from __future__ import annotations

import json

import pytest

from apply_pending_lessons import (
    approve_lesson,
    apply_lesson,
    list_lessons,
    parse_lessons,
    run_apply,
    _set_lesson_status,
)

SAMPLE = """# Leçons

### Leçon — Anti-Ollama fallback
- **Constat** : Ollama hors-sujet après Groq 429
- **Tu as fait X** : fallback Ollama par défaut
- **Mieux** : Groq seul, message quota clair
- **Ship core** : reflection
- **Statut** : pending

### Leçon — ACP delete déterministe
- **Constat** : hallucination « workflow supprimé »
- **Tu as fait X** : réponse LLM sans acp-cli
- **Mieux** : route execute_offering_delete
- **Ship core** : pitfall
- **Statut** : approved

### Leçon — Outreach X goldenfarfr
- **Constat** : feedback communauté positif
- **Mieux** : defer outreach
- **Ship core** : defer
- **Statut** : pending
"""


def test_parse_lessons_empty():
    assert parse_lessons("") == []
    assert parse_lessons("  \n") == []


def test_parse_lessons_fields():
    rows = parse_lessons(SAMPLE)
    assert len(rows) == 3
    assert rows[0]["title"] == "Anti-Ollama fallback"
    assert rows[0]["ship"] == "reflection"
    assert rows[0]["status"] == "pending"
    assert rows[1]["status"] == "approved"
    assert rows[2]["ship"] == "defer"


def test_set_lesson_status_updates_existing():
    updated = _set_lesson_status(SAMPLE, 1, "approved")
    assert "Statut** : approved" in updated
    assert "Statut** : pending" not in updated.split("### Leçon — Anti")[1].split("###")[0]


def test_set_lesson_status_adds_missing(tmp_path):
    bare = "### Leçon — Test\n- **Ship core** : reflection\n"
    updated = _set_lesson_status(bare, 1, "done")
    assert "- **Statut** : done" in updated


def test_approve_lesson(monkeypatch, tmp_path):
    pending = tmp_path / "pending-lessons.md"
    pending.write_text(SAMPLE, encoding="utf-8")
    monkeypatch.setattr("apply_pending_lessons.PENDING_PATH", pending)
    assert approve_lesson(1)
    text = pending.read_text(encoding="utf-8")
    assert "Anti-Ollama" in text
    assert "Statut** : approved" in text.split("Anti-Ollama")[1].split("###")[0]


def test_list_lessons(monkeypatch, tmp_path):
    pending = tmp_path / "pending-lessons.md"
    pending.write_text(SAMPLE, encoding="utf-8")
    monkeypatch.setattr("apply_pending_lessons.PENDING_PATH", pending)
    rows = list_lessons()
    assert len(rows) == 3


def test_apply_defer():
    lesson = {"ship": "defer", "title": "x", "fields": {}}
    target, ship = apply_lesson(lesson)
    assert target == "defer"
    assert ship == "skipped"


def test_apply_reflection(monkeypatch):
    captured: list[str] = []

    def fake_append(body, *, context="", outcome=""):
        captured.append(body)

    monkeypatch.setattr("apply_pending_lessons.bootstrap_aria_core_runtime", lambda: None)
    import aria_core.memory.reflection as ref_mod

    monkeypatch.setattr(ref_mod, "append_reflection", fake_append)
    lesson = {
        "title": "Test reflection",
        "fields": {"Constat": "c", "Tu as fait X": "x", "Mieux": "y"},
        "ship": "reflection",
    }
    target, ship = apply_lesson(lesson)
    assert target == "reflection"
    assert ship == "reflection"
    assert captured and "Test reflection" in captured[0]


def test_apply_pitfall(monkeypatch):
    calls: list[dict] = []

    def fake_append(entry):
        calls.append(entry)
        return True

    import aria_core.knowledge.operator_runbook as orb

    monkeypatch.setattr(orb, "append_pitfall_if_new", fake_append)
    lesson = {
        "title": "ACP sans CLI",
        "fields": {"Constat": "hallucination", "Mieux": "acp-cli", "Tu as fait X": "LLM seul"},
        "ship": "pitfall",
    }
    target, ship = apply_lesson(lesson)
    assert target == "pitfall"
    assert calls[0]["lesson"] == "hallucination"
    assert calls[0]["fix"] == "acp-cli"


def test_apply_collegue(tmp_path, monkeypatch):
    collegue = tmp_path / "COLLEGUE.md"
    collegue.write_text("# COLLEGUE\n\n## Journal\n\n| Date | Décision |\n|------|----------|\n", encoding="utf-8")
    monkeypatch.setattr("apply_pending_lessons.COLLEGUE_PATH", collegue)
    lesson = {
        "title": "Décision test",
        "fields": {"Mieux": "ship reflection d'abord"},
        "ship": "collegue",
    }
    target, _ = apply_lesson(lesson)
    assert target == "collegue"
    text = collegue.read_text(encoding="utf-8")
    assert "Décision test" in text


def test_apply_skill_route(monkeypatch, tmp_path):
    written: list = []

    class FakeTask:
        pass

    def fake_write(task):
        written.append(task)
        return tmp_path / "ARIA-WORKER.md"

    import aria_core.aria_worker_queue as wq

    monkeypatch.setattr(wq, "WorkerTask", lambda **kw: type("T", (), kw)())
    monkeypatch.setattr(wq, "_write_task_to_local_md", fake_write)
    lesson = {
        "title": "Route ACP delete",
        "fields": {"Constat": "hallucination", "Mieux": "skill déterministe"},
        "block": "### Leçon — Route ACP delete",
        "ship": "skill_route",
    }
    target, ship = apply_lesson(lesson)
    assert target == "skill_route"
    assert written


def test_run_apply_approved_only(monkeypatch, tmp_path):
    pending = tmp_path / "pending-lessons.md"
    pending.write_text(SAMPLE, encoding="utf-8")
    monkeypatch.setattr("apply_pending_lessons.PENDING_PATH", pending)
    monkeypatch.setattr("apply_pending_lessons._append_journal", lambda _m: None)
    monkeypatch.setattr("apply_pending_lessons.bootstrap_aria_core_runtime", lambda: None)
    monkeypatch.setattr(
        "apply_pending_lessons._apply_reflection",
        lambda les: "reflection",
    )
    monkeypatch.setattr("sync_core_to_letta.run_sync", lambda: {"ok": True})

    report = run_apply(approved_only=True)
    assert report["ok"]
    assert report["reason"] == "applied"
    assert report["applied"] == 1
    assert report["items"][0]["title"] == "ACP delete déterministe"
    text = pending.read_text(encoding="utf-8")
    assert "ACP delete" in text
    assert "Statut** : done" in text.split("ACP delete")[1].split("###")[0]


def test_run_apply_nothing(monkeypatch, tmp_path):
    pending = tmp_path / "pending-lessons.md"
    pending.write_text(
        "### Leçon — Done\n- **Ship core** : reflection\n- **Statut** : done\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("apply_pending_lessons.PENDING_PATH", pending)
    report = run_apply(approved_only=True)
    assert report["ok"]
    assert report["reason"] == "nothing_to_apply"
    assert report["applied"] == 0