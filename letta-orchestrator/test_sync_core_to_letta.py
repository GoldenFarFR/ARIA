"""Tests sync aria-core → Letta archival."""
from __future__ import annotations

import json

import pytest

from sync_core_to_letta import _hash_text, collect_passages, run_sync


def test_collect_passages_has_journal():
    rows = collect_passages()
    assert isinstance(rows, list)
    if rows:
        assert rows[0][1].startswith("[aria-core/")


def test_hash_dedup_stable():
    a = _hash_text("[aria-core/journal] test ligne")
    b = _hash_text("[aria-core/journal] test ligne")
    assert a == b


def test_run_sync_letta_down(monkeypatch):
    monkeypatch.setattr("sync_core_to_letta.is_letta_available", lambda: False)
    report = run_sync(dry_run=True)
    assert report["reason"] == "letta_down"


def test_run_sync_dry_run_inserts(monkeypatch, tmp_path):
    monkeypatch.setattr("sync_core_to_letta.is_letta_available", lambda: True)
    monkeypatch.setattr(
        "sync_core_to_letta._resolve_agent_ids",
        lambda: ["agent-test-1"],
    )
    monkeypatch.setattr(
        "sync_core_to_letta.collect_passages",
        lambda: [("journal", "[aria-core/journal] 18h00 — test sync letta")],
    )
    state_file = tmp_path / "sync-letta-state.json"
    monkeypatch.setattr("sync_core_to_letta._state_path", lambda: state_file)
    report = run_sync(dry_run=True)
    assert report["inserted"] == 1


def test_run_sync_live_mock(monkeypatch, tmp_path):
    monkeypatch.setattr("sync_core_to_letta.is_letta_available", lambda: True)
    monkeypatch.setattr(
        "sync_core_to_letta._resolve_agent_ids",
        lambda: ["agent-ouvrier"],
    )
    monkeypatch.setattr(
        "sync_core_to_letta.collect_passages",
        lambda: [("reflection", "[aria-core/reflection] ouvrier/success — ACP ok")],
    )
    state_file = tmp_path / "sync-letta-state.json"
    monkeypatch.setattr("sync_core_to_letta._state_path", lambda: state_file)
    captured: list[str] = []

    def fake_insert(agent_id, text):
        captured.append(text)
        return [{"id": "mem-1"}]

    monkeypatch.setattr("sync_core_to_letta.insert_archival_memory", fake_insert)
    report = run_sync(dry_run=False)
    assert report["inserted"] == 1
    assert captured
    assert state_file.is_file()
    data = json.loads(state_file.read_text(encoding="utf-8"))
    assert data.get("last_sync_at")