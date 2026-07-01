from __future__ import annotations

import os
from datetime import datetime, timezone
from uuid import uuid4

import aiosqlite

from app.config import settings
from app.models.schemas import WatchlistItem

from app.paths import product_db_path

DB_PATH = str(product_db_path())


async def _migrate_watchlist_visitor(db: aiosqlite.Connection) -> None:
    cursor = await db.execute("PRAGMA table_info(watchlist)")
    columns = {row[1] for row in await cursor.fetchall()}
    if "visitor_id" in columns:
        return
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS watchlist_new (
            id TEXT PRIMARY KEY,
            visitor_id TEXT NOT NULL DEFAULT '',
            chain_id TEXT NOT NULL,
            pair_address TEXT NOT NULL,
            symbol TEXT NOT NULL,
            added_at TEXT NOT NULL,
            UNIQUE(visitor_id, chain_id, pair_address)
        )
        """
    )
    await db.execute(
        """
        INSERT OR IGNORE INTO watchlist_new (id, visitor_id, chain_id, pair_address, symbol, added_at)
        SELECT id, '', chain_id, pair_address, symbol, added_at FROM watchlist
        """
    )
    await db.execute("DROP TABLE watchlist")
    await db.execute("ALTER TABLE watchlist_new RENAME TO watchlist")


async def init_db() -> None:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS watchlist (
                id TEXT PRIMARY KEY,
                chain_id TEXT NOT NULL,
                pair_address TEXT NOT NULL,
                symbol TEXT NOT NULL,
                added_at TEXT NOT NULL,
                UNIQUE(chain_id, pair_address)
            )
            """
        )
        await _migrate_watchlist_visitor(db)
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS alert_history (
                id TEXT PRIMARY KEY,
                chain_id TEXT NOT NULL,
                pair_address TEXT NOT NULL,
                symbol TEXT NOT NULL,
                signal_type TEXT NOT NULL,
                score REAL NOT NULL,
                timeframe TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        await db.commit()


async def add_to_watchlist(
    chain_id: str,
    pair_address: str,
    symbol: str,
    visitor_id: str,
) -> WatchlistItem:
    now = datetime.now(timezone.utc)
    item_id = str(uuid4())
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT OR IGNORE INTO watchlist (id, visitor_id, chain_id, pair_address, symbol, added_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                item_id,
                visitor_id,
                chain_id,
                pair_address,
                symbol,
                now.isoformat(),
            ),
        )
        await db.commit()
        cursor = await db.execute(
            """
            SELECT id, chain_id, pair_address, symbol, added_at
            FROM watchlist
            WHERE visitor_id = ? AND chain_id = ? AND pair_address = ?
            """,
            (visitor_id, chain_id, pair_address),
        )
        row = await cursor.fetchone()
    if not row:
        return WatchlistItem(
            id=item_id,
            chain_id=chain_id,
            pair_address=pair_address,
            symbol=symbol,
            added_at=now,
        )
    return WatchlistItem(
        id=row[0],
        chain_id=row[1],
        pair_address=row[2],
        symbol=row[3],
        added_at=datetime.fromisoformat(row[4]),
    )


async def remove_from_watchlist(chain_id: str, pair_address: str, visitor_id: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "DELETE FROM watchlist WHERE visitor_id = ? AND chain_id = ? AND pair_address = ?",
            (visitor_id, chain_id, pair_address),
        )
        await db.commit()
        return cursor.rowcount > 0


async def get_watchlist(visitor_id: str | None = None) -> list[WatchlistItem]:
    async with aiosqlite.connect(DB_PATH) as db:
        if visitor_id:
            cursor = await db.execute(
                """
                SELECT id, chain_id, pair_address, symbol, added_at
                FROM watchlist
                WHERE visitor_id = ?
                ORDER BY added_at DESC
                """,
                (visitor_id,),
            )
        else:
            cursor = await db.execute(
                """
                SELECT id, chain_id, pair_address, symbol, added_at
                FROM watchlist
                ORDER BY added_at DESC
                """
            )
        rows = await cursor.fetchall()
    return [
        WatchlistItem(
            id=row[0],
            chain_id=row[1],
            pair_address=row[2],
            symbol=row[3],
            added_at=datetime.fromisoformat(row[4]),
        )
        for row in rows
    ]


async def has_recent_alert(
    chain_id: str,
    pair_address: str,
    signal_type: str,
    timeframe: str,
    *,
    within_hours: int = 4,
) -> bool:
    """Skip duplicate alerts for the same pair/signal/timeframe."""
    from datetime import timedelta

    cutoff = (datetime.now(timezone.utc) - timedelta(hours=within_hours)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            SELECT 1 FROM alert_history
            WHERE chain_id = ? AND pair_address = ? AND signal_type = ?
              AND timeframe = ? AND created_at >= ?
            LIMIT 1
            """,
            (chain_id, pair_address, signal_type, timeframe, cutoff),
        )
        row = await cursor.fetchone()
    return row is not None


async def save_alert(
    chain_id: str,
    pair_address: str,
    symbol: str,
    signal_type: str,
    score: float,
    timeframe: str,
    message: str,
) -> str:
    alert_id = str(uuid4())
    created_at = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO alert_history
            (id, chain_id, pair_address, symbol, signal_type, score, timeframe, message, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                alert_id,
                chain_id,
                pair_address,
                symbol,
                signal_type,
                score,
                timeframe,
                message,
                created_at,
            ),
        )
        await db.commit()
    return alert_id


async def get_recent_alerts(limit: int = 50) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            SELECT id, chain_id, pair_address, symbol, signal_type, score, timeframe, message, created_at
            FROM alert_history
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = await cursor.fetchall()
    return [
        {
            "id": row[0],
            "chain_id": row[1],
            "pair_address": row[2],
            "symbol": row[3],
            "signal_type": row[4],
            "score": row[5],
            "timeframe": row[6],
            "message": row[7],
            "created_at": row[8],
        }
        for row in rows
    ]