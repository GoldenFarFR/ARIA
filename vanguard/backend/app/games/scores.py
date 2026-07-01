"""Holding site mini-game scores — keyed by Privy DID."""

from __future__ import annotations

from datetime import datetime, timezone

import aiosqlite

from app.auth.access_code import DB_PATH, init_auth_db


async def _ensure_table() -> None:
    await init_auth_db()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS game_scores (
                privy_did TEXT NOT NULL,
                site_slug TEXT NOT NULL,
                game_id TEXT NOT NULL,
                score INTEGER NOT NULL,
                better TEXT NOT NULL DEFAULT 'max',
                updated_at TEXT NOT NULL,
                PRIMARY KEY (privy_did, site_slug, game_id)
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS user_links (
                privy_did TEXT PRIMARY KEY,
                twitter_username TEXT NOT NULL,
                linked_at TEXT NOT NULL
            )
            """
        )
        await db.commit()


async def get_session_identity(token: str | None) -> tuple[str, str] | None:
    if not token:
        return None
    await init_auth_db()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            SELECT privy_did, twitter_username
            FROM sessions
            WHERE token = ? AND privy_did IS NOT NULL
            """,
            (token,),
        )
        row = await cursor.fetchone()
    if not row or not row[0]:
        return None
    return str(row[0]), str(row[1] or "member")


def _is_better(new_score: int, old_score: int, better: str) -> bool:
    if better == "min":
        return new_score < old_score
    return new_score > old_score


async def get_score(*, privy_did: str, site_slug: str, game_id: str) -> int | None:
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            SELECT score FROM game_scores
            WHERE privy_did = ? AND site_slug = ? AND game_id = ?
            """,
            (privy_did, site_slug, game_id),
        )
        row = await cursor.fetchone()
    return int(row[0]) if row else None


async def upsert_score(
    *,
    privy_did: str,
    site_slug: str,
    game_id: str,
    score: int,
    better: str = "max",
) -> int:
    await _ensure_table()
    now = datetime.now(timezone.utc).isoformat()
    existing = await get_score(privy_did=privy_did, site_slug=site_slug, game_id=game_id)

    if existing is not None and not _is_better(score, existing, better):
        return existing

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO game_scores (privy_did, site_slug, game_id, score, better, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(privy_did, site_slug, game_id) DO UPDATE SET
                score = excluded.score,
                better = excluded.better,
                updated_at = excluded.updated_at
            """,
            (privy_did, site_slug, game_id, score, better, now),
        )
        await db.commit()
    return score


async def leaderboard(
    *,
    site_slug: str,
    game_id: str,
    limit: int = 10,
) -> list[dict]:
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        mode_cursor = await db.execute(
            "SELECT better FROM game_scores WHERE site_slug = ? AND game_id = ? LIMIT 1",
            (site_slug, game_id),
        )
        mode_row = await mode_cursor.fetchone()
        order = "ASC" if mode_row and mode_row[0] == "min" else "DESC"

        cursor = await db.execute(
            f"""
            SELECT gs.score, gs.better, gs.updated_at, ul.twitter_username
            FROM game_scores gs
            LEFT JOIN user_links ul ON ul.privy_did = gs.privy_did
            WHERE gs.site_slug = ? AND gs.game_id = ?
            ORDER BY gs.score {order}
            LIMIT ?
            """,
            (site_slug, game_id, limit),
        )
        rows = await cursor.fetchall()

    return [
        {
            "score": row[0],
            "handle": row[3] or "member",
            "updated_at": row[2],
        }
        for row in rows
    ]