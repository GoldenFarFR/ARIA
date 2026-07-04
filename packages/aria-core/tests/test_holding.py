from datetime import datetime, timezone

import aiosqlite
import pytest

from aria_core import repertoire_db
from aria_core.models import EntityType


@pytest.mark.asyncio
async def test_holding_seed_and_structure(tmp_path, monkeypatch):
    db = tmp_path / "aria.db"
    monkeypatch.setattr("aria_core.repertoire_db.DB_PATH", str(db))

    await repertoire_db.init_repertoire_db()
    structure = await repertoire_db.get_holding_structure()

    assert structure is not None
    assert structure.holding.entity_type == EntityType.HOLDING
    assert structure.holding.slug == "aria-vanguard-zhc"
    assert any(s.slug == "aria-market" for s in structure.subsidiaries)
    assert any(s.slug == "dexpulse" and s.status.value == "archived" for s in structure.subsidiaries)
    assert all(s.parent_id == structure.holding.id for s in structure.subsidiaries)


@pytest.mark.asyncio
async def test_orphan_ventures_relinked_to_holding(tmp_path, monkeypatch):
    db = tmp_path / "aria.db"
    monkeypatch.setattr("aria_core.repertoire_db.DB_PATH", str(db))
    await repertoire_db.init_repertoire_db()

    orphan_id = "orphan-venture-id"
    async with aiosqlite.connect(str(db)) as conn:
        now = datetime.now(timezone.utc).isoformat()
        await conn.execute(
            """
            INSERT INTO repertoire
            (id, name, description, status, category, revenue_monthly, priority, tags,
             zhc_aligned, notes, created_at, updated_at, entity_type, parent_id, slug)
            VALUES (?, ?, ?, ?, ?, 0, 3, '', 1, '', ?, ?, 'venture', NULL, NULL)
            """,
            (orphan_id, "Orphan SaaS", "No parent", "idea", "saas", now, now),
        )
        await conn.commit()

    await repertoire_db.init_repertoire_db()
    item = await repertoire_db.get_by_id(orphan_id)
    structure = await repertoire_db.get_holding_structure()

    assert item is not None
    assert item.parent_id == structure.holding.id
    assert any(v.name == "Orphan SaaS" for v in structure.ventures)


@pytest.mark.asyncio
async def test_new_project_under_holding(tmp_path, monkeypatch):
    db = tmp_path / "aria.db"
    monkeypatch.setattr("aria_core.repertoire_db.DB_PATH", str(db))
    await repertoire_db.init_repertoire_db()

    item = await repertoire_db.create(
        name="Future SaaS",
        description="Test venture",
        entity_type=EntityType.VENTURE,
    )
    assert item.parent_id is not None
    structure = await repertoire_db.get_holding_structure()
    assert any(v.name == "Future SaaS" for v in structure.ventures)