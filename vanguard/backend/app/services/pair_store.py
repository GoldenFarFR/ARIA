from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import aiosqlite

from app.models.schemas import PairIndexStats, PairSummary
from app.paths import product_db_path

DB_PATH = str(product_db_path())


async def init_pair_store() -> None:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS pair_snapshots (
                chain_id TEXT NOT NULL,
                pair_address TEXT NOT NULL,
                symbol TEXT NOT NULL,
                feed_type TEXT NOT NULL,
                data_json TEXT NOT NULL,
                price_usd REAL,
                price_change_h24 REAL,
                volume_h24 REAL,
                liquidity_usd REAL,
                pair_created_at INTEGER,
                indexed_at TEXT NOT NULL,
                PRIMARY KEY (chain_id, pair_address)
            )
            """
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_pair_feed ON pair_snapshots(feed_type)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_pair_change ON pair_snapshots(price_change_h24)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_pair_created ON pair_snapshots(pair_created_at)"
        )
        await db.commit()


async def upsert_pairs(pairs: list[PairSummary], feed_type: str) -> int:
    if not pairs:
        return 0
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        for pair in pairs:
            await db.execute(
                """
                INSERT INTO pair_snapshots (
                    chain_id, pair_address, symbol, feed_type, data_json,
                    price_usd, price_change_h24, volume_h24, liquidity_usd,
                    pair_created_at, indexed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chain_id, pair_address) DO UPDATE SET
                    symbol = excluded.symbol,
                    feed_type = excluded.feed_type,
                    data_json = excluded.data_json,
                    price_usd = excluded.price_usd,
                    price_change_h24 = excluded.price_change_h24,
                    volume_h24 = excluded.volume_h24,
                    liquidity_usd = excluded.liquidity_usd,
                    pair_created_at = excluded.pair_created_at,
                    indexed_at = excluded.indexed_at
                """,
                (
                    pair.chain_id,
                    pair.pair_address,
                    pair.base_token.symbol,
                    feed_type,
                    pair.model_dump_json(),
                    pair.price_usd,
                    pair.price_change_h24,
                    pair.volume_h24,
                    pair.liquidity_usd,
                    pair.pair_created_at,
                    now,
                ),
            )
        await db.commit()
    return len(pairs)


def _row_to_pair(data_json: str) -> PairSummary:
    return PairSummary.model_validate(json.loads(data_json))


async def get_pairs_by_feed(
    feed_type: str,
    *,
    chain_id: str | None = None,
    limit: int = 50,
    order_by: str = "liquidity_usd",
    descending: bool = True,
) -> list[PairSummary]:
    allowed_orders = {
        "liquidity_usd",
        "price_change_h24",
        "volume_h24",
        "pair_created_at",
        "indexed_at",
    }
    col = order_by if order_by in allowed_orders else "liquidity_usd"
    direction = "DESC" if descending else "ASC"
    query = f"""
        SELECT data_json FROM pair_snapshots
        WHERE feed_type = ?
    """
    params: list[object] = [feed_type]
    if chain_id:
        query += " AND chain_id = ?"
        params.append(chain_id)
    query += f" ORDER BY {col} IS NULL, {col} {direction} LIMIT ?"
    params.append(limit)

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()
    return [_row_to_pair(row[0]) for row in rows]


async def get_ranked_pairs(
    *,
    chain_id: str | None = None,
    limit: int = 50,
    gainers: bool = True,
) -> list[PairSummary]:
    direction = "DESC" if gainers else "ASC"
    query = """
        SELECT data_json FROM pair_snapshots
        WHERE price_change_h24 IS NOT NULL
    """
    params: list[object] = []
    if chain_id:
        query += " AND chain_id = ?"
        params.append(chain_id)
    query += f" ORDER BY price_change_h24 {direction} LIMIT ?"
    params.append(limit)

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()
    return [_row_to_pair(row[0]) for row in rows]


async def get_new_pairs(
    *,
    chain_id: str | None = None,
    limit: int = 50,
) -> list[PairSummary]:
    query = """
        SELECT data_json FROM pair_snapshots
        WHERE pair_created_at IS NOT NULL
    """
    params: list[object] = []
    if chain_id:
        query += " AND chain_id = ?"
        params.append(chain_id)
    query += " ORDER BY pair_created_at DESC LIMIT ?"
    params.append(limit)

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()
    return [_row_to_pair(row[0]) for row in rows]


async def get_index_stats() -> PairIndexStats:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM pair_snapshots")
        total = (await cursor.fetchone())[0]
        cursor = await db.execute(
            "SELECT feed_type, COUNT(*) FROM pair_snapshots GROUP BY feed_type"
        )
        by_feed = {row[0]: row[1] for row in await cursor.fetchall()}
        cursor = await db.execute("SELECT MAX(indexed_at) FROM pair_snapshots")
        last = (await cursor.fetchone())[0]
        cursor = await db.execute(
            "SELECT DISTINCT chain_id FROM pair_snapshots ORDER BY chain_id"
        )
        chains = [row[0] for row in await cursor.fetchall()]
    return PairIndexStats(
        total_pairs=total,
        by_feed=by_feed,
        last_indexed_at=last,
        chains=chains,
    )