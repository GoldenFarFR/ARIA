"""Boîte de dépôt de connaissance — propose une ISSUE GitHub (jamais un commit/fusion),
hors-ligne, tout injecté."""
from __future__ import annotations

import json

import pytest

from aria_core.skills import knowledge_inbox as ki


class _FakeGitHubClient:
    def __init__(self, *, entries=None, files=None, raises_list=None, raises_create=None):
        self.entries = entries or []
        self.files = files or {}
        self.raises_list = raises_list
        self.raises_create = raises_create
        self.calls: list[dict] = []

    async def list_directory(self, owner, repo, path):
        if self.raises_list:
            raise self.raises_list
        return self.entries

    async def get_file_text(self, owner, repo, path):
        return self.files.get(path, ("", None))

    async def create_issue(self, owner, repo, title, body, *, labels=None):
        if self.raises_create:
            raise self.raises_create
        self.calls.append({"owner": owner, "repo": repo, "title": title, "body": body, "labels": labels})
        return {"html_url": f"https://github.com/{owner}/{repo}/issues/7", "number": 7}


def _good_llm(title="Ajouter critere smart-money Z", body="Resume: ... Fichier cible: ...", actionable=True):
    async def llm(prompt, system, max_tokens=700):
        return json.dumps({"title": title, "body": body, "actionable": actionable})
    return llm


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr("aria_core.paths.aria_db_path", lambda: tmp_path / "inbox_test.db")
    yield


def test_disabled_without_github_token(monkeypatch):
    monkeypatch.setattr("aria_core.skills.github_skill.github_configured", lambda: False)
    assert ki.knowledge_inbox_enabled() is False


def test_disabled_without_explicit_flag(monkeypatch):
    monkeypatch.setattr("aria_core.skills.github_skill.github_configured", lambda: True)
    monkeypatch.delenv("ARIA_KNOWLEDGE_INBOX_ENABLED", raising=False)
    assert ki.knowledge_inbox_enabled() is False


def test_enabled_when_token_and_flag_both_set(monkeypatch):
    monkeypatch.setattr("aria_core.skills.github_skill.github_configured", lambda: True)
    monkeypatch.setenv("ARIA_KNOWLEDGE_INBOX_ENABLED", "1")
    assert ki.knowledge_inbox_enabled() is True


def test_pick_next_candidate_skips_readme_and_dotfiles():
    entries = [
        {"name": "README.md"},
        {"name": ".gitkeep"},
        {"name": "notes-methode.md"},
    ]
    assert ki._pick_next_candidate(entries, set()) == "docs/aria-learning-inbox/notes-methode.md"


def test_pick_next_candidate_skips_already_processed():
    entries = [{"name": "a.md"}, {"name": "b.md"}]
    processed = {"docs/aria-learning-inbox/a.md"}
    assert ki._pick_next_candidate(entries, processed) == "docs/aria-learning-inbox/b.md"


def test_pick_next_candidate_none_when_all_done():
    entries = [{"name": "README.md"}]
    assert ki._pick_next_candidate(entries, set()) is None


@pytest.mark.asyncio
async def test_cycle_skipped_when_disabled(monkeypatch):
    monkeypatch.setattr("aria_core.skills.github_skill.github_configured", lambda: False)
    result = await ki.run_knowledge_inbox_cycle()
    assert result["outcome"] == "skipped_disabled"


@pytest.mark.asyncio
async def test_cycle_nothing_new(monkeypatch):
    monkeypatch.setattr("aria_core.skills.github_skill.github_configured", lambda: True)
    monkeypatch.setenv("ARIA_KNOWLEDGE_INBOX_ENABLED", "1")
    fake_client = _FakeGitHubClient(entries=[{"name": "README.md"}])

    result = await ki.run_knowledge_inbox_cycle(github_client=fake_client)
    assert result["outcome"] == "nothing_new"
    assert fake_client.calls == []


@pytest.mark.asyncio
async def test_cycle_opens_knowledge_issue_never_a_commit(monkeypatch):
    monkeypatch.setattr("aria_core.skills.github_skill.github_configured", lambda: True)
    monkeypatch.setenv("ARIA_KNOWLEDGE_INBOX_ENABLED", "1")
    path = "docs/aria-learning-inbox/notes-methode.md"
    fake_client = _FakeGitHubClient(
        entries=[{"name": "README.md"}, {"name": "notes-methode.md"}],
        files={path: ("Une methode interessante pour filtrer les faux positifs.", "sha1")},
    )
    notified = []

    async def notifier(text):
        notified.append(text)

    result = await ki.run_knowledge_inbox_cycle(
        llm=_good_llm(), github_client=fake_client, notifier=notifier,
    )
    assert result["outcome"] == "ok"
    assert result["path"] == path
    assert len(fake_client.calls) == 1
    call = fake_client.calls[0]
    assert call["labels"] == ["aria-knowledge-proposal"]
    assert "revue humaine requise" in call["body"]
    assert notified and "https://github.com" in notified[0]
    assert not hasattr(fake_client, "create_pull_request")
    assert not hasattr(fake_client, "create_commit")

    # note deja traitee -> pas reproposee au tour suivant
    result2 = await ki.run_knowledge_inbox_cycle(llm=_good_llm(), github_client=fake_client)
    assert result2["outcome"] == "nothing_new"
    assert len(fake_client.calls) == 1


@pytest.mark.asyncio
async def test_cycle_empty_note_marked_processed_no_issue(monkeypatch):
    monkeypatch.setattr("aria_core.skills.github_skill.github_configured", lambda: True)
    monkeypatch.setenv("ARIA_KNOWLEDGE_INBOX_ENABLED", "1")
    path = "docs/aria-learning-inbox/vide.md"
    fake_client = _FakeGitHubClient(
        entries=[{"name": "vide.md"}],
        files={path: ("   \n  ", "sha1")},
    )

    result = await ki.run_knowledge_inbox_cycle(github_client=fake_client)
    assert result["outcome"] == "empty_skipped"
    assert fake_client.calls == []


@pytest.mark.asyncio
async def test_cycle_not_actionable_marked_processed_no_issue(monkeypatch):
    monkeypatch.setattr("aria_core.skills.github_skill.github_configured", lambda: True)
    monkeypatch.setenv("ARIA_KNOWLEDGE_INBOX_ENABLED", "1")
    path = "docs/aria-learning-inbox/vague.md"
    fake_client = _FakeGitHubClient(
        entries=[{"name": "vague.md"}],
        files={path: ("Des reflexions vagues sans rien de concret.", "sha1")},
    )

    result = await ki.run_knowledge_inbox_cycle(
        llm=_good_llm(actionable=False), github_client=fake_client,
    )
    assert result["outcome"] == "not_actionable"
    assert fake_client.calls == []


@pytest.mark.asyncio
async def test_cycle_generation_failure_does_not_open_issue(monkeypatch):
    monkeypatch.setattr("aria_core.skills.github_skill.github_configured", lambda: True)
    monkeypatch.setenv("ARIA_KNOWLEDGE_INBOX_ENABLED", "1")
    path = "docs/aria-learning-inbox/a.md"
    fake_client = _FakeGitHubClient(entries=[{"name": "a.md"}], files={path: ("contenu", "sha1")})

    async def broken_llm(prompt, system, max_tokens=700):
        return None

    result = await ki.run_knowledge_inbox_cycle(llm=broken_llm, github_client=fake_client)
    assert result["outcome"] == "generation_failed"
    assert fake_client.calls == []


@pytest.mark.asyncio
async def test_cycle_list_directory_error_is_logged_not_raised(monkeypatch):
    monkeypatch.setattr("aria_core.skills.github_skill.github_configured", lambda: True)
    monkeypatch.setenv("ARIA_KNOWLEDGE_INBOX_ENABLED", "1")
    fake_client = _FakeGitHubClient(raises_list=RuntimeError("404 not found"))

    result = await ki.run_knowledge_inbox_cycle(github_client=fake_client)
    assert result["outcome"] == "error"
    assert "404" in result["error"]
