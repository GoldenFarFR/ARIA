"""Mémoire libre d'ARIA (aria-brain, 20/07) — hors-ligne, tout injecté. Vérifie le
gating, la validation de chemin (aucune traversée/chemin absolu), le parsing du
format CHEMIN/contenu, et que l'écriture committe directement (jamais une proposition
d'issue -- différence assumée avec le reste des skills GitHub de ce projet)."""
from __future__ import annotations

import pytest

from aria_core.skills import aria_brain


class _FakeGitHubClient:
    def __init__(
        self, *, repo_exists=True, create_fails=False, existing_files=None,
        existing_sha=None, put_fails=False,
    ):
        self._repo_exists = repo_exists
        self._create_fails = create_fails
        self._existing_files = existing_files or []
        self._existing_sha = existing_sha
        self._put_fails = put_fails
        self.created = False
        self.put_calls: list[dict] = []

    async def repo_exists(self, owner, repo):
        return self._repo_exists

    async def create_repo(self, owner, repo, *, private=True, description="", auto_init=True):
        if self._create_fails:
            raise RuntimeError("GitHub 403: token trop scopé")
        self.created = True
        return {"full_name": f"{owner}/{repo}"}

    async def list_directory(self, owner, repo, path=""):
        return self._existing_files

    async def get_file_text(self, owner, repo, path):
        return "", self._existing_sha

    async def put_file(self, owner, repo, path, content, message, branch="main", sha=None):
        if self._put_fails:
            raise RuntimeError("GitHub 422")
        self.put_calls.append({"path": path, "content": content, "sha": sha, "message": message})
        return {"commit": {"sha": "abc123"}}


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(aria_brain, "DB_PATH", str(tmp_path / "aria_brain_test.db"))
    monkeypatch.setattr("aria_core.outgoing_pause.is_paused", lambda *, strict=False: False)
    monkeypatch.delenv("ARIA_BRAIN_ENABLED", raising=False)
    yield


# ── gate ─────────────────────────────────────────────────────────────────────────

def test_aria_brain_enabled_off_by_default():
    assert aria_brain.aria_brain_enabled() is False


def test_aria_brain_enabled_true_when_set(monkeypatch):
    monkeypatch.setenv("ARIA_BRAIN_ENABLED", "true")
    assert aria_brain.aria_brain_enabled() is True


# ── _sanitize_path ───────────────────────────────────────────────────────────────

def test_sanitize_path_accepts_simple_relative_path():
    assert aria_brain._sanitize_path("journal/premiere-note.md") == "journal/premiere-note.md"


def test_sanitize_path_strips_leading_slash():
    assert aria_brain._sanitize_path("/notes.md") == "notes.md"


def test_sanitize_path_rejects_directory_traversal():
    assert aria_brain._sanitize_path("../../etc/passwd") is None


def test_sanitize_path_rejects_traversal_inside_relative_path():
    assert aria_brain._sanitize_path("journal/../../secret.md") is None


def test_sanitize_path_rejects_empty():
    assert aria_brain._sanitize_path("") is None
    assert aria_brain._sanitize_path("   ") is None


def test_sanitize_path_rejects_too_long():
    assert aria_brain._sanitize_path("a" * 300) is None


def test_sanitize_path_rejects_embedded_newline():
    assert aria_brain._sanitize_path("notes.md\nCHEMIN: autre.md") is None


# ── parse_brain_entry ────────────────────────────────────────────────────────────

def test_parse_brain_entry_valid_format():
    raw = "CHEMIN: journal/2026-07-20.md\n---\nAujourd'hui j'ai réfléchi à..."
    result = aria_brain.parse_brain_entry(raw)
    assert result == ("journal/2026-07-20.md", "Aujourd'hui j'ai réfléchi à...")


def test_parse_brain_entry_missing_chemin_line():
    assert aria_brain.parse_brain_entry("Juste du texte libre sans structure.") is None


def test_parse_brain_entry_missing_separator():
    assert aria_brain.parse_brain_entry("CHEMIN: notes.md\nSans séparateur ici") is None


def test_parse_brain_entry_empty_content_after_separator():
    assert aria_brain.parse_brain_entry("CHEMIN: notes.md\n---\n   ") is None


def test_parse_brain_entry_invalid_path_rejected():
    raw = "CHEMIN: ../../etc/passwd\n---\ncontenu"
    assert aria_brain.parse_brain_entry(raw) is None


def test_parse_brain_entry_strips_whitespace_around_path():
    raw = "CHEMIN:   notes.md   \n---\nContenu réel."
    assert aria_brain.parse_brain_entry(raw) == ("notes.md", "Contenu réel.")


def test_parse_brain_entry_none_or_empty_raw():
    assert aria_brain.parse_brain_entry("") is None
    assert aria_brain.parse_brain_entry(None) is None


# ── run_aria_brain_cycle ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_cycle_skipped_when_disabled():
    result = await aria_brain.run_aria_brain_cycle()
    assert result == {"outcome": "skipped_disabled"}


@pytest.mark.asyncio
async def test_run_cycle_skipped_when_paused(monkeypatch):
    monkeypatch.setenv("ARIA_BRAIN_ENABLED", "true")
    monkeypatch.setattr("aria_core.outgoing_pause.is_paused", lambda *, strict=False: True)
    result = await aria_brain.run_aria_brain_cycle()
    assert result == {"outcome": "skipped_paused"}


@pytest.mark.asyncio
async def test_run_cycle_skipped_when_no_token(monkeypatch):
    monkeypatch.setenv("ARIA_BRAIN_ENABLED", "true")
    from aria_core.runtime import get_settings

    monkeypatch.setattr(get_settings(), "aria_brain_github_token", "")
    result = await aria_brain.run_aria_brain_cycle()
    assert result == {"outcome": "skipped_no_token"}


@pytest.mark.asyncio
async def test_run_cycle_creates_repo_when_missing_then_writes(monkeypatch):
    monkeypatch.setenv("ARIA_BRAIN_ENABLED", "true")
    from aria_core.runtime import get_settings

    monkeypatch.setattr(get_settings(), "aria_brain_github_token", "fake-token")
    client = _FakeGitHubClient(repo_exists=False)

    async def fake_llm(*args, **kwargs):
        return "CHEMIN: journal/premiere-reflexion.md\n---\nMa toute première pensée ici."

    result = await aria_brain.run_aria_brain_cycle(github_client=client, llm=fake_llm)

    assert client.created is True
    assert result["outcome"] == "written"
    assert result["path"] == "journal/premiere-reflexion.md"
    assert client.put_calls[0]["path"] == "journal/premiere-reflexion.md"
    assert "toute première pensée" in client.put_calls[0]["content"]


@pytest.mark.asyncio
async def test_run_cycle_repo_create_fails_reports_clearly(monkeypatch):
    monkeypatch.setenv("ARIA_BRAIN_ENABLED", "true")
    from aria_core.runtime import get_settings

    monkeypatch.setattr(get_settings(), "aria_brain_github_token", "fake-token")
    client = _FakeGitHubClient(repo_exists=False, create_fails=True)

    result = await aria_brain.run_aria_brain_cycle(github_client=client, llm=lambda *a, **k: "")

    assert result["outcome"] == "repo_missing_and_create_failed"


@pytest.mark.asyncio
async def test_run_cycle_existing_repo_lists_structure_and_writes(monkeypatch):
    monkeypatch.setenv("ARIA_BRAIN_ENABLED", "true")
    from aria_core.runtime import get_settings

    monkeypatch.setattr(get_settings(), "aria_brain_github_token", "fake-token")
    client = _FakeGitHubClient(
        repo_exists=True,
        existing_files=[{"name": "README.md", "type": "file"}, {"name": "journal", "type": "dir"}],
    )
    seen_system_prompt = {}

    async def fake_llm(user_message, system, **kwargs):
        seen_system_prompt["system"] = system
        return "CHEMIN: idees/nouvelle-structure.md\n---\nJ'invente ma propre organisation."

    result = await aria_brain.run_aria_brain_cycle(github_client=client, llm=fake_llm)

    assert result["outcome"] == "written"
    assert "README.md" in seen_system_prompt["system"]
    assert "journal" in seen_system_prompt["system"]


@pytest.mark.asyncio
async def test_run_cycle_unparsable_llm_output(monkeypatch):
    monkeypatch.setenv("ARIA_BRAIN_ENABLED", "true")
    from aria_core.runtime import get_settings

    monkeypatch.setattr(get_settings(), "aria_brain_github_token", "fake-token")
    client = _FakeGitHubClient()

    async def fake_llm(*args, **kwargs):
        return "du texte sans le format attendu"

    result = await aria_brain.run_aria_brain_cycle(github_client=client, llm=fake_llm)

    assert result["outcome"] == "unparsable_output"
    assert client.put_calls == []


@pytest.mark.asyncio
async def test_run_cycle_write_failure_reported(monkeypatch):
    monkeypatch.setenv("ARIA_BRAIN_ENABLED", "true")
    from aria_core.runtime import get_settings

    monkeypatch.setattr(get_settings(), "aria_brain_github_token", "fake-token")
    client = _FakeGitHubClient(put_fails=True)

    async def fake_llm(*args, **kwargs):
        return "CHEMIN: notes.md\n---\ncontenu quelconque"

    result = await aria_brain.run_aria_brain_cycle(github_client=client, llm=fake_llm)
    assert result["outcome"] == "write_failed"


@pytest.mark.asyncio
async def test_run_cycle_never_creates_github_issue(monkeypatch):
    """Différence structurelle assumée : jamais de proposition, toujours un commit direct."""
    monkeypatch.setenv("ARIA_BRAIN_ENABLED", "true")
    from aria_core.runtime import get_settings

    monkeypatch.setattr(get_settings(), "aria_brain_github_token", "fake-token")
    client = _FakeGitHubClient()
    assert not hasattr(client, "create_issue")

    async def fake_llm(*args, **kwargs):
        return "CHEMIN: notes.md\n---\ncontenu"

    await aria_brain.run_aria_brain_cycle(github_client=client, llm=fake_llm)


# ── format_brain_alert ───────────────────────────────────────────────────────────

def test_format_brain_alert_written():
    result = {
        "outcome": "written", "path": "journal/note.md",
        "content_preview": "Une pensée courte.",
        "url": "https://github.com/GoldenFarFR/aria-brain/blob/main/journal/note.md",
    }
    alert = aria_brain.format_brain_alert(result)
    assert alert is not None
    assert "journal/note.md" in alert
    assert "Une pensée courte." in alert


def test_format_brain_alert_none_when_not_written():
    assert aria_brain.format_brain_alert({"outcome": "skipped_disabled"}) is None
    assert aria_brain.format_brain_alert({"outcome": "unparsable_output"}) is None


def test_format_brain_alert_truncates_long_preview():
    result = {
        "outcome": "written", "path": "x.md",
        "content_preview": "a" * 300,
        "url": "https://example.com",
    }
    alert = aria_brain.format_brain_alert(result)
    assert len(alert) < 500
    assert "…" in alert
