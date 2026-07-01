from __future__ import annotations

import os
from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

import aiosqlite
from pydantic import BaseModel

from aria_core.paths import aria_db_path

DB_PATH = str(aria_db_path())


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class ApprovalRequest(BaseModel):
    id: str
    action: str
    description: str
    payload: str
    status: ApprovalStatus
    requested_by: str
    created_at: datetime
    resolved_at: datetime | None = None
    resolved_by: str | None = None


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS approvals (
                id TEXT PRIMARY KEY,
                action TEXT NOT NULL,
                description TEXT NOT NULL,
                payload TEXT DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'pending',
                requested_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                resolved_at TEXT,
                resolved_by TEXT
            )
            """
        )
        await db.commit()


async def create_approval(
    action: str,
    description: str,
    payload: str = "{}",
    requested_by: str = "aria",
) -> ApprovalRequest:
    await _ensure_table()
    req = ApprovalRequest(
        id=str(uuid4())[:8],
        action=action,
        description=description,
        payload=payload,
        status=ApprovalStatus.PENDING,
        requested_by=requested_by,
        created_at=datetime.now(timezone.utc),
    )
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO approvals
            (id, action, description, payload, status, requested_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                req.id,
                req.action,
                req.description,
                req.payload,
                req.status.value,
                req.requested_by,
                req.created_at.isoformat(),
            ),
        )
        await db.commit()
    return req


async def resolve_approval(
    approval_id: str,
    approved: bool,
    admin_id: str,
) -> ApprovalRequest | None:
    await _ensure_table()
    status = ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            UPDATE approvals SET status = ?, resolved_at = ?, resolved_by = ?
            WHERE id = ? AND status = 'pending'
            """,
            (status.value, now, admin_id, approval_id),
        )
        await db.commit()
        cursor = await db.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,))
        row = await cursor.fetchone()
    if not row:
        return None
    return ApprovalRequest(
        id=row[0],
        action=row[1],
        description=row[2],
        payload=row[3],
        status=ApprovalStatus(row[4]),
        requested_by=row[5],
        created_at=datetime.fromisoformat(row[6]),
        resolved_at=datetime.fromisoformat(row[7]) if row[7] else None,
        resolved_by=row[8],
    )


async def get_pending() -> list[ApprovalRequest]:
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT * FROM approvals WHERE status = 'pending' ORDER BY created_at DESC"
        )
        rows = await cursor.fetchall()
    return [
        ApprovalRequest(
            id=row[0],
            action=row[1],
            description=row[2],
            payload=row[3],
            status=ApprovalStatus(row[4]),
            requested_by=row[5],
            created_at=datetime.fromisoformat(row[6]),
            resolved_at=datetime.fromisoformat(row[7]) if row[7] else None,
            resolved_by=row[8],
        )
        for row in rows
    ]