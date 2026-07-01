from __future__ import annotations

import os
from datetime import datetime, timezone
from uuid import uuid4

import aiosqlite

from aria_core.holding import (
    DEFAULT_SUBSIDIARIES,
    DEXPULSE_SLUG,
    HOLDING_SLUG,
    HOLDING_TEMPLATE,
    holding_name,
)

PROTECTED_SLUGS = frozenset({HOLDING_SLUG, DEXPULSE_SLUG})
from aria_core.models import (
    EntityType,
    HoldingStructure,
    RepertoireCreateRequest,
    RepertoireItem,
    RepertoireItemStatus,
)

from aria_core.paths import aria_db_path

DB_PATH = str(aria_db_path())


async def init_repertoire_db() -> None:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS repertoire (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT,
                status TEXT NOT NULL,
                category TEXT,
                revenue_monthly REAL DEFAULT 0,
                priority INTEGER DEFAULT 3,
                tags TEXT DEFAULT '',
                zhc_aligned INTEGER DEFAULT 0,
                notes TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_messages (
                id TEXT PRIMARY KEY,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                skill_used TEXT,
                metadata TEXT DEFAULT '{}',
                created_at TEXT NOT NULL
            )
            """
        )
        await db.commit()
        await _migrate_repertoire_columns(db)
        await _migrate_agent_messages_columns(db)
        await _seed_holding_group(db)


async def _migrate_agent_messages_columns(db: aiosqlite.Connection) -> None:
    cursor = await db.execute("PRAGMA table_info(agent_messages)")
    cols = {row[1] for row in await cursor.fetchall()}
    if "visitor_id" not in cols:
        await db.execute(
            "ALTER TABLE agent_messages ADD COLUMN visitor_id TEXT NOT NULL DEFAULT ''"
        )
        await db.commit()


async def _migrate_repertoire_columns(db: aiosqlite.Connection) -> None:
    cursor = await db.execute("PRAGMA table_info(repertoire)")
    cols = {row[1] for row in await cursor.fetchall()}
    for name, col_type in (
        ("entity_type", "TEXT NOT NULL DEFAULT 'subsidiary'"),
        ("parent_id", "TEXT"),
        ("slug", "TEXT"),
    ):
        if name not in cols:
            await db.execute(f"ALTER TABLE repertoire ADD COLUMN {name} {col_type}")
    await db.commit()


async def _seed_holding_group(db: aiosqlite.Connection) -> None:
    cursor = await db.execute(
        "SELECT id FROM repertoire WHERE slug = ? LIMIT 1",
        (HOLDING_SLUG,),
    )
    holding_row = await cursor.fetchone()
    now = datetime.now(timezone.utc).isoformat()

    if not holding_row:
        holding_id = str(uuid4())
        tmpl = HOLDING_TEMPLATE
        await db.execute(
            """
            INSERT INTO repertoire
            (id, name, description, status, category, revenue_monthly, priority, tags,
             zhc_aligned, notes, created_at, updated_at, entity_type, parent_id, slug)
            VALUES (?, ?, ?, ?, ?, 0, ?, ?, 1, '', ?, ?, 'holding', NULL, ?)
            """,
            (
                holding_id,
                holding_name(),
                tmpl.description,
                tmpl.status,
                tmpl.category,
                tmpl.priority,
                ",".join(tmpl.tags),
                now,
                now,
                tmpl.slug,
            ),
        )
    else:
        holding_id = holding_row[0]
        await db.execute(
            "UPDATE repertoire SET name = ?, entity_type = 'holding' WHERE id = ?",
            (holding_name(), holding_id),
        )

    for sub in DEFAULT_SUBSIDIARIES:
        cursor = await db.execute(
            "SELECT id FROM repertoire WHERE slug = ? LIMIT 1",
            (sub.slug,),
        )
        if await cursor.fetchone():
            continue
        await db.execute(
            """
            INSERT INTO repertoire
            (id, name, description, status, category, revenue_monthly, priority, tags,
             zhc_aligned, notes, created_at, updated_at, entity_type, parent_id, slug)
            VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, '', ?, ?, 'subsidiary', ?, ?)
            """,
            (
                str(uuid4()),
                sub.name,
                sub.description,
                sub.status,
                sub.category,
                sub.priority,
                ",".join(sub.tags),
                int(sub.zhc_aligned),
                now,
                now,
                holding_id,
                sub.slug,
            ),
        )
    await _link_orphans_to_holding(db, holding_id)
    await db.commit()


async def _link_orphans_to_holding(db: aiosqlite.Connection, holding_id: str) -> None:
    """Ensure every non-holding entity is parented by the holding."""
    await db.execute(
        """
        UPDATE repertoire
        SET parent_id = ?, entity_type = COALESCE(NULLIF(entity_type, ''), 'subsidiary')
        WHERE entity_type != 'holding'
          AND (parent_id IS NULL OR parent_id = '' OR parent_id != ?)
        """,
        (holding_id, holding_id),
    )


def _row_to_item(row: tuple) -> RepertoireItem:
    tags = [t for t in row[7].split(",") if t] if row[7] else []
    entity_raw = row[12] if len(row) > 12 and row[12] else EntityType.SUBSIDIARY.value
    parent_id = row[13] if len(row) > 13 else None
    slug = row[14] if len(row) > 14 else None
    try:
        entity_type = EntityType(entity_raw)
    except ValueError:
        entity_type = EntityType.SUBSIDIARY
    return RepertoireItem(
        id=row[0],
        name=row[1],
        description=row[2] or "",
        status=RepertoireItemStatus(row[3]),
        category=row[4] or "projet",
        revenue_monthly=row[5] or 0.0,
        priority=row[6] or 3,
        tags=tags,
        zhc_aligned=bool(row[8]),
        notes=row[9] or "",
        created_at=datetime.fromisoformat(row[10]),
        updated_at=datetime.fromisoformat(row[11]),
        entity_type=entity_type,
        parent_id=parent_id,
        slug=slug,
    )


async def get_all() -> list[RepertoireItem]:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT * FROM repertoire ORDER BY priority DESC, updated_at DESC"
        )
        rows = await cursor.fetchall()
    return [_row_to_item(row) for row in rows]


async def get_by_id(item_id: str) -> RepertoireItem | None:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT * FROM repertoire WHERE id = ?", (item_id,))
        row = await cursor.fetchone()
    return _row_to_item(row) if row else None


def deletion_blocked_reason(item: RepertoireItem) -> str | None:
    if item.entity_type == EntityType.HOLDING:
        return "La holding mère ne peut pas être supprimée."
    if item.slug and item.slug in PROTECTED_SLUGS:
        return f"{item.name} est une entité protégée (flagship/holding)."
    return None


async def find_by_name(query: str) -> list[RepertoireItem]:
    needle = query.strip().lower()
    if not needle:
        return []
    items = await get_all()
    exact = [i for i in items if i.name.lower() == needle]
    if exact:
        return exact
    return [i for i in items if needle in i.name.lower()]


async def delete_item(item_id: str) -> tuple[bool, str, RepertoireItem | None]:
    item = await get_by_id(item_id)
    if not item:
        return False, "Entrée introuvable.", None
    blocked = deletion_blocked_reason(item)
    if blocked:
        return False, blocked, item
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM repertoire WHERE id = ?", (item_id,))
        await db.commit()
    return True, f"Supprimé : {item.name}", item


async def delete_by_name(name: str) -> tuple[bool, str, RepertoireItem | None]:
    matches = await find_by_name(name)
    if not matches:
        return False, f"Aucune entrée pour « {name} ».", None
    if len(matches) > 1:
        names = ", ".join(m.name for m in matches)
        return False, f"Plusieurs entrées ({names}) — précise le nom exact.", None
    return await delete_item(matches[0].id)


async def archive_item(item_id: str) -> tuple[bool, str, RepertoireItem | None]:
    item = await get_by_id(item_id)
    if not item:
        return False, "Entrée introuvable.", None
    blocked = deletion_blocked_reason(item)
    if blocked:
        return False, blocked, item
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE repertoire SET status = ?, updated_at = ? WHERE id = ?",
            (RepertoireItemStatus.ARCHIVED.value, now, item_id),
        )
        await db.commit()
    item.status = RepertoireItemStatus.ARCHIVED
    return True, f"Archivé : {item.name}", item


async def get_holding_id() -> str | None:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT id FROM repertoire WHERE slug = ? OR entity_type = 'holding' LIMIT 1",
            (HOLDING_SLUG,),
        )
        row = await cursor.fetchone()
    return row[0] if row else None


async def get_holding_structure() -> HoldingStructure | None:
    items = await get_all()
    holding = next((i for i in items if i.entity_type == EntityType.HOLDING), None)
    if not holding:
        return None
    subsidiaries = [
        i for i in items
        if i.entity_type == EntityType.SUBSIDIARY and i.parent_id == holding.id
    ]
    ventures = [
        i for i in items
        if i.entity_type == EntityType.VENTURE and i.parent_id == holding.id
    ]
    return HoldingStructure(holding=holding, subsidiaries=subsidiaries, ventures=ventures)


async def create(
    name: str,
    description: str = "",
    category: str = "projet",
    status: RepertoireItemStatus = RepertoireItemStatus.IDEA,
    priority: int = 3,
    tags: list[str] | None = None,
    zhc_aligned: bool = False,
    notes: str = "",
    *,
    entity_type: EntityType = EntityType.SUBSIDIARY,
    parent_id: str | None = None,
    slug: str | None = None,
) -> RepertoireItem:
    if entity_type != EntityType.HOLDING and not parent_id:
        parent_id = await get_holding_id()

    now = datetime.now(timezone.utc)
    item = RepertoireItem(
        id=str(uuid4()),
        name=name,
        description=description,
        status=status,
        category=category,
        priority=priority,
        tags=tags or [],
        zhc_aligned=zhc_aligned,
        created_at=now,
        updated_at=now,
        notes=notes,
        entity_type=entity_type,
        parent_id=parent_id,
        slug=slug,
    )
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO repertoire
            (id, name, description, status, category, revenue_monthly, priority, tags,
             zhc_aligned, notes, created_at, updated_at, entity_type, parent_id, slug)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.id,
                item.name,
                item.description,
                item.status.value,
                item.category,
                item.revenue_monthly,
                item.priority,
                ",".join(item.tags),
                int(item.zhc_aligned),
                item.notes,
                item.created_at.isoformat(),
                item.updated_at.isoformat(),
                item.entity_type.value,
                item.parent_id,
                item.slug,
            ),
        )
        await db.commit()
    return item


async def create_from_request(req: RepertoireCreateRequest) -> RepertoireItem:
    if req.entity_type == EntityType.HOLDING:
        raise ValueError("Cannot create a second holding entity")
    holding_id = await get_holding_id()
    if not holding_id:
        raise ValueError("Holding not initialized — cannot register venture")
    return await create(
        name=req.name,
        description=req.description,
        category=req.category,
        status=req.status,
        priority=req.priority,
        tags=req.tags,
        zhc_aligned=req.zhc_aligned,
        notes=req.notes,
        entity_type=req.entity_type,
        parent_id=holding_id,
        slug=req.slug,
    )


async def save_message(
    role: str,
    content: str,
    skill_used: str | None = None,
    metadata: str = "{}",
    visitor_id: str = "",
) -> str:
    msg_id = str(uuid4())
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO agent_messages
            (id, role, content, skill_used, metadata, created_at, visitor_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (msg_id, role, content, skill_used, metadata, now, visitor_id or ""),
        )
        await db.commit()
    return msg_id


async def get_messages(limit: int = 50, visitor_id: str | None = None) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        if visitor_id:
            cursor = await db.execute(
                """
                SELECT id, role, content, skill_used, metadata, created_at, visitor_id
                FROM agent_messages
                WHERE visitor_id = ?
                ORDER BY created_at DESC LIMIT ?
                """,
                (visitor_id, limit),
            )
        else:
            cursor = await db.execute(
                """
                SELECT id, role, content, skill_used, metadata, created_at, visitor_id
                FROM agent_messages ORDER BY created_at DESC LIMIT ?
                """,
                (limit,),
            )
        rows = await cursor.fetchall()
    return [
        {
            "id": row[0],
            "role": row[1],
            "content": row[2],
            "skill_used": row[3],
            "metadata": row[4],
            "created_at": row[5],
            "visitor_id": row[6] if len(row) > 6 else "",
        }
        for row in reversed(rows)
    ]