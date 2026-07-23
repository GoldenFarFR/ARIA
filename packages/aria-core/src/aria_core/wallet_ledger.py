"""Structured ledger of ACP transactions — amount, counterparty, decision, outcome.

One row per transaction (id = approval_id), created ``pending`` on escalation
then transitioned only once via ``claim_for_decision`` (atomic
pending -> approved/rejected transition, protects against a double Telegram
click that would trigger a double spend).
"""
from __future__ import annotations

from datetime import datetime, timezone

import aiosqlite

from aria_core.paths import aria_db_path

DB_PATH = str(aria_db_path())

_COLUMNS = [
    "id",
    "action",
    "amount",
    "counterparty",
    "requested_at",
    "decision",
    "decided_at",
    "decided_by",
    "result",
    "payload",
]


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS wallet_transactions (
                id TEXT PRIMARY KEY,
                action TEXT NOT NULL,
                amount TEXT NOT NULL,
                counterparty TEXT NOT NULL,
                requested_at TEXT NOT NULL,
                decision TEXT NOT NULL DEFAULT 'pending',
                decided_at TEXT,
                decided_by TEXT,
                result TEXT,
                payload TEXT DEFAULT '{}'
            )
            """
        )
        await db.commit()


async def create_ledger_entry(
    *,
    entry_id: str,
    action: str,
    amount: str,
    counterparty: str,
    payload: str = "{}",
) -> None:
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO wallet_transactions
            (id, action, amount, counterparty, requested_at, decision, payload)
            VALUES (?, ?, ?, ?, ?, 'pending', ?)
            """,
            (entry_id, action, amount, counterparty, datetime.now(timezone.utc).isoformat(), payload),
        )
        await db.commit()


async def claim_for_decision(entry_id: str, *, decision: str, decided_by: str) -> dict | None:
    """Atomic pending -> decision transition.

    Returns the row if the transition took place, otherwise ``None`` (already
    processed — protects against double execution on a double Telegram
    click).
    """
    await _ensure_table()
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            UPDATE wallet_transactions
            SET decision = ?, decided_at = ?, decided_by = ?
            WHERE id = ? AND decision = 'pending'
            """,
            (decision, now, decided_by, entry_id),
        )
        await db.commit()
        if cursor.rowcount == 0:
            return None
        row_cursor = await db.execute("SELECT * FROM wallet_transactions WHERE id = ?", (entry_id,))
        row = await row_cursor.fetchone()
    if not row:
        return None
    return dict(zip(_COLUMNS, row))


async def set_result(entry_id: str, result: str) -> None:
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE wallet_transactions SET result = ? WHERE id = ?",
            (result[:1000], entry_id),
        )
        await db.commit()
