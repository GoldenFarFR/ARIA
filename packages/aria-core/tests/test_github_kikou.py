import importlib

import pytest

from aria_core.brain import detect_intent
from aria_core.models import SkillName
from aria_core.testing import reload_test_settings


def test_kikou_triggers_github_intent():
    assert detect_intent("est-ce que tu vois le repo kikou") == SkillName.GITHUB_SANDBOX


@pytest.mark.asyncio
async def test_kikou_repo_lookup(monkeypatch):
    reload_test_settings(
        monkeypatch,
        GITHUB_TOKEN="test",
        GITHUB_READ_REPOS="*",
        GITHUB_WRITE_REPOS="*",
    )

    class FakeClient:
        async def repo_exists(self, owner: str, repo: str) -> bool:
            return repo == "kikou"

    import aria_core.skills.github_skill as mod

    importlib.reload(mod)
    monkeypatch.setattr(mod, "GitHubClient", lambda _t: FakeClient())
    monkeypatch.setattr(mod, "github_configured", lambda: True)

    out, data = await mod.execute_github_sandbox("tu vois le repo kikou ?", lang="fr")
    assert data.get("exists") is True
    assert "kikou" in out.lower()