"""Tests ARIA-Critique (Letta-2)."""
from __future__ import annotations

import json

import pytest

from letta2_critique import (
    _append_pending_lessons,
    _context_hash,
    build_critique_context,
    run_critique,
)


def test_build_critique_context_nonempty():
    ctx = build_critique_context()
    assert isinstance(ctx, str)
    assert len(ctx) >= 50


def test_context_hash_stable():
    assert _context_hash("abc") == _context_hash("abc")
    assert _context_hash("abc") != _context_hash("abd")


def test_append_pending_lessons(tmp_path, monkeypatch):
    pending = tmp_path / "pending-lessons.md"
    monkeypatch.setattr("letta2_critique.PENDING_PATH", pending)
    _append_pending_lessons("### Leçon — test\n- **Mieux** : Y", engine="groq")
    assert pending.is_file()
    text = pending.read_text(encoding="utf-8")
    assert "Leçon" in text
    assert "groq" in text


def test_run_critique_dry_run():
    report = run_critique(dry_run=True, force=True)
    assert report.get("ok")
    assert report.get("reason") == "dry_run"


def test_run_critique_lessons(monkeypatch, tmp_path):
    pending = tmp_path / "pending-lessons.md"
    state = tmp_path / "state.json"
    monkeypatch.setattr("letta2_critique.PENDING_PATH", pending)
    monkeypatch.setattr("letta2_critique._state_path", lambda: state)
    monkeypatch.setattr(
        "letta2_critique.build_critique_context",
        lambda: "x" * 200 + "\n## Journal\n- 18h00 test",
    )
    monkeypatch.setattr(
        "letta2_critique._pick_critique_text",
        lambda _c: (
            "### Leçon — Anti-Ollama\n- **Mieux** : Groq seul\n- **Ship core** : pitfall",
            "groq",
        ),
    )
    monkeypatch.setattr("letta2_critique._sync_critique_to_archival", lambda _t: None)
    report = run_critique(force=True)
    assert report.get("reason") == "lessons"
    assert pending.is_file()
    data = json.loads(state.read_text(encoding="utf-8"))
    assert data.get("last_result") == "lessons"


def test_run_critique_aucune_lecon(monkeypatch, tmp_path):
    state = tmp_path / "state.json"
    monkeypatch.setattr("letta2_critique._state_path", lambda: state)
    monkeypatch.setattr(
        "letta2_critique.build_critique_context",
        lambda: "y" * 250,
    )
    monkeypatch.setattr(
        "letta2_critique._pick_critique_text",
        lambda _c: ("AUCUNE_LECON", "groq"),
    )
    report = run_critique(force=True)
    assert report.get("reason") == "aucune_lecon"