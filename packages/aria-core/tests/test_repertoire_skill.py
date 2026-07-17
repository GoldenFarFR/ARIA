"""skills/repertoire_skill.py -- aucune couverture jusqu'ici (seul get_repertoire_summary
était appelé indirectement, jamais réellement exercé -- toujours mocké ailleurs)."""
from __future__ import annotations

import pytest

from aria_core import repertoire_db
from aria_core.models import EntityType, RepertoireItemStatus
from aria_core.skills.repertoire_skill import (
    _extract_target_name,
    execute_develop_repertoire,
    execute_manage_repertoire,
    get_repertoire_summary,
    wants_manage_repertoire,
)


@pytest.fixture(autouse=True)
async def _isolated_repertoire_db(tmp_path, monkeypatch):
    monkeypatch.setattr(repertoire_db, "DB_PATH", str(tmp_path / "repertoire.db"))
    await repertoire_db.init_repertoire_db()
    yield


def test_wants_manage_repertoire_detects_delete_and_archive():
    assert wants_manage_repertoire("supprime le projet DEXPulse du répertoire")
    assert wants_manage_repertoire("archive cette entrée du repertoire")
    assert wants_manage_repertoire("delete this venture from the repertoire")


def test_wants_manage_repertoire_false_without_repertoire_context():
    assert not wants_manage_repertoire("supprime ce fichier du serveur")


def test_wants_manage_repertoire_false_without_action_verb():
    assert not wants_manage_repertoire("montre-moi le répertoire")


def test_extract_target_name_from_delete_phrase():
    assert _extract_target_name("supprime le projet DEXPulse du répertoire") == "DEXPulse"


def test_extract_target_name_from_archive_phrase():
    assert "Aria Market" in _extract_target_name("archive Aria Market du repertoire")


def test_extract_target_name_returns_empty_without_verb():
    assert _extract_target_name("montre-moi le répertoire") == ""


@pytest.mark.asyncio
async def test_get_repertoire_summary_only_holding_seeded():
    """init_repertoire_db() sème toujours la holding elle-même (Aria Vanguard ZHC,
    live) -- jamais un répertoire vraiment vide en pratique. La branche "aucune
    filiale" ne se déclenche que si la holding elle-même est absente (cas dégradé,
    testé séparément ci-dessous)."""
    text = await get_repertoire_summary(lang="fr")
    assert "1 projets" in text


@pytest.mark.asyncio
async def test_get_repertoire_summary_empty_when_no_items_at_all():
    """delete_item() protège la holding (deletion_blocked_reason) -- un répertoire
    réellement vide n'est donc pas atteignable via l'API publique en usage normal.
    Suppression SQL directe ici, uniquement pour exercer la branche "aucune
    filiale" côté skill (défense en profondeur, jamais sensée arriver seule)."""
    import aiosqlite

    async with aiosqlite.connect(repertoire_db.DB_PATH) as db:
        await db.execute("DELETE FROM repertoire")
        await db.commit()
    text_fr = await get_repertoire_summary(lang="fr")
    text_en = await get_repertoire_summary(lang="en")
    assert "Aucune filiale live" in text_fr
    assert "No subsidiary live" in text_en


@pytest.mark.asyncio
async def test_get_repertoire_summary_lists_projects():
    await repertoire_db.create("Projet A", status=RepertoireItemStatus.BUILDING)
    text = await get_repertoire_summary(lang="fr")
    assert "2 projets" in text  # holding + Projet A
    assert "Projet A" in text


@pytest.mark.asyncio
async def test_execute_manage_repertoire_help_without_target_lists_entries():
    await repertoire_db.create("Projet A", status=RepertoireItemStatus.IDEA)
    text, data = await execute_manage_repertoire("aide répertoire", lang="fr")
    assert data["action"] == "help"
    assert "Projet A" in text


@pytest.mark.asyncio
async def test_execute_manage_repertoire_not_found():
    text, data = await execute_manage_repertoire(
        "supprime le projet Fantome du répertoire", lang="fr",
    )
    assert data["ok"] is False
    assert "Aucune entrée" in text


@pytest.mark.asyncio
async def test_execute_manage_repertoire_ambiguous_multiple_matches():
    await repertoire_db.create("Alpha Test", status=RepertoireItemStatus.IDEA)
    await repertoire_db.create("Alpha Prod", status=RepertoireItemStatus.LIVE)
    text, data = await execute_manage_repertoire(
        "supprime le projet Alpha du répertoire", lang="fr",
    )
    assert data["ok"] is False
    assert "Plusieurs entrées" in text


@pytest.mark.asyncio
async def test_execute_manage_repertoire_deletes_matching_entry():
    await repertoire_db.create("Projet Unique", status=RepertoireItemStatus.IDEA)
    text, data = await execute_manage_repertoire(
        "supprime le projet Projet Unique du répertoire", lang="fr",
    )
    assert data["action"] == "delete"
    assert data["ok"] is True
    remaining = await repertoire_db.get_all()
    assert not any(i.name == "Projet Unique" for i in remaining)


@pytest.mark.asyncio
async def test_execute_manage_repertoire_archives_matching_entry():
    item = await repertoire_db.create("Projet A Archiver", status=RepertoireItemStatus.LIVE)
    text, data = await execute_manage_repertoire(
        f"archive le projet {item.name} du répertoire", lang="fr",
    )
    assert data["action"] == "archive"
    assert data["ok"] is True
    remaining = await repertoire_db.get_all()
    archived = next(i for i in remaining if i.name == item.name)
    assert archived.status == RepertoireItemStatus.ARCHIVED


@pytest.mark.asyncio
async def test_execute_manage_repertoire_english_not_found_translates_words():
    text, data = await execute_manage_repertoire(
        "delete the project Fantome from the repertoire", lang="en",
    )
    assert data["ok"] is False
    assert "No entry for" in text


@pytest.mark.asyncio
async def test_execute_develop_repertoire_only_holding_seeded():
    text, data = await execute_develop_repertoire(lang="fr")
    assert data["total"] == 1  # la holding elle-même, toujours seedée
    assert data["live"] == 1


@pytest.mark.asyncio
async def test_execute_develop_repertoire_flags_stale_dexpulse_entry():
    await repertoire_db.create("DEXPulse", status=RepertoireItemStatus.LIVE)
    text, data = await execute_develop_repertoire(lang="fr")
    assert any("ARCHIVER maintenant" in s for s in data["suggestions"])


@pytest.mark.asyncio
async def test_execute_develop_repertoire_counts_by_status():
    await repertoire_db.create("Live One", status=RepertoireItemStatus.LIVE)
    await repertoire_db.create("Building One", status=RepertoireItemStatus.BUILDING)
    await repertoire_db.create("Idea One", status=RepertoireItemStatus.IDEA)
    _text, data = await execute_develop_repertoire(lang="fr")
    assert data["live"] == 2  # holding (toujours live) + "Live One"
    assert data["building"] == 1
    assert data["ideas"] == 1
    assert data["total"] == 4  # holding + les 3 créées


@pytest.mark.asyncio
async def test_execute_develop_repertoire_english_branch():
    await repertoire_db.create("Building One", status=RepertoireItemStatus.BUILDING)
    text, _data = await execute_develop_repertoire(lang="en")
    assert "entries" in text
    assert "Recommended actions" in text
