"""Pot commun USDC — inscriptions par dépôt on-chain (Base)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import aiosqlite

from app.auth.access_code import DB_PATH, init_auth_db
DEPOSIT_MICRO = 100_000  # 0.1 USDC (6 decimals)


async def _ensure_tables() -> None:
    await init_auth_db()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS pot_rounds (
                id TEXT PRIMARY KEY,
                site_slug TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                total_micro INTEGER NOT NULL DEFAULT 0,
                entries_count INTEGER NOT NULL DEFAULT 0,
                ends_at TEXT NOT NULL,
                winner_wallet TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS pot_entries (
                id TEXT PRIMARY KEY,
                round_id TEXT NOT NULL,
                wallet TEXT NOT NULL,
                privy_did TEXT,
                tx_hash TEXT NOT NULL UNIQUE,
                amount_micro INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (round_id) REFERENCES pot_rounds(id)
            )
            """
        )
        await db.commit()


async def _get_or_create_round(site_slug: str) -> dict:
    await _ensure_tables()
    now = datetime.now(timezone.utc)
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            SELECT id, total_micro, entries_count, ends_at, status
            FROM pot_rounds
            WHERE site_slug = ? AND status = 'open'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (site_slug,),
        )
        row = await cursor.fetchone()
        if row:
            return {
                "id": row[0],
                "total_micro": row[1],
                "entries_count": row[2],
                "ends_at": row[3],
                "status": row[4],
            }

        round_id = str(uuid.uuid4())
        ends = (now + timedelta(days=7)).isoformat()
        created = now.isoformat()
        await db.execute(
            """
            INSERT INTO pot_rounds (id, site_slug, status, total_micro, entries_count, ends_at, created_at)
            VALUES (?, ?, 'open', 0, 0, ?, ?)
            """,
            (round_id, site_slug, ends, created),
        )
        await db.commit()
        return {
            "id": round_id,
            "total_micro": 0,
            "entries_count": 0,
            "ends_at": ends,
            "status": "open",
        }


async def get_pot_status(*, site_slug: str, wallet: str | None = None) -> dict:
    rnd = await _get_or_create_round(site_slug)
    user_entered = False
    if wallet:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                "SELECT 1 FROM pot_entries WHERE round_id = ? AND lower(wallet) = lower(?)",
                (rnd["id"], wallet),
            )
            user_entered = await cursor.fetchone() is not None

    total = rnd["total_micro"]
    return {
        "round_id": rnd["id"],
        "pot_usdc": f"{total / 1_000_000:.2f}",
        "entries": rnd["entries_count"],
        "ends_at": rnd["ends_at"],
        "user_entered": user_entered,
        "deposit_usdc": "0.1",
    }


async def register_deposit(
    *,
    site_slug: str,
    wallet: str,
    tx_hash: str,
    privy_did: str | None = None,
) -> dict:
    if not wallet.startswith("0x") or len(wallet) < 42:
        raise ValueError("Adresse wallet invalide.")
    if not tx_hash.startswith("0x"):
        raise ValueError("Hash de transaction invalide.")

    rnd = await _get_or_create_round(site_slug)
    now = datetime.now(timezone.utc).isoformat()
    entry_id = str(uuid.uuid4())

    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT 1 FROM pot_entries WHERE tx_hash = ?", (tx_hash,))
        if await cursor.fetchone():
            return await get_pot_status(site_slug=site_slug, wallet=wallet)

        await db.execute(
            """
            INSERT INTO pot_entries (id, round_id, wallet, privy_did, tx_hash, amount_micro, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (entry_id, rnd["id"], wallet.lower(), privy_did, tx_hash.lower(), DEPOSIT_MICRO, now),
        )
        await db.execute(
            """
            UPDATE pot_rounds
            SET total_micro = total_micro + ?, entries_count = entries_count + 1
            WHERE id = ?
            """,
            (DEPOSIT_MICRO, rnd["id"]),
        )
        await db.commit()

    return await get_pot_status(site_slug=site_slug, wallet=wallet)


