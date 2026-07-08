import pytest

from aria_core.skills.holding_site_skill import (
    execute_holding_site,
    wants_holding_site,
    wants_holding_site_execute,
)
from aria_core.testing import reload_test_settings


def test_lancer_le_site_triggers_holding_skill():
    assert wants_holding_site_execute("Lancer le site") is True
    assert wants_holding_site("Lancer le site") is True


@pytest.mark.asyncio
async def test_execute_audit_without_write(monkeypatch, tmp_path):
    reload_test_settings(
        monkeypatch,
        GITHUB_TOKEN="test",
        GITHUB_READ_REPOS="GoldenFarFR/ARIA",
        GITHUB_WRITE_REPOS="GoldenFarFR/aria-sandbox",
        GITHUB_OWNER="GoldenFarFR",
    )

    class FakeClient:
        async def repo_exists(self, owner: str, repo: str) -> bool:
            return repo == "ARIA"

        async def get_file_text(self, owner: str, repo: str, path: str) -> tuple[str, str | None]:
            if "VanguardSite" in path or "FaqSection" in path or "VanguardNav" in path:
                return ("export const x = 1", "sha1")
            return ("", None)

        async def list_directory(self, owner: str, repo: str, path: str = "") -> list:
            return [{"name": "pages"}, {"name": "components"}]

    import aria_core.skills.holding_site_skill as mod

    monkeypatch.setattr(mod, "GitHubClient", lambda _t: FakeClient())
    monkeypatch.setattr(mod, "memory_dir", lambda: tmp_path / "memory")
    monkeypatch.setattr("aria_core.skills.github_skill.github_configured", lambda: True)

    out, data = await execute_holding_site("Lancer le site holding", lang="fr")
    assert data.get("audit_complete") is True
    assert data.get("write_denied") is True
    assert "ariavanguardzhc.com" in out
    assert "MVP" in out or "mvp" in out.lower()
    assert "commit" not in out.lower() or "pas de commit" in out.lower() or "refusée" in out.lower()