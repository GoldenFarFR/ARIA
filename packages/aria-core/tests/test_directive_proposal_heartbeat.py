"""Câblage heartbeat -> propose_directive (tâche #82) -- pilote autonome, gate OFF par
défaut, appelle propose_directive() tel quel (jamais de contournement du périmètre)."""
from __future__ import annotations

import pytest

from aria_core import aria_directives as ad
from aria_core import heartbeat
from aria_core.skills import directive_proposal as dp


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(ad, "DB_PATH", str(tmp_path / "directives.db"))
    monkeypatch.setattr(ad, "data_dir", lambda: tmp_path)
    monkeypatch.setattr("aria_core.paths.aria_db_path", lambda: tmp_path / "directives.db")
    monkeypatch.setenv("ARIA_DIRECTIVE_CHANNEL_ENABLED", "1")
    yield


def _one_candidate(path="foo/bar.py", line=12, text="ranger ce module"):
    return lambda: [{"key": f"{path}:{line}", "path": path, "line": line, "text": text}]


def test_directive_proposal_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ARIA_DIRECTIVE_PROPOSAL_ENABLED", raising=False)
    assert dp.directive_proposal_enabled() is False


def test_directive_proposal_enabled_with_explicit_flag(monkeypatch):
    monkeypatch.setenv("ARIA_DIRECTIVE_PROPOSAL_ENABLED", "1")
    assert dp.directive_proposal_enabled() is True


@pytest.mark.asyncio
async def test_cycle_skipped_when_disabled(monkeypatch):
    monkeypatch.delenv("ARIA_DIRECTIVE_PROPOSAL_ENABLED", raising=False)
    result = await dp.run_directive_proposal_cycle(scanner=_one_candidate())
    assert result["outcome"] == "skipped_disabled"
    assert await ad.list_directives() == []


@pytest.mark.asyncio
async def test_cycle_nothing_new_when_no_candidate(monkeypatch):
    monkeypatch.setenv("ARIA_DIRECTIVE_PROPOSAL_ENABLED", "1")
    result = await dp.run_directive_proposal_cycle(scanner=lambda: [])
    assert result["outcome"] == "nothing_new"


@pytest.mark.asyncio
async def test_cycle_calls_propose_directive_with_allowed_category(monkeypatch):
    monkeypatch.setenv("ARIA_DIRECTIVE_PROPOSAL_ENABLED", "1")
    notified = []

    async def notifier(text):
        notified.append(text)

    result = await dp.run_directive_proposal_cycle(
        scanner=_one_candidate(), notifier=notifier,
    )
    assert result["outcome"] == "ok"
    assert result["category"] in ad._DIRECTIVE_CATEGORIES
    assert result["title"]

    directives = await ad.list_directives()
    assert len(directives) == 1
    assert directives[0]["category"] in ad._DIRECTIVE_CATEGORIES
    assert directives[0]["status"] == "pending"
    assert notified and "auto-prop" in notified[0].lower()


@pytest.mark.asyncio
async def test_cycle_dedup_same_candidate_only_proposed_once(monkeypatch):
    monkeypatch.setenv("ARIA_DIRECTIVE_PROPOSAL_ENABLED", "1")
    scanner = _one_candidate()

    result1 = await dp.run_directive_proposal_cycle(scanner=scanner)
    assert result1["outcome"] == "ok"

    result2 = await dp.run_directive_proposal_cycle(scanner=scanner)
    assert result2["outcome"] == "nothing_new"

    assert len(await ad.list_directives()) == 1


@pytest.mark.asyncio
async def test_cycle_never_marks_seen_when_propose_directive_refuses(monkeypatch):
    # Canal producteur OFF (independant de ARIA_DIRECTIVE_PROPOSAL_ENABLED) -> refuse,
    # mais le candidat doit rester disponible pour un prochain cycle (pas de perte
    # silencieuse d'un candidat legitime a cause d'un gate different, temporairement clos).
    monkeypatch.setenv("ARIA_DIRECTIVE_PROPOSAL_ENABLED", "1")
    monkeypatch.delenv("ARIA_DIRECTIVE_CHANNEL_ENABLED", raising=False)
    scanner = _one_candidate()

    result = await dp.run_directive_proposal_cycle(scanner=scanner)
    assert result["outcome"] == "skipped"
    assert await ad.list_directives() == []

    # Re-active le canal producteur : le meme candidat doit encore etre proposable.
    monkeypatch.setenv("ARIA_DIRECTIVE_CHANNEL_ENABLED", "1")
    result2 = await dp.run_directive_proposal_cycle(scanner=scanner)
    assert result2["outcome"] == "ok"


def test_heartbeat_task_directive_proposal_cycle_disabled_by_default():
    tasks = {task.id: task for task in heartbeat.HEARTBEAT_TASKS}
    assert "directive_proposal_cycle" in tasks
    assert tasks["directive_proposal_cycle"].enabled is False
