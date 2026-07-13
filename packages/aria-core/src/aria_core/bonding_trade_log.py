"""Journal des exécutions du wrapper `bondv5-trader` (#60, marché « Jetons d'agent »).

Séparé de `screened_token` par conception : ce module n'écrit **jamais** dans le
pool de screening (lecture-analyse) — il enregistre uniquement le résultat d'une
tentative d'exécution on-chain déjà décidée en amont par `bonding_screen.py` /
`bonding_absorber.py`. Aucune logique de scoring ici.
"""
from __future__ import annotations

from datetime import datetime, timezone

import aiosqlite

from aria_core.paths import aria_db_path

DB_PATH = str(aria_db_path())

_COLUMNS = [
    "id",
    "contract",
    "symbol",
    "side",
    "amount_usdc",
    "amount_token",
    "min_out_wei",
    "slippage_bps",
    "tx_hash",
    "status",
    "reason",
    "created_at",
]


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS bonding_trade_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                contract TEXT NOT NULL,
                symbol TEXT NOT NULL DEFAULT '',
                side TEXT NOT NULL,
                amount_usdc REAL NOT NULL DEFAULT 0,
                amount_token REAL NOT NULL DEFAULT 0,
                min_out_wei TEXT NOT NULL DEFAULT '',
                slippage_bps INTEGER NOT NULL DEFAULT 0,
                tx_hash TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            )
            """
        )
        await db.commit()


async def record_trade(
    *,
    contract: str,
    symbol: str = "",
    side: str,
    amount_usdc: float = 0.0,
    amount_token: float = 0.0,
    min_out_wei: str = "",
    slippage_bps: int = 0,
    tx_hash: str = "",
    status: str,
    reason: str = "",
) -> None:
    """Enregistre une tentative d'exécution (`status` in {"ok", "failed", "blocked"}).

    `status="blocked"` couvre les refus côté garde-fou (slippage calculé > tolérance,
    kill-switch désactivé, devis indisponible) — jamais silencieux, toujours tracé.
    """
    await _ensure_table()
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO bonding_trade_log
                (contract, symbol, side, amount_usdc, amount_token, min_out_wei,
                 slippage_bps, tx_hash, status, reason, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                contract, symbol, side, amount_usdc, amount_token, min_out_wei,
                slippage_bps, tx_hash, status, reason, now,
            ),
        )
        await db.commit()


async def list_trades(limit: int = 100) -> list[dict]:
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        rows = await (
            await db.execute(
                "SELECT * FROM bonding_trade_log ORDER BY id DESC LIMIT ?",
                (limit,),
            )
        ).fetchall()
    return [dict(zip(_COLUMNS, row)) for row in rows]
