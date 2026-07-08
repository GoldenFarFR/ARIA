import pytest

from aria_core.brain import detect_intent
from aria_core.models import EntityType, SkillName
from aria_core import repertoire_db
from aria_core.skills.repertoire_skill import execute_manage_repertoire


@pytest.mark.asyncio
async def test_delete_repertoire_entry(tmp_path, monkeypatch):
    db = tmp_path / "aria.db"
    monkeypatch.setattr("aria_core.repertoire_db.DB_PATH", str(db))
    await repertoire_db.init_repertoire_db()

    item = await repertoire_db.create(name="Test Venture", description="temp")
    out, data = await execute_manage_repertoire("supprime du répertoire Test Venture", lang="fr")
    assert data["ok"] is True
    assert "Supprimé" in out
    assert await repertoire_db.get_by_id(item.id) is None


@pytest.mark.asyncio
async def test_cannot_delete_holding(tmp_path, monkeypatch):
    db = tmp_path / "aria.db"
    monkeypatch.setattr("aria_core.repertoire_db.DB_PATH", str(db))
    await repertoire_db.init_repertoire_db()
    structure = await repertoire_db.get_holding_structure()
    ok, reason, _ = await repertoire_db.delete_item(structure.holding.id)
    assert not ok
    assert "holding" in reason.lower()


@pytest.mark.asyncio
async def test_retired_codenames_not_seeded(tmp_path, monkeypatch):
    """Aria Market / DEXPulse are retired codenames — a fresh repertoire must not carry them."""
    db = tmp_path / "aria.db"
    monkeypatch.setattr("aria_core.repertoire_db.DB_PATH", str(db))
    await repertoire_db.init_repertoire_db()
    items = await repertoire_db.get_all()
    assert not any(i.slug in ("aria-market", "dexpulse") for i in items)


def test_detect_manage_repertoire_intent():
    assert detect_intent("supprime Orphan SaaS du répertoire") == SkillName.MANAGE_REPERTOIRE