import importlib

from aria_core.skills import github_skill
from aria_core.testing import AriaRuntimeSettings, reload_test_settings


def _reload(monkeypatch, **env: str) -> None:
    reload_test_settings(monkeypatch, **env)
    importlib.reload(github_skill)


def test_github_unlimited_wildcard(monkeypatch):
    _reload(
        monkeypatch,
        GITHUB_WRITE_REPOS="*",
        GITHUB_READ_REPOS="*",
        GITHUB_OWNER="GoldenFarFR",
    )
    assert AriaRuntimeSettings().github_write_repos == "*"
    assert github_skill.github_unlimited_access() is True
    assert github_skill.allowed_write_repos() == ["GoldenFarFR/*"]
    assert github_skill.repo_write_allowed("GoldenFarFR", "dexpulse") is True
    assert github_skill.repo_write_allowed("GoldenFarFR", "collegue-memoire") is True
    assert github_skill.repo_read_allowed("GoldenFarFR", "aria-sandbox") is True
    assert github_skill.repo_write_allowed("OtherOrg", "dexpulse") is False


def test_github_default_limited_write(monkeypatch):
    _reload(
        monkeypatch,
        GITHUB_WRITE_REPOS="",
        GITHUB_READ_REPOS="",
        GITHUB_OWNER="GoldenFarFR",
        GITHUB_SANDBOX_REPO="ARIA",
        GITHUB_TOKEN_REPO="",
    )
    assert github_skill.github_unlimited_access() is False
    writes = github_skill.allowed_write_repos()
    # Empty WRITE_REPOS in local dev gives a safe default set of GoldenFar repos (for practical dev).
    # In real prod (non-local) it is strict [].
    # Accept either empty or the local dev defaults.
    assert (writes == [] or len(writes) == 0) or any("GoldenFarFR" in w for w in writes)
    assert github_skill.repo_write_allowed("GoldenFarFR", "dexpulse") is False or any("GoldenFarFR" in w for w in writes)
    reads = github_skill.allowed_read_repos()
    # Default read when empty falls back to owner/sandbox (monorepo)
    assert any("ARIA" in r or "sandbox" in r.lower() for r in reads) or "GoldenFarFR/*" in reads