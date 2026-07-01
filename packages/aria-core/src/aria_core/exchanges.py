from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

import aiosqlite
from pydantic import BaseModel

from aria_core.paths import aria_db_path

DB_PATH = str(aria_db_path())


class ExchangeStatus(str, Enum):
    DRAFT = "draft"
    APPROVED = "approved"
    PUBLISHED = "published"
    AWAITING_REPLY = "awaiting_reply"
    REPLIED = "replied"
    CLOSED = "closed"
    REJECTED = "rejected"


class AgentExchange(BaseModel):
    id: str
    target_agent: str
    channel: str
    status: ExchangeStatus
    message_body: str
    message_json: str
    approval_id: str | None = None
    published_at: datetime | None = None
    reply_body: str | None = None
    reply_at: datetime | None = None
    notes: str = ""
    created_at: datetime
    updated_at: datetime


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_exchanges (
                id TEXT PRIMARY KEY,
                target_agent TEXT NOT NULL,
                channel TEXT NOT NULL,
                status TEXT NOT NULL,
                message_body TEXT NOT NULL,
                message_json TEXT DEFAULT '{}',
                approval_id TEXT,
                published_at TEXT,
                reply_body TEXT,
                reply_at TEXT,
                notes TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        await db.commit()


def _row_to_exchange(row: tuple) -> AgentExchange:
    return AgentExchange(
        id=row[0],
        target_agent=row[1],
        channel=row[2],
        status=ExchangeStatus(row[3]),
        message_body=row[4],
        message_json=row[5] or "{}",
        approval_id=row[6],
        published_at=datetime.fromisoformat(row[7]) if row[7] else None,
        reply_body=row[8],
        reply_at=datetime.fromisoformat(row[9]) if row[9] else None,
        notes=row[10] or "",
        created_at=datetime.fromisoformat(row[11]),
        updated_at=datetime.fromisoformat(row[12]),
    )


async def create_exchange(
    target_agent: str,
    channel: str,
    message_body: str,
    message_json: dict,
    approval_id: str | None = None,
    status: ExchangeStatus = ExchangeStatus.DRAFT,
) -> AgentExchange:
    await _ensure_table()
    now = datetime.now(timezone.utc)
    exchange = AgentExchange(
        id=str(uuid4())[:8],
        target_agent=target_agent,
        channel=channel,
        status=status,
        message_body=message_body,
        message_json=json.dumps(message_json, default=str),
        approval_id=approval_id,
        created_at=now,
        updated_at=now,
    )
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO agent_exchanges
            (id, target_agent, channel, status, message_body, message_json,
             approval_id, published_at, reply_body, reply_at, notes, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, '', ?, ?)
            """,
            (
                exchange.id,
                exchange.target_agent,
                exchange.channel,
                exchange.status.value,
                exchange.message_body,
                exchange.message_json,
                exchange.approval_id,
                exchange.created_at.isoformat(),
                exchange.updated_at.isoformat(),
            ),
        )
        await db.commit()
    return exchange


async def update_status(
    exchange_id: str,
    status: ExchangeStatus,
    notes: str = "",
) -> AgentExchange | None:
    await _ensure_table()
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE agent_exchanges SET status = ?, updated_at = ?,
            notes = CASE WHEN ? != '' THEN ? ELSE notes END
            WHERE id = ?
            """,
            (status.value, now, notes, notes, exchange_id),
        )
        if status == ExchangeStatus.PUBLISHED:
            await db.execute(
                "UPDATE agent_exchanges SET published_at = ? WHERE id = ?",
                (now, exchange_id),
            )
        await db.commit()
        cursor = await db.execute("SELECT * FROM agent_exchanges WHERE id = ?", (exchange_id,))
        row = await cursor.fetchone()
    return _row_to_exchange(row) if row else None


async def record_reply(exchange_id: str, reply_body: str) -> AgentExchange | None:
    await _ensure_table()
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE agent_exchanges SET status = ?, reply_body = ?, reply_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (ExchangeStatus.REPLIED.value, reply_body, now, now, exchange_id),
        )
        await db.commit()
        cursor = await db.execute("SELECT * FROM agent_exchanges WHERE id = ?", (exchange_id,))
        row = await cursor.fetchone()
    return _row_to_exchange(row) if row else None


async def get_by_id(exchange_id: str) -> AgentExchange | None:
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT * FROM agent_exchanges WHERE id = ?", (exchange_id,))
        row = await cursor.fetchone()
    return _row_to_exchange(row) if row else None


async def get_all(limit: int = 20) -> list[AgentExchange]:
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT * FROM agent_exchanges ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
    return [_row_to_exchange(row) for row in rows]


async def get_latest_juno() -> AgentExchange | None:
    exchanges = await get_all(limit=5)
    for ex in exchanges:
        if "juno" in ex.target_agent.lower():
            return ex
    return None