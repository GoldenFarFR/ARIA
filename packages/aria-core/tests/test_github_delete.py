import importlib

import pytest

from aria_core.brain import detect_intent
from aria_core.testing import reload_test_settings
from aria_core.models import SkillName
from aria_core.skills.github_skill import (
    execute_github_sandbox,
    looks_like_repo_delete,
)


def test_delete_repo_intent_not_repertoire():
    assert looks_like_repo_delete("supprime repo kikou") is True
    assert looks_like_repo_delete("supprime le repo kikou") is True
    assert looks_like_repo_delete("supprime kikou du répertoire") is False
    assert detect_intent("supprime repo kikou") == SkillName.GITHUB_SANDBOX
    assert detect_intent("supprime le repo kikou") == SkillName.GITHUB_SANDBOX


@pytest.mark.asyncio
async def test_delete_repo_success(monkeypatch):
    reload_test_settings(
        monkeypatch,
        GITHUB_TOKEN="test",
        GITHUB_READ_REPOS="*",
        GITHUB_WRITE_REPOS="*",
    )

    deleted: list[str] = []

    class FakeClient:
        async def repo_exists(self, owner: str, repo: str) -> bool:
            return repo == "kikou"

        async def delete_repo(self, owner: str, repo: str) -> None:
            deleted.append(f"{owner}/{repo}")

    import aria_core.skills.github_skill as mod

    importlib.reload(mod)
    monkeypatch.setattr(mod, "GitHubClient", lambda _t: FakeClient())
    monkeypatch.setattr(mod, "github_configured", lambda: True)

    out, data = await mod.execute_github_sandbox("supprime repo kikou", lang="fr")
    assert data.get("deleted") is True
    assert deleted == ["GoldenFarFR/kikou"]
    assert "supprimé" in out.lower()


@pytest.mark.asyncio
async def test_delete_403_returns_permission_hint(monkeypatch):
    reload_test_settings(
        monkeypatch,
        GITHUB_TOKEN="test",
        GITHUB_READ_REPOS="*",
        GITHUB_WRITE_REPOS="*",
    )

    class FakeClient:
        async def repo_exists(self, owner: str, repo: str) -> bool:
            return repo == "kikou"

        async def delete_repo(self, owner: str, repo: str) -> None:
            raise RuntimeError('GitHub 403: {"message":"Must have admin rights to Repository."}')

        async def token_info(self) -> dict:
            return {"login": "GoldenFarFR", "scopes": ["read:user", "repo:status"], "fine_grained": False}

    import aria_core.skills.github_skill as mod

    importlib.reload(mod)
    monkeypatch.setattr(mod, "GitHubClient", lambda _t: FakeClient())
    monkeypatch.setattr(mod, "github_configured", lambda: True)

    out, data = await mod.execute_github_sandbox("supprime repo kikou", lang="fr")
    assert data.get("error") == "forbidden"
    assert "delete_repo" in out
    assert "403" in out or "admin" in out.lower()


@pytest.mark.asyncio
async def test_delete_protected_repo_refused(monkeypatch):
    reload_test_settings(
        monkeypatch,
        GITHUB_TOKEN="test",
        GITHUB_READ_REPOS="*",
        GITHUB_WRITE_REPOS="*",
        GITHUB_PROTECTED_REPOS="kikou",
    )

    import aria_core.skills.github_skill as mod

    importlib.reload(mod)
    monkeypatch.setattr(mod, "github_configured", lambda: True)

    out, data = await mod.execute_github_sandbox("delete repo kikou", lang="en")
    assert data.get("error") == "protected"
    assert "protected" in out.lower()