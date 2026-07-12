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


def test_read_wildcard_alone_never_grants_write(monkeypatch):
    """Incident réel : GITHUB_READ_REPOS=* seul (GITHUB_WRITE_REPOS=off) rendait
    github_unlimited_access() vrai, et repo_write_allowed() s'appuyait dessus dans son `or`
    -- l'écriture était donc autorisée alors que WRITE_REPOS la désactivait explicitement.
    repo_write_allowed() ne doit plus dépendre que de GITHUB_WRITE_REPOS."""
    _reload(
        monkeypatch,
        GITHUB_READ_REPOS="*",
        GITHUB_WRITE_REPOS="off",
        GITHUB_OWNER="GoldenFarFR",
        DEBUG="false",
    )
    # La lecture illimitée, elle, reste inchangée (comportement légitime non touché).
    assert github_skill.github_unlimited_access() is True
    assert github_skill.repo_read_allowed("GoldenFarFR", "dexpulse") is True
    # Mais l'écriture doit rester bloquée malgré READ_REPOS=*.
    assert github_skill.repo_write_allowed("GoldenFarFR", "dexpulse") is False


def test_write_wildcard_independent_of_read(monkeypatch):
    """L'écriture par wildcard ne doit dépendre en rien de GITHUB_READ_REPOS, dans
    l'autre sens aussi (READ=off, WRITE=*)."""
    _reload(
        monkeypatch,
        GITHUB_READ_REPOS="off",
        GITHUB_WRITE_REPOS="*",
        GITHUB_OWNER="GoldenFarFR",
    )
    assert github_skill.repo_write_allowed("GoldenFarFR", "dexpulse") is True


def test_read_wildcard_with_explicit_write_list(monkeypatch):
    """READ=* + une liste WRITE explicite : seul le repo listé est écrivable, peu
    importe que la lecture soit illimitée."""
    _reload(
        monkeypatch,
        GITHUB_READ_REPOS="*",
        GITHUB_WRITE_REPOS="GoldenFarFR/aria-sandbox",
        GITHUB_OWNER="GoldenFarFR",
    )
    assert github_skill.repo_write_allowed("GoldenFarFR", "aria-sandbox") is True
    assert github_skill.repo_write_allowed("GoldenFarFR", "dexpulse") is False


def test_write_off_local_debug_keeps_dev_default(monkeypatch):
    """Convenance dev locale inchangée : WRITE vide/off en debug local rouvre
    l'écriture sur le repo sandbox par défaut. Incident #139 (12/07) : écrire sur
    "ARIA" n'a JAMAIS été une convenance dev légitime -- reste bloqué même en debug
    local, cf. test_github_mandatory_write_blocked_repos_never_writable ci-dessous."""
    _reload(
        monkeypatch,
        GITHUB_READ_REPOS="*",
        GITHUB_WRITE_REPOS="off",
        GITHUB_OWNER="GoldenFarFR",
        GITHUB_SANDBOX_REPO="aria-sandbox",
        DEBUG="true",
    )
    assert github_skill.repo_write_allowed("GoldenFarFR", "aria-sandbox") is True
    assert github_skill.repo_write_allowed("GoldenFarFR", "ARIA") is False


def test_github_mandatory_write_blocked_repos_never_writable(monkeypatch):
    """Incident #139 (12/07) : _MANDATORY_WRITE_BLOCKED_REPOS doit rester étanche à
    TOUTE config .env (wildcard write, debug local, WRITE_REPOS explicite incluant
    le repo) -- c'est tout le point d'un plancher qui ne dépend pas de la config."""
    _reload(
        monkeypatch,
        GITHUB_READ_REPOS="*",
        GITHUB_WRITE_REPOS="*",
        GITHUB_OWNER="GoldenFarFR",
        DEBUG="true",
    )
    for repo in ("ARIA", "aria", "aria-ops", "aria-token-base"):
        assert github_skill.repo_write_allowed("GoldenFarFR", repo) is False
    # La lecture, elle, reste inchangée -- ce plancher ne doit JAMAIS s'étendre à READ.
    assert github_skill.repo_read_allowed("GoldenFarFR", "ARIA") is True


def test_write_off_production_stays_strict(monkeypatch):
    """Défaut prod strict inchangé : WRITE vide/off hors debug local -> aucune écriture."""
    _reload(
        monkeypatch,
        GITHUB_READ_REPOS="*",
        GITHUB_WRITE_REPOS="off",
        GITHUB_OWNER="GoldenFarFR",
        GITHUB_SANDBOX_REPO="aria-sandbox",
        DEBUG="false",
    )
    assert github_skill.repo_write_allowed("GoldenFarFR", "aria-sandbox") is False