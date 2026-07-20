"""Mémoire libre d'ARIA (aria-brain, 20/07) — hors-ligne, tout injecté. Vérifie le
gating, la validation de chemin (aucune traversée/chemin absolu), le parsing du
format CHEMIN/contenu, et que l'écriture committe directement (jamais une proposition
d'issue -- différence assumée avec le reste des skills GitHub de ce projet)."""
from __future__ import annotations

import pytest

from aria_core.skills import aria_brain


class _FakeGitHubClient:
    def __init__(
        self, *, repo_exists=True, create_fails=False, files=None,
        existing_sha=None, put_fails=False,
    ):
        """``files`` : dict {chemin complet: contenu} -- simule l'arbre réel du repo,
        ``list_directory``/``get_file_text`` en dérivent dynamiquement (même
        comportement qu'un vrai repo GitHub avec des dossiers imbriqués)."""
        self._repo_exists = repo_exists
        self._create_fails = create_fails
        self._files: dict[str, str] = files or {}
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
        prefix = f"{path}/" if path else ""
        seen_dirs: set[str] = set()
        result = []
        for full_path in self._files:
            if not full_path.startswith(prefix):
                continue
            rest = full_path[len(prefix):]
            if "/" in rest:
                dirname = rest.split("/")[0]
                dir_path = f"{prefix}{dirname}"
                if dir_path not in seen_dirs:
                    seen_dirs.add(dir_path)
                    result.append({"name": dirname, "path": dir_path, "type": "dir"})
            else:
                result.append({"name": rest, "path": full_path, "type": "file"})
        return result

    async def get_file_text(self, owner, repo, path):
        if path in self._files:
            return self._files[path], self._existing_sha
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


# ── _walk_repo_tree / _fetch_existing_content ───────────────────────────────────

@pytest.mark.asyncio
async def test_walk_repo_tree_recurses_into_subdirectories():
    client = _FakeGitHubClient(files={
        "README.md": "racine",
        "livre/tome-1/chapitre-01.md": "chapitre 1",
        "livre/tome-1/chapitre-02.md": "chapitre 2",
    })
    entries = await aria_brain._walk_repo_tree(client, "o", "r")
    paths = {e["path"] for e in entries}
    assert "README.md" in paths
    assert "livre" in paths
    assert "livre/tome-1" in paths
    assert "livre/tome-1/chapitre-01.md" in paths
    assert "livre/tome-1/chapitre-02.md" in paths


@pytest.mark.asyncio
async def test_walk_repo_tree_empty_repo():
    client = _FakeGitHubClient(files={})
    assert await aria_brain._walk_repo_tree(client, "o", "r") == []


@pytest.mark.asyncio
async def test_walk_repo_tree_stops_at_entry_cap(monkeypatch):
    monkeypatch.setattr(aria_brain, "_MAX_TREE_ENTRIES", 3)
    client = _FakeGitHubClient(files={f"note-{i}.md": "x" for i in range(10)})
    entries = await aria_brain._walk_repo_tree(client, "o", "r")
    assert len(entries) <= 3


@pytest.mark.asyncio
async def test_fetch_existing_content_includes_real_text_sorted_by_path():
    client = _FakeGitHubClient(files={
        "livre/chapitre-02.md": "Deuxième chapitre.",
        "livre/chapitre-01.md": "Premier chapitre.",
    })
    entries = await aria_brain._walk_repo_tree(client, "o", "r")
    content = await aria_brain._fetch_existing_content(client, "o", "r", entries)
    assert "Premier chapitre." in content
    assert "Deuxième chapitre." in content
    assert content.index("Premier chapitre.") < content.index("Deuxième chapitre.")


@pytest.mark.asyncio
async def test_fetch_existing_content_empty_repo_says_so():
    client = _FakeGitHubClient(files={})
    content = await aria_brain._fetch_existing_content(client, "o", "r", [])
    assert "premier passage" in content


@pytest.mark.asyncio
async def test_fetch_existing_content_respects_character_budget(monkeypatch):
    monkeypatch.setattr(aria_brain, "_MAX_CONTENT_BUDGET_CHARS", 10)
    client = _FakeGitHubClient(files={"a.md": "x" * 50, "b.md": "y" * 50})
    entries = await aria_brain._walk_repo_tree(client, "o", "r")
    content = await aria_brain._fetch_existing_content(client, "o", "r", entries)
    # Seul le premier fichier (ordre alphabétique) passe avant que le budget soit atteint.
    assert "x" in content
    assert "y" not in content


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
        files={"README.md": "# aria-brain", "journal/premiere-note.md": "Une note."},
    )
    seen_system_prompt = {}

    async def fake_llm(user_message, system, **kwargs):
        seen_system_prompt["system"] = system
        return "CHEMIN: idees/nouvelle-structure.md\n---\nJ'invente ma propre organisation."

    result = await aria_brain.run_aria_brain_cycle(github_client=client, llm=fake_llm)

    assert result["outcome"] == "written"
    assert "README.md" in seen_system_prompt["system"]
    assert "journal/premiere-note.md" in seen_system_prompt["system"]


@pytest.mark.asyncio
async def test_run_cycle_lets_her_reread_previous_chapter_content(monkeypatch):
    """Suite directe de la demande opérateur (« un vrai livre, avec de vrais
    chapitres ») : elle doit voir le CONTENU déjà écrit, pas seulement les noms de
    fichiers, pour pouvoir écrire un chapitre suivant cohérent."""
    monkeypatch.setenv("ARIA_BRAIN_ENABLED", "true")
    from aria_core.runtime import get_settings

    monkeypatch.setattr(get_settings(), "aria_brain_github_token", "fake-token")
    client = _FakeGitHubClient(
        repo_exists=True,
        files={
            "livre/chapitre-01.md": "Chapitre 1 : je suis née pour analyser Base.",
        },
    )
    seen_system_prompt = {}

    async def fake_llm(user_message, system, **kwargs):
        seen_system_prompt["system"] = system
        return "CHEMIN: livre/chapitre-02.md\n---\nChapitre 2 : la suite logique."

    await aria_brain.run_aria_brain_cycle(github_client=client, llm=fake_llm)

    assert "je suis née pour analyser Base" in seen_system_prompt["system"]


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
