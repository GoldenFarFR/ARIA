"""Pool de tokens « screenés » — le vivier dans lequel la boucle tire ses 20.

Un token qui **passe le filtre** (`skills/safety_screen.py`) entre ici. Chaque
lundi, la boucle d'entraînement tire **20 candidats au sort** dans le pool actif
(loterie) → échantillon **non biaisé** (pas de cherry-pick) ET **screené** (pas un
scam technique). Un token peut être re-vérifié et **retiré** (`dropped`) s'il se
dégrade (liquidité qui fuit, LP délocké) — un contrat propre aujourd'hui peut ne
plus l'être demain.

Stockage local SQLite `aria.db`, table `screened_token` (clé = contrat).
Aucune action financière : c'est un annuaire de candidats.
"""
from __future__ import annotations

import random
from datetime import datetime, timezone

import aiosqlite

from aria_core.paths import aria_db_path

DB_PATH = str(aria_db_path())

_COLUMNS = [
    "contract",
    "symbol",
    "liquidity_usd",
    "security_score",
    "top_holder_pct",
    "verdict",
    "pool_address",
    "network",
    "status",
    "first_screened_at",
    "last_checked_at",
]


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS screened_token (
                contract TEXT PRIMARY KEY,
                symbol TEXT,
                liquidity_usd REAL,
                security_score INTEGER,
                top_holder_pct REAL,
                verdict TEXT,
                pool_address TEXT,
                network TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                first_screened_at TEXT NOT NULL,
                last_checked_at TEXT NOT NULL
            )
            """
        )
        await db.commit()


async def upsert_screened(
    *,
    contract: str,
    symbol: str = "",
    liquidity_usd: float = 0.0,
    security_score: int = 0,
    top_holder_pct: float | None = None,
    verdict: str = "",
    pool_address: str = "",
    network: str = "base",
) -> None:
    """Ajoute/rafraîchit un token screené (status ``active``).

    Upsert : ``first_screened_at`` est préservé au ré-enregistrement (on garde la
    date de première entrée), ``last_checked_at`` est toujours mis à jour. Ré-activer
    (`active`) un token qui repasse le filtre est volontaire.
    """
    await _ensure_table()
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO screened_token
              (contract, symbol, liquidity_usd, security_score, top_holder_pct,
               verdict, pool_address, network, status, first_screened_at, last_checked_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
            ON CONFLICT(contract) DO UPDATE SET
              symbol=excluded.symbol,
              liquidity_usd=excluded.liquidity_usd,
              security_score=excluded.security_score,
              top_holder_pct=excluded.top_holder_pct,
              verdict=excluded.verdict,
              pool_address=excluded.pool_address,
              network=excluded.network,
              status='active',
              last_checked_at=excluded.last_checked_at
            """,
            (
                contract, symbol, liquidity_usd, security_score, top_holder_pct,
                verdict, pool_address, network, now, now,
            ),
        )
        await db.commit()


async def drop_token(contract: str, *, reason: str = "") -> None:
    """Retire un token du pool actif (dégradé). Reste en base (status ``dropped``)."""
    await _ensure_table()
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE screened_token SET status='dropped', last_checked_at=? WHERE contract=?",
            (now, contract),
        )
        await db.commit()


async def list_pool(status: str = "active", limit: int = 1000) -> list[dict]:
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        rows = await (
            await db.execute(
                "SELECT * FROM screened_token WHERE status=? ORDER BY last_checked_at DESC LIMIT ?",
                (status, limit),
            )
        ).fetchall()
    return [dict(zip(_COLUMNS, row)) for row in rows]


async def count_pool(status: str = "active") -> int:
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (
            await db.execute("SELECT COUNT(*) FROM screened_token WHERE status=?", (status,))
        ).fetchone()
    return int(row[0]) if row else 0


async def draw_lottery(n: int = 20, *, status: str = "active") -> list[dict]:
    """Tire ``n`` tokens AU SORT dans le pool actif (échantillon non biaisé).

    Si le pool contient moins de ``n`` tokens, retourne tout le pool (mélangé).
    Le tirage aléatoire est ce qui empêche le cherry-pick : ARIA ne choisit pas
    « ceux qui l'arrangent », le hasard décide dans un vivier déjà screené.
    """
    pool = await list_pool(status=status, limit=100_000)
    if n <= 0 or not pool:
        return []
    if len(pool) <= n:
        random.shuffle(pool)
        return pool
    return random.sample(pool, n)
