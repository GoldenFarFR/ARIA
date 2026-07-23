"""Execution log for the `bondv5-trader` wrapper (#60, "Agent Tokens" market).

Separated from `screened_token` by design: this module **never** writes to the
screening pool (read-analysis) — it only records the result of an on-chain
execution attempt already decided upstream by `bonding_screen.py` /
`bonding_absorber.py`. No scoring logic here.
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
    """Records an execution attempt (`status` in {"ok", "failed", "blocked"}).

    `status="blocked"` covers guard-rail refusals (computed slippage > tolerance,
    kill-switch disabled, quote unavailable) — never silent, always logged.
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
