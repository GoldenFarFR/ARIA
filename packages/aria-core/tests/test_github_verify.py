"""Client GitHub (19/07, vérification du contenu d'un dépôt déclaré) -- aucun
appel réseau réel, httpx.AsyncClient mocké (même patron que test_rugcheck.py)."""
from __future__ import annotations

import pytest

from aria_core.services.github_verify import (
    GitHubRepoVerification,
    _parse_owner_repo,
    format_repo_verification,
    verify_repo,
)

REAL_PAYLOAD = {
    "created_at": "2026-02-09T20:44:49Z",
    "pushed_at": "2026-06-16T20:49:03Z",
    "stargazers_count": 121,
    "forks_count": 47,
    "fork": False,
    "archived": False,
}


class FakeResponse:
    def __init__(self, status_code: int, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class FakeClient:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, **kwargs):
        return self._response


def _patch_client(monkeypatch, response):
    monkeypatch.setattr(
        "aria_core.services.github_verify.httpx.AsyncClient", lambda **kw: FakeClient(response),
    )


def test_parse_owner_repo_variants():
    assert _parse_owner_repo("https://github.com/coinbase/agentic-wallet-skills") == (
        "coinbase", "agentic-wallet-skills",
    )
    assert _parse_owner_repo("https://github.com/foo/bar.git") == ("foo", "bar")  # .git strippé
    assert _parse_owner_repo("https://github.com/foo/bar/tree/main") == ("foo", "bar")
    assert _parse_owner_repo("not a github url") is None


def test_parse_owner_repo_tolerates_query_string_fragment_and_trailing_text():
    """Bug réel trouvé en revue croisée (19/07) : l'ancre `$` de fin de chaîne
    faisait échouer le parsing sur des variantes très courantes d'une URL
    GitHub copiée-collée (query string, fragment, texte de phrase autour)."""
    assert _parse_owner_repo("https://github.com/foo/bar?tab=readme-ov-file") == ("foo", "bar")
    assert _parse_owner_repo("https://github.com/foo/bar#readme") == ("foo", "bar")
    assert _parse_owner_repo("https://github.com/foo/bar,") == ("foo", "bar")
    assert _parse_owner_repo(
        "Check out our repo at https://github.com/foo/bar for more info!"
    ) == ("foo", "bar")


@pytest.mark.asyncio
async def test_verify_repo_real_schema_parses_correctly(monkeypatch):
    _patch_client(monkeypatch, FakeResponse(200, REAL_PAYLOAD))

    result = await verify_repo("https://github.com/coinbase/agentic-wallet-skills")

    assert result.available is True
    assert result.exists is True
    assert result.stargazers == 121
    assert result.is_fork is False
    assert result.is_archived is False
    assert result.age_days is not None and result.age_days > 0
    assert result.days_since_last_push is not None and result.days_since_last_push > 0


@pytest.mark.asyncio
async def test_verify_repo_404_is_exists_false_not_unavailable(monkeypatch):
    """Un vrai 404 confirmé -- jamais confondu avec une panne réseau."""
    _patch_client(monkeypatch, FakeResponse(404))

    result = await verify_repo("https://github.com/nonexistent/nonexistent")

    assert result.available is True
    assert result.exists is False


@pytest.mark.asyncio
async def test_verify_repo_other_http_error_is_unavailable(monkeypatch):
    _patch_client(monkeypatch, FakeResponse(500))

    result = await verify_repo("https://github.com/foo/bar")

    assert result.available is False
    assert result.exists is None


@pytest.mark.asyncio
async def test_verify_repo_network_exception_never_raises(monkeypatch):
    def _raise(**kw):
        raise RuntimeError("réseau down")

    monkeypatch.setattr("aria_core.services.github_verify.httpx.AsyncClient", _raise)

    result = await verify_repo("https://github.com/foo/bar")

    assert result.available is False


@pytest.mark.asyncio
async def test_verify_repo_unparseable_url_no_network_call(monkeypatch):
    def _fail_if_called(**kw):
        raise AssertionError("ne doit jamais être appelé, URL illisible")

    monkeypatch.setattr("aria_core.services.github_verify.httpx.AsyncClient", _fail_if_called)

    result = await verify_repo("not a url at all")

    assert result.available is False


@pytest.mark.asyncio
async def test_verify_repo_malformed_body_never_raises(monkeypatch):
    class _BadResponse(FakeResponse):
        def json(self):
            raise ValueError("bad json")

    _patch_client(monkeypatch, _BadResponse(200))

    result = await verify_repo("https://github.com/foo/bar")

    assert result.available is False


def test_format_repo_verification_unavailable():
    assert format_repo_verification(GitHubRepoVerification(available=False)) == "vérification indisponible"


def test_format_repo_verification_not_found():
    v = GitHubRepoVerification(available=True, exists=False)
    assert "introuvable" in format_repo_verification(v)


def test_format_repo_verification_full_signal():
    v = GitHubRepoVerification(
        available=True, exists=True, age_days=159, days_since_last_push=32, stargazers=121,
        is_fork=False, is_archived=False,
    )
    formatted = format_repo_verification(v)
    assert "159j" in formatted
    assert "32j" in formatted
    assert "121 étoiles" in formatted
    assert "fork" not in formatted.lower()


def test_format_repo_verification_flags_fork_and_archived():
    v = GitHubRepoVerification(available=True, exists=True, is_fork=True, is_archived=True)
    formatted = format_repo_verification(v)
    assert "fork" in formatted.lower()
    assert "ARCHIVÉ" in formatted
