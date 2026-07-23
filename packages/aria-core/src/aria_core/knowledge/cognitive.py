from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import aiosqlite
from pydantic import BaseModel

from aria_core.paths import aria_db_path

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())


class KnowledgeItem(BaseModel):
    id: str
    source: str  # x_twitter, zhc_api, manual, curiosity
    topic: str
    content: str
    confidence: float
    approved: bool
    created_at: datetime


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS cognitive_knowledge (
                id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                topic TEXT NOT NULL,
                content TEXT NOT NULL,
                confidence REAL DEFAULT 0.5,
                approved INTEGER DEFAULT 0,
                created_at TEXT NOT NULL
            )
            """
        )
        await db.commit()


async def add_knowledge(
    source: str,
    topic: str,
    content: str,
    confidence: float = 0.5,
    approved: bool = False,
) -> KnowledgeItem:
    await _ensure_table()
    now = datetime.now(timezone.utc)
    item = KnowledgeItem(
        id=str(uuid4())[:8],
        source=source,
        topic=topic,
        content=content,
        confidence=confidence,
        approved=approved,
        created_at=now,
    )
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO cognitive_knowledge
            (id, source, topic, content, confidence, approved, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.id,
                item.source,
                item.topic,
                item.content,
                item.confidence,
                int(item.approved),
                item.created_at.isoformat(),
            ),
        )
        await db.commit()
    return item


async def approve_knowledge(item_id: str) -> bool:
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "UPDATE cognitive_knowledge SET approved = 1 WHERE id = ?",
            (item_id,),
        )
        await db.commit()
        ok = cursor.rowcount > 0
    if ok:
        try:
            from aria_core.memory.vector.ingest import ingest_approved_item

            await ingest_approved_item(item_id)
        except Exception as exc:
            logger.debug("vector ingest skip: %s", exc)
    return ok


async def get_knowledge_by_id(item_id: str) -> KnowledgeItem | None:
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            SELECT id, source, topic, content, confidence, approved, created_at
            FROM cognitive_knowledge WHERE id = ?
            """,
            (item_id,),
        )
        row = await cursor.fetchone()
    if not row:
        return None
    return KnowledgeItem(
        id=row[0],
        source=row[1],
        topic=row[2],
        content=row[3],
        confidence=row[4],
        approved=bool(row[5]),
        created_at=datetime.fromisoformat(row[6]),
    )


async def get_approved_since(
    since: datetime,
    *,
    source: str | None = None,
    limit: int = 20,
) -> list[KnowledgeItem]:
    """Approved insights created after `since` (e.g. X replies after a published tweet)."""
    await _ensure_table()
    since_iso = since.isoformat()
    query = """
        SELECT id, source, topic, content, confidence, approved, created_at
        FROM cognitive_knowledge
        WHERE approved = 1 AND created_at >= ?
    """
    params: list[Any] = [since_iso]
    if source:
        query += " AND source = ?"
        params.append(source)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()
    return [
        KnowledgeItem(
            id=row[0],
            source=row[1],
            topic=row[2],
            content=row[3],
            confidence=row[4],
            approved=bool(row[5]),
            created_at=datetime.fromisoformat(row[6]),
        )
        for row in rows
    ]


async def count_approved_since(
    since: datetime,
    *,
    source: str | None = None,
) -> int:
    await _ensure_table()
    since_iso = since.isoformat()
    query = "SELECT COUNT(*) FROM cognitive_knowledge WHERE approved = 1 AND created_at >= ?"
    params: list[Any] = [since_iso]
    if source:
        query += " AND source = ?"
        params.append(source)
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(query, params)
        row = await cursor.fetchone()
    return int(row[0]) if row else 0


async def get_approved(limit: int = 50) -> list[KnowledgeItem]:
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            SELECT id, source, topic, content, confidence, approved, created_at
            FROM cognitive_knowledge WHERE approved = 1
            ORDER BY created_at DESC LIMIT ?
            """,
            (limit,),
        )
        rows = await cursor.fetchall()
    return [
        KnowledgeItem(
            id=row[0],
            source=row[1],
            topic=row[2],
            content=row[3],
            confidence=row[4],
            approved=bool(row[5]),
            created_at=datetime.fromisoformat(row[6]),
        )
        for row in rows
    ]


async def get_pending(limit: int = 20) -> list[KnowledgeItem]:
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            SELECT id, source, topic, content, confidence, approved, created_at
            FROM cognitive_knowledge WHERE approved = 0
            ORDER BY created_at DESC LIMIT ?
            """,
            (limit,),
        )
        rows = await cursor.fetchall()
    return [
        KnowledgeItem(
            id=row[0],
            source=row[1],
            topic=row[2],
            content=row[3],
            confidence=row[4],
            approved=bool(row[5]),
            created_at=datetime.fromisoformat(row[6]),
        )
        for row in rows
    ]


async def upsert_knowledge_by_topic(
    topic: str,
    content: str,
    *,
    source: str = "doctrine",
    confidence: float = 1.0,
    approved: bool = True,
) -> KnowledgeItem:
    """Replace approved knowledge for a topic — idempotent doctrine updates."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM cognitive_knowledge WHERE topic = ? AND approved = 1",
            (topic,),
        )
        await db.commit()
    return await add_knowledge(
        source=source,
        topic=topic,
        content=content,
        confidence=confidence,
        approved=approved,
    )


async def purge_placeholder_insights() -> int:
    """Remove legacy mock x_setup insights from cognitive memory."""
    from aria_core.gateway.x_twitter import is_placeholder_x_insight

    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            SELECT id, topic, content FROM cognitive_knowledge
            WHERE topic = 'x_setup' OR content LIKE '%configure X_BEARER_TOKEN%'
            """
        )
        rows = await cursor.fetchall()
        to_delete = [
            row[0] for row in rows if is_placeholder_x_insight(row[2], row[1])
        ]
        if not to_delete:
            return 0
        placeholders = ",".join("?" for _ in to_delete)
        cursor = await db.execute(
            f"DELETE FROM cognitive_knowledge WHERE id IN ({placeholders})",
            to_delete,
        )
        await db.commit()
        return cursor.rowcount


async def build_context_summary() -> str:
    items = await get_approved(limit=15)
    if not items:
        return "No approved cognitive knowledge yet."
    lines = [f"[{k.topic}] {k.content}" for k in items]
    return "\n".join(lines)