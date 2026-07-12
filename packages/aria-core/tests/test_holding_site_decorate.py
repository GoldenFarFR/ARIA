import pytest

from aria_core.skills.holding_site_skill import (
    _patch_index_css,
    _patch_vanguard_site,
    execute_holding_site,
    wants_holding_site,
    wants_holding_site_decorate,
)
from aria_core.testing import reload_test_settings

VANGUARD_SNIPPET = """import { VanguardNav } from '../components/VanguardNav'

      <section className="relative min-h-screen flex flex-col justify-center pt-16">
        <Orb className="w-[480px] h-[480px] bg-[#c9a962]/12 -top-40 -left-32" />
        <Orb className="w-[360px] h-[360px] bg-[#8a7344]/10 top-1/3 -right-48 animate-vanguard-float-delayed" />
"""


def test_decorate_intent_shooting_star():
    msg = "ajoute une étoile filante sur la page d'accueil de vanguard"
    assert wants_holding_site_decorate(msg) is True
    assert wants_holding_site(msg) is True


def test_patch_vanguard_site_injects_component():
    patched = _patch_vanguard_site(VANGUARD_SNIPPET)
    assert patched is not None
    assert "ShootingStar" in patched
    assert _patch_vanguard_site(patched) is None


def test_patch_index_css_appends_keyframes():
    css = "@keyframes vanguard-float {}\n"
    patched = _patch_index_css(css)
    assert patched is not None
    assert "shooting-star-fly" in patched
    assert _patch_index_css(patched) is None


@pytest.mark.asyncio
async def test_execute_shooting_star_write_blocked_even_with_explicit_write_repos(monkeypatch, tmp_path):
    """Incident #139 (12/07) : le commit direct sur ARIA (sans PR, sans revue) a été
    retiré -- github_skill._MANDATORY_WRITE_BLOCKED_REPOS bloque désormais l'écriture
    sur "aria" EN DUR, même quand GITHUB_WRITE_REPOS l'inclut explicitement (c'est
    justement ce que ce test vérifiait comme "succès" avant l'incident). La lecture/
    audit du site reste intacte (repo_read_allowed non touché) -- seul le commit direct
    est coupé."""
    reload_test_settings(
        monkeypatch,
        GITHUB_TOKEN="test",
        GITHUB_READ_REPOS="GoldenFarFR/ARIA",
        GITHUB_WRITE_REPOS="GoldenFarFR/ARIA",
        GITHUB_OWNER="GoldenFarFR",
    )

    puts: list[tuple[str, str]] = []

    class FakeClient:
        async def repo_exists(self, owner: str, repo: str) -> bool:
            return repo == "ARIA"

        async def get_file_text(self, owner: str, repo: str, path: str) -> tuple[str, str | None]:
            if path.endswith("VanguardSite.tsx"):
                return (VANGUARD_SNIPPET, "sha-site")
            if path.endswith("index.css"):
                return ("@keyframes vanguard-float {}\n", "sha-css")
            if path.endswith("ShootingStar.tsx"):
                return ("", None)
            return ("", None)

        async def put_file(self, owner, repo, path, content, message, sha=None):
            puts.append((path, message))
            return {"commit": {"sha": "abc123", "html_url": f"https://github.com/commit/{path}"}}

    import aria_core.skills.holding_site_skill as mod

    monkeypatch.setattr(mod, "GitHubClient", lambda _t: FakeClient())
    monkeypatch.setattr(mod, "memory_dir", lambda: tmp_path / "memory")
    monkeypatch.setattr("aria_core.skills.github_skill.github_configured", lambda: True)

    out, data = await execute_holding_site(
        "ajoute une étoile filante sur la page d'accueil de aria-vanguard",
        lang="fr",
    )
    # La LECTURE a bien eu lieu (patch calculé) mais aucune écriture n'est partie.
    assert data.get("patch_complete") is True
    assert data.get("write_denied") is True
    assert not data.get("committed")
    assert puts == []
    assert "refusée" in out.lower() or "refuse" in out.lower()


@pytest.mark.asyncio
async def test_execute_shooting_star_write_denied(monkeypatch, tmp_path):
    reload_test_settings(
        monkeypatch,
        GITHUB_TOKEN="test",
        GITHUB_READ_REPOS="GoldenFarFR/ARIA",
        GITHUB_WRITE_REPOS="GoldenFarFR/aria-sandbox",
        GITHUB_OWNER="GoldenFarFR",
    )

    class FakeClient:
        async def repo_exists(self, owner: str, repo: str) -> bool:
            return True

        async def get_file_text(self, owner: str, repo: str, path: str) -> tuple[str, str | None]:
            if path.endswith("VanguardSite.tsx"):
                return (VANGUARD_SNIPPET, "sha-site")
            if path.endswith("index.css"):
                return ("@keyframes vanguard-float {}\n", "sha-css")
            return ("", None)

    import aria_core.skills.holding_site_skill as mod

    monkeypatch.setattr(mod, "GitHubClient", lambda _t: FakeClient())
    monkeypatch.setattr(mod, "memory_dir", lambda: tmp_path / "memory")
    monkeypatch.setattr("aria_core.skills.github_skill.github_configured", lambda: True)

    out, data = await execute_holding_site(
        "ajoute une étoile filante sur la page d'accueil de vanguard",
        lang="fr",
    )
    assert data.get("write_denied") is True
    assert "refusée" in out.lower() or "refuse" in out.lower()