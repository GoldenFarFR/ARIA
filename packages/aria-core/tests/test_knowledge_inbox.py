"""Boîte de dépôt de connaissance — propose une ISSUE GitHub (jamais un commit/fusion),
hors-ligne, tout injecté."""
from __future__ import annotations

import json

import pytest

from aria_core.skills import knowledge_inbox as ki


class _FakeGitHubClient:
    def __init__(self, *, entries=None, files=None, raises_list=None, raises_create=None):
        self.entries = entries or []
        self.files = files or {}
        self.raises_list = raises_list
        self.raises_create = raises_create
        self.calls: list[dict] = []

    async def list_directory(self, owner, repo, path):
        if self.raises_list:
            raise self.raises_list
        return self.entries

    async def get_file_text(self, owner, repo, path):
        return self.files.get(path, ("", None))

    async def create_issue(self, owner, repo, title, body, *, labels=None):
        if self.raises_create:
            raise self.raises_create
        self.calls.append({"owner": owner, "repo": repo, "title": title, "body": body, "labels": labels})
        return {"html_url": f"https://github.com/{owner}/{repo}/issues/7", "number": 7}


def _good_llm(title="Ajouter critere smart-money Z", body="Resume: ... Fichier cible: ...", actionable=True):
    async def llm(prompt, system, max_tokens=700):
        return json.dumps({"title": title, "body": body, "actionable": actionable})
    return llm


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr("aria_core.paths.aria_db_path", lambda: tmp_path / "inbox_test.db")
    yield


def test_disabled_without_github_token(monkeypatch):
    monkeypatch.setattr("aria_core.skills.github_skill.github_configured", lambda: False)
    assert ki.knowledge_inbox_enabled() is False


def test_disabled_without_explicit_flag(monkeypatch):
    monkeypatch.setattr("aria_core.skills.github_skill.github_configured", lambda: True)
    monkeypatch.delenv("ARIA_KNOWLEDGE_INBOX_ENABLED", raising=False)
    assert ki.knowledge_inbox_enabled() is False


def test_enabled_when_token_and_flag_both_set(monkeypatch):
    monkeypatch.setattr("aria_core.skills.github_skill.github_configured", lambda: True)
    monkeypatch.setenv("ARIA_KNOWLEDGE_INBOX_ENABLED", "1")
    assert ki.knowledge_inbox_enabled() is True


def test_pick_next_candidate_skips_readme_and_dotfiles():
    entries = [
        {"name": "README.md"},
        {"name": ".gitkeep"},
        {"name": "notes-methode.md"},
    ]
    assert ki._pick_next_candidate(entries, set()) == "docs/aria-learning-inbox/notes-methode.md"


def test_pick_next_candidate_skips_already_processed():
    entries = [{"name": "a.md"}, {"name": "b.md"}]
    processed = {"docs/aria-learning-inbox/a.md"}
    assert ki._pick_next_candidate(entries, processed) == "docs/aria-learning-inbox/b.md"


def test_pick_next_candidate_none_when_all_done():
    entries = [{"name": "README.md"}]
    assert ki._pick_next_candidate(entries, set()) is None


@pytest.mark.asyncio
async def test_cycle_skipped_when_disabled(monkeypatch):
    monkeypatch.setattr("aria_core.skills.github_skill.github_configured", lambda: False)
    result = await ki.run_knowledge_inbox_cycle()
    assert result["outcome"] == "skipped_disabled"


@pytest.mark.asyncio
async def test_cycle_nothing_new(monkeypatch):
    monkeypatch.setattr("aria_core.skills.github_skill.github_configured", lambda: True)
    monkeypatch.setenv("ARIA_KNOWLEDGE_INBOX_ENABLED", "1")
    fake_client = _FakeGitHubClient(entries=[{"name": "README.md"}])

    result = await ki.run_knowledge_inbox_cycle(github_client=fake_client)
    assert result["outcome"] == "nothing_new"
    assert fake_client.calls == []


@pytest.mark.asyncio
async def test_cycle_opens_knowledge_issue_never_a_commit(monkeypatch):
    monkeypatch.setattr("aria_core.skills.github_skill.github_configured", lambda: True)
    monkeypatch.setenv("ARIA_KNOWLEDGE_INBOX_ENABLED", "1")
    path = "docs/aria-learning-inbox/notes-methode.md"
    fake_client = _FakeGitHubClient(
        entries=[{"name": "README.md"}, {"name": "notes-methode.md"}],
        files={path: ("Une methode interessante pour filtrer les faux positifs.", "sha1")},
    )
    notified = []

    async def notifier(text):
        notified.append(text)

    result = await ki.run_knowledge_inbox_cycle(
        llm=_good_llm(), github_client=fake_client, notifier=notifier,
    )
    assert result["outcome"] == "ok"
    assert result["path"] == path
    assert len(fake_client.calls) == 1
    call = fake_client.calls[0]
    assert call["labels"] == ["aria-knowledge-proposal"]
    assert "human review required" in call["body"]
    assert notified and "https://github.com" in notified[0]
    assert not hasattr(fake_client, "create_pull_request")
    assert not hasattr(fake_client, "create_commit")

    # note deja traitee -> pas reproposee au tour suivant
    result2 = await ki.run_knowledge_inbox_cycle(llm=_good_llm(), github_client=fake_client)
    assert result2["outcome"] == "nothing_new"
    assert len(fake_client.calls) == 1


@pytest.mark.asyncio
async def test_cycle_empty_note_marked_processed_no_issue(monkeypatch):
    monkeypatch.setattr("aria_core.skills.github_skill.github_configured", lambda: True)
    monkeypatch.setenv("ARIA_KNOWLEDGE_INBOX_ENABLED", "1")
    path = "docs/aria-learning-inbox/vide.md"
    fake_client = _FakeGitHubClient(
        entries=[{"name": "vide.md"}],
        files={path: ("   \n  ", "sha1")},
    )

    result = await ki.run_knowledge_inbox_cycle(github_client=fake_client)
    assert result["outcome"] == "empty_skipped"
    assert fake_client.calls == []


@pytest.mark.asyncio
async def test_cycle_not_actionable_marked_processed_no_issue(monkeypatch):
    monkeypatch.setattr("aria_core.skills.github_skill.github_configured", lambda: True)
    monkeypatch.setenv("ARIA_KNOWLEDGE_INBOX_ENABLED", "1")
    path = "docs/aria-learning-inbox/vague.md"
    fake_client = _FakeGitHubClient(
        entries=[{"name": "vague.md"}],
        files={path: ("Des reflexions vagues sans rien de concret.", "sha1")},
    )

    result = await ki.run_knowledge_inbox_cycle(
        llm=_good_llm(actionable=False), github_client=fake_client,
    )
    assert result["outcome"] == "not_actionable"
    assert fake_client.calls == []


@pytest.mark.asyncio
async def test_cycle_generation_failure_does_not_open_issue(monkeypatch):
    monkeypatch.setattr("aria_core.skills.github_skill.github_configured", lambda: True)
    monkeypatch.setenv("ARIA_KNOWLEDGE_INBOX_ENABLED", "1")
    path = "docs/aria-learning-inbox/a.md"
    fake_client = _FakeGitHubClient(entries=[{"name": "a.md"}], files={path: ("contenu", "sha1")})

    async def broken_llm(prompt, system, max_tokens=700):
        return None

    result = await ki.run_knowledge_inbox_cycle(llm=broken_llm, github_client=fake_client)
    assert result["outcome"] == "generation_failed"
    assert fake_client.calls == []
    # 20/07 -- changement de comportement assumé (voir _try_claim) : la réclamation
    # se fait maintenant AVANT l'appel LLM, donc une panne LLM claime quand même
    # la note (jamais retentée) -- le prix à payer pour fermer la fenêtre de
    # course qui produisait des issues dupliquées. Un vrai miss est préférable à
    # un doublon imprévisible.
    assert await ki._already_processed(path) is True


@pytest.mark.asyncio
async def test_try_claim_true_only_for_the_winning_attempt():
    """20/07 -- bug réel : issues #42/#43 créées 6 minutes d'écart depuis la MÊME
    note (Note du 2026-07-15, Clanker/GoPlus) -- l'ancien _mark_processed n'était
    appelé qu'APRÈS tout le travail LLM, laissant une large fenêtre de course où
    deux passages concurrents voyaient tous les deux _already_processed() ->
    False. Verrouille le contrat de base : True seulement pour le premier appel."""
    path = "docs/aria-learning-inbox/x.md"
    assert await ki._try_claim(path) is True
    assert await ki._try_claim(path) is False


@pytest.mark.asyncio
async def test_cycle_never_opens_a_second_issue_when_race_already_claimed(monkeypatch):
    """Reproduit l'incident exact (issues #42/#43) : au moment précis où CE cycle
    tente de réclamer la note, un passage concurrent l'a déjà réclamée en premier
    (``_try_claim`` renvoie False) -- même si _pick_next_candidate l'a choisie
    croyant qu'elle était encore libre. Aucun appel LLM, aucune issue."""
    monkeypatch.setattr("aria_core.skills.github_skill.github_configured", lambda: True)
    monkeypatch.setenv("ARIA_KNOWLEDGE_INBOX_ENABLED", "1")
    path = "docs/aria-learning-inbox/radar-clanker-goplus.md"
    fake_client = _FakeGitHubClient(
        entries=[{"name": "radar-clanker-goplus.md"}],
        files={path: ("Note du 2026-07-15 sur Clanker/GoPlus.", "sha1")},
    )

    async def lost_the_race(claimed_path):
        return False  # un autre passage a gagné la course entre-temps

    monkeypatch.setattr(ki, "_try_claim", lost_the_race)

    llm_calls = {"n": 0}

    async def counting_llm(prompt, system, max_tokens=700):
        llm_calls["n"] += 1
        return json.dumps({"title": "x", "body": "y", "actionable": True})

    result = await ki.run_knowledge_inbox_cycle(llm=counting_llm, github_client=fake_client)
    assert result["outcome"] == "lost_claim_race"
    assert llm_calls["n"] == 0
    assert fake_client.calls == []


def test_extract_referenced_paths_finds_backtick_file_paths():
    content = "Gap dans `vanguard/deploy.sh` et `deploy-vitrine.sh`, voir `knowledge/x.yaml`."
    assert ki._extract_referenced_paths(content) == [
        "vanguard/deploy.sh", "deploy-vitrine.sh", "knowledge/x.yaml",
    ]


def test_extract_referenced_paths_ignores_non_path_backticks():
    content = "Utilise `grep` pour chercher `TODO` dans le code, pas de fichier ici."
    assert ki._extract_referenced_paths(content) == []


def test_extract_referenced_paths_deduplicates_preserving_order():
    content = "`a/b.py` puis encore `a/b.py` puis `c/d.py`."
    assert ki._extract_referenced_paths(content) == ["a/b.py", "c/d.py"]


@pytest.mark.asyncio
async def test_current_file_states_fetches_and_labels_each_path():
    client = _FakeGitHubClient(files={
        "vanguard/deploy.sh": ("#!/bin/bash\nblue-green rollback", "sha1"),
        "deploy-vitrine.sh": ("#!/bin/bash\n.old restore", "sha2"),
    })
    result = await ki._current_file_states(
        client, "GoldenFarFR", ["vanguard/deploy.sh", "deploy-vitrine.sh"],
    )
    assert "vanguard/deploy.sh" in result
    assert "blue-green rollback" in result
    assert "deploy-vitrine.sh" in result
    assert ".old restore" in result


@pytest.mark.asyncio
async def test_current_file_states_skips_unfetchable_path_gracefully():
    class _PartialFailClient(_FakeGitHubClient):
        async def get_file_text(self, owner, repo, path):
            if path == "chemin/inexistant.sh":
                raise RuntimeError("404")
            return await super().get_file_text(owner, repo, path)

    client = _PartialFailClient(files={"vrai/fichier.sh": ("contenu reel", "sha1")})
    result = await ki._current_file_states(
        client, "GoldenFarFR", ["chemin/inexistant.sh", "vrai/fichier.sh"],
    )
    assert "chemin/inexistant.sh" not in result
    assert "contenu reel" in result


@pytest.mark.asyncio
async def test_current_file_states_caps_number_of_files_fetched():
    client = _FakeGitHubClient(files={
        f"f{i}.sh": (f"contenu{i}", "sha") for i in range(5)
    })
    result = await ki._current_file_states(
        client, "GoldenFarFR", [f"f{i}.sh" for i in range(5)],
    )
    fetched = sum(1 for i in range(5) if f"contenu{i}" in result)
    assert fetched == ki._MAX_REFERENCED_FILES


@pytest.mark.asyncio
async def test_cycle_prompt_includes_current_file_state_when_note_references_files(monkeypatch):
    """Reproduit le scenario reel de l'issue #31 : une note cite un fichier precis --
    l'etat ACTUEL de ce fichier doit atteindre le prompt envoye au LLM."""
    monkeypatch.setattr("aria_core.skills.github_skill.github_configured", lambda: True)
    monkeypatch.setenv("ARIA_KNOWLEDGE_INBOX_ENABLED", "1")
    note_path = "docs/aria-learning-inbox/2026-07-13-rollback-gap.md"
    fake_client = _FakeGitHubClient(
        entries=[{"name": "2026-07-13-rollback-gap.md"}],
        files={
            note_path: (
                "Gap trouve : `vanguard/deploy.sh` supprime l'ancien conteneur avant "
                "health-check, pas de rollback automatique.",
                "sha1",
            ),
            "vanguard/deploy.sh": (
                "# blue-green par alternance de ports (#154), rollback automatique "
                "si le health-check echoue avant toute suppression.",
                "sha2",
            ),
        },
    )
    captured = {}

    async def llm(prompt, system, max_tokens=700):
        captured["prompt"] = prompt
        return json.dumps({
            "title": "Gap rollback",
            "body": "La note decrit un gap deja corrige par #154 -- obsolete.",
            "actionable": False,
        })

    result = await ki.run_knowledge_inbox_cycle(llm=llm, github_client=fake_client)

    assert "vanguard/deploy.sh" in captured["prompt"]
    assert "blue-green par alternance de ports" in captured["prompt"]
    assert result["outcome"] == "not_actionable"
    assert fake_client.calls == []  # note perimee -> jamais publiee comme issue


@pytest.mark.asyncio
async def test_cycle_prompt_has_no_current_state_block_when_note_cites_nothing(monkeypatch):
    monkeypatch.setattr("aria_core.skills.github_skill.github_configured", lambda: True)
    monkeypatch.setenv("ARIA_KNOWLEDGE_INBOX_ENABLED", "1")
    path = "docs/aria-learning-inbox/idee-generale.md"
    fake_client = _FakeGitHubClient(
        entries=[{"name": "idee-generale.md"}],
        files={path: ("Une idee generale sans fichier cite.", "sha1")},
    )
    captured = {}

    async def llm(prompt, system, max_tokens=700):
        captured["prompt"] = prompt
        return json.dumps({"title": "t", "body": "b", "actionable": True})

    await ki.run_knowledge_inbox_cycle(llm=llm, github_client=fake_client)
    assert "État actuel des fichiers cités" not in captured["prompt"]


@pytest.mark.asyncio
async def test_cycle_list_directory_error_is_logged_not_raised(monkeypatch):
    monkeypatch.setattr("aria_core.skills.github_skill.github_configured", lambda: True)
    monkeypatch.setenv("ARIA_KNOWLEDGE_INBOX_ENABLED", "1")
    fake_client = _FakeGitHubClient(raises_list=RuntimeError("404 not found"))

    result = await ki.run_knowledge_inbox_cycle(github_client=fake_client)
    assert result["outcome"] == "error"
    assert "404" in result["error"]
