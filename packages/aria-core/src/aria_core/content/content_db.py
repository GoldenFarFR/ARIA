"""Content drafts — marketing, comms, FAQ entries produced by ARIA."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from uuid import uuid4

import aiosqlite

from aria_core.paths import aria_db_path

DB_PATH = str(aria_db_path())


async def init_content_db() -> None:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS content_drafts (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                channel TEXT DEFAULT 'site',
                status TEXT NOT NULL DEFAULT 'draft',
                tags TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        await db.commit()


async def save_draft(
    kind: str,
    title: str,
    body: str,
    *,
    channel: str = "site",
    tags: str = "",
    status: str = "draft",
) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    draft_id = str(uuid4())
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO content_drafts
            (id, kind, title, body, channel, status, tags, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (draft_id, kind, title, body, channel, status, tags, now, now),
        )
        await db.commit()
    return {
        "id": draft_id,
        "kind": kind,
        "title": title,
        "body": body,
        "channel": channel,
        "status": status,
        "tags": tags,
        "created_at": now,
    }


async def list_drafts(limit: int = 20, kind: str | None = None) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        if kind:
            cursor = await db.execute(
                """
                SELECT id, kind, title, body, channel, status, tags, created_at
                FROM content_drafts WHERE kind = ?
                ORDER BY created_at DESC LIMIT ?
                """,
                (kind, limit),
            )
        else:
            cursor = await db.execute(
                """
                SELECT id, kind, title, body, channel, status, tags, created_at
                FROM content_drafts ORDER BY created_at DESC LIMIT ?
                """,
                (limit,),
            )
        rows = await cursor.fetchall()
    return [
        {
            "id": r[0],
            "kind": r[1],
            "title": r[2],
            "body": r[3],
            "channel": r[4],
            "status": r[5],
            "tags": r[6],
            "created_at": r[7],
        }
        for r in rows
    ]