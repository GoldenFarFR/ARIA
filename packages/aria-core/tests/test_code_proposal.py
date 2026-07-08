"""Propositions de code long-cours — ouvre une ISSUE GitHub (jamais une PR/commit),
hors-ligne, tout injecté."""
from __future__ import annotations

import json

import pytest

from aria_core.skills import code_proposal as cp


class _FakeGitHubClient:
    def __init__(self, *, raises: Exception | None = None):
        self.raises = raises
        self.calls: list[dict] = []

    async def create_issue(self, owner, repo, title, body, *, labels=None):
        if self.raises:
            raise self.raises
        self.calls.append({"owner": owner, "repo": repo, "title": title, "body": body, "labels": labels})
        return {"html_url": f"https://github.com/{owner}/{repo}/issues/1", "number": 1}


def _good_llm(title="Ameliorer le cache OHLCV", body="Probleme: ... Approche: ..."):
    async def llm(prompt, system, max_tokens=700):
        return json.dumps({"title": title, "body": body})
    return llm


@pytest.mark.asyncio
async def test_disabled_without_github_token(monkeypatch):
    monkeypatch.setattr("aria_core.skills.github_skill.github_configured", lambda: False)
    assert cp.code_proposal_enabled() is False


@pytest.mark.asyncio
async def test_disabled_without_explicit_flag(monkeypatch):
    monkeypatch.setattr("aria_core.skills.github_skill.github_configured", lambda: True)
    monkeypatch.delenv("ARIA_CODE_PROPOSAL_ENABLED", raising=False)
    assert cp.code_proposal_enabled() is False


@pytest.mark.asyncio
async def test_enabled_when_token_and_flag_both_set(monkeypatch):
    monkeypatch.setattr("aria_core.skills.github_skill.github_configured", lambda: True)
    monkeypatch.setenv("ARIA_CODE_PROPOSAL_ENABLED", "1")
    assert cp.code_proposal_enabled() is True


@pytest.mark.asyncio
async def test_generate_proposal_parses_json():
    result = await cp.generate_code_proposal(llm=_good_llm(), context="ctx")
    assert result == {"title": "Ameliorer le cache OHLCV", "body": "Probleme: ... Approche: ..."}


@pytest.mark.asyncio
async def test_generate_proposal_fails_closed_on_bad_json():
    async def broken_llm(prompt, system, max_tokens=700):
        return "pas du JSON valide"

    assert await cp.generate_code_proposal(llm=broken_llm, context="ctx") is None


@pytest.mark.asyncio
async def test_generate_proposal_fails_closed_when_llm_empty():
    async def empty_llm(prompt, system, max_tokens=700):
        return None

    assert await cp.generate_code_proposal(llm=empty_llm, context="ctx") is None


@pytest.mark.asyncio
async def test_cycle_skipped_when_disabled(monkeypatch):
    monkeypatch.setattr("aria_core.skills.github_skill.github_configured", lambda: False)
    result = await cp.run_code_proposal_cycle()
    assert result["outcome"] == "skipped_disabled"


@pytest.mark.asyncio
async def test_cycle_opens_issue_never_a_pr_or_commit(monkeypatch):
    monkeypatch.setattr("aria_core.skills.github_skill.github_configured", lambda: True)
    monkeypatch.setenv("ARIA_CODE_PROPOSAL_ENABLED", "1")
    fake_client = _FakeGitHubClient()
    notified = []

    async def notifier(text):
        notified.append(text)

    result = await cp.run_code_proposal_cycle(
        llm=_good_llm(), github_client=fake_client, notifier=notifier,
    )
    assert result["outcome"] == "ok"
    assert len(fake_client.calls) == 1
    call = fake_client.calls[0]
    assert call["repo"] == cp.TARGET_REPO
    assert call["labels"] == ["aria-proposal"]
    assert "revue humaine requise" in call["body"]
    assert len(notified) == 1
    assert "https://github.com" in notified[0]

    # Aucune methode de creation de commit/PR n'a jamais ete appelee sur le fake client :
    # seule create_issue existe sur ce double, donc tout appel a autre chose leverait deja
    # une AttributeError -- verifie explicitement l'absence de toute trace de PR/commit.
    assert not hasattr(fake_client, "create_pull_request")
    assert not hasattr(fake_client, "create_commit")


@pytest.mark.asyncio
async def test_cycle_generation_failure_does_not_open_issue(monkeypatch):
    monkeypatch.setattr("aria_core.skills.github_skill.github_configured", lambda: True)
    monkeypatch.setenv("ARIA_CODE_PROPOSAL_ENABLED", "1")
    fake_client = _FakeGitHubClient()

    async def broken_llm(prompt, system, max_tokens=700):
        return None

    result = await cp.run_code_proposal_cycle(llm=broken_llm, github_client=fake_client)
    assert result["outcome"] == "generation_failed"
    assert fake_client.calls == []


@pytest.mark.asyncio
async def test_cycle_github_error_is_logged_not_raised(monkeypatch):
    monkeypatch.setattr("aria_core.skills.github_skill.github_configured", lambda: True)
    monkeypatch.setenv("ARIA_CODE_PROPOSAL_ENABLED", "1")
    fake_client = _FakeGitHubClient(raises=RuntimeError("403 forbidden"))

    result = await cp.run_code_proposal_cycle(llm=_good_llm(), github_client=fake_client)
    assert result["outcome"] == "error"
    assert "403" in result["error"]
