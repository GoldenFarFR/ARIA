"""Persistence of `/walletscore` scan progress (#157 follow-up, 07/15).

Operator observation: a fixed cap on analyzed tokens (`WEIGHTS.max_tokens_analyzed`)
can never cover a very active wallet (e.g. 680 tokens traded) in a single call.
This module allows covering the full history over SEVERAL passes: each
`score_wallets` call processes the next batch of never-yet-seen tokens (or ones
whose activity has changed since the last pass), and the final score is based on ALL
closed trades ever archived for this wallet, not just the ones from the last batch.

Two tables:
- `wallet_scan_checkpoint`: per-wallet progress (tokens already seen, date of the last
  scan, whether full coverage has been reached).
- `wallet_archived_trade`: closed (FIFO) trades archived per token. A
  re-scanned token has its trades REPLACED (never appended twice) -- the FIFO is
  recomputed in full from the token's complete history on every scan.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime

import aiosqlite

from aria_core.paths import aria_db_path

DB_PATH = str(aria_db_path())


async def _ensure_tables() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS wallet_scan_checkpoint (
                wallet TEXT PRIMARY KEY,
                scanned_tokens TEXT NOT NULL DEFAULT '[]',
                last_scan_at TEXT,
                tokens_found_total INTEGER NOT NULL DEFAULT 0,
                full_coverage_at TEXT,
                last_activity_at TEXT
            )
            """
        )
        # Idempotent hot migration (07/15, permanent tracking -- #157 follow-up 2) --
        # a database already deployed before this field doesn't have this column.
        checkpoint_cols = {
            row[1] for row in await (await db.execute("PRAGMA table_info(wallet_scan_checkpoint)")).fetchall()
        }
        if "last_activity_at" not in checkpoint_cols:
            await db.execute("ALTER TABLE wallet_scan_checkpoint ADD COLUMN last_activity_at TEXT")
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS wallet_archived_trade (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wallet TEXT NOT NULL,
                token_address TEXT NOT NULL,
                buy_ts TEXT NOT NULL,
                sell_ts TEXT NOT NULL,
                token_amount REAL NOT NULL,
                buy_price REAL NOT NULL,
                sell_price REAL NOT NULL,
                buy_price_exact INTEGER NOT NULL DEFAULT 0,
                sell_price_exact INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        # Idempotent hot migration (07/15, Gemini review -- price_confirmation_ratio)
        # -- same pattern as vc_predictions.py/exam.py: a database already deployed before
        # this field doesn't have these columns, `CREATE TABLE IF NOT EXISTS` doesn't add
        # them retroactively.
        cursor = await db.execute("PRAGMA table_info(wallet_archived_trade)")
        existing_cols = {row[1] for row in await cursor.fetchall()}
        if "buy_price_exact" not in existing_cols:
            await db.execute("ALTER TABLE wallet_archived_trade ADD COLUMN buy_price_exact INTEGER NOT NULL DEFAULT 0")
        if "sell_price_exact" not in existing_cols:
            await db.execute("ALTER TABLE wallet_archived_trade ADD COLUMN sell_price_exact INTEGER NOT NULL DEFAULT 0")
        await db.commit()


@dataclass
class ScanCheckpoint:
    scanned_tokens: set[str] = field(default_factory=set)
    last_scan_at: datetime | None = None
    tokens_found_total: int = 0
    full_coverage_at: datetime | None = None
    # Permanent tracking (07/15, #157 follow-up 2): last REAL on-chain activity
    # ever seen for this wallet (max of observed transfer timestamps) --
    # distinct from `last_scan_at` (which advances on EVERY pass, even with no
    # new activity). Used to measure real inactivity (e.g. 3 months)
    # to stop weekly post-100% monitoring.
    last_activity_at: datetime | None = None

    @property
    def full_coverage(self) -> bool:
        return self.full_coverage_at is not None


async def get_checkpoint(wallet: str) -> ScanCheckpoint:
    await _ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (
            await db.execute(
                "SELECT scanned_tokens, last_scan_at, tokens_found_total, full_coverage_at, last_activity_at "
                "FROM wallet_scan_checkpoint WHERE wallet=?",
                (wallet.lower(),),
            )
        ).fetchone()
    if row is None:
        return ScanCheckpoint()
    scanned_raw, last_scan_raw, tokens_found_total, full_coverage_raw, last_activity_raw = row
    return ScanCheckpoint(
        scanned_tokens=set(json.loads(scanned_raw or "[]")),
        last_scan_at=datetime.fromisoformat(last_scan_raw) if last_scan_raw else None,
        tokens_found_total=tokens_found_total or 0,
        full_coverage_at=datetime.fromisoformat(full_coverage_raw) if full_coverage_raw else None,
        last_activity_at=datetime.fromisoformat(last_activity_raw) if last_activity_raw else None,
    )


async def save_checkpoint(
    wallet: str,
    *,
    scanned_tokens: set[str],
    last_scan_at: datetime,
    tokens_found_total: int,
    full_coverage_at: datetime | None,
    last_activity_at: datetime | None = None,
) -> None:
    await _ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO wallet_scan_checkpoint
                (wallet, scanned_tokens, last_scan_at, tokens_found_total, full_coverage_at, last_activity_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(wallet) DO UPDATE SET
                scanned_tokens=excluded.scanned_tokens,
                last_scan_at=excluded.last_scan_at,
                tokens_found_total=excluded.tokens_found_total,
                full_coverage_at=excluded.full_coverage_at,
                last_activity_at=excluded.last_activity_at
            """,
            (
                wallet.lower(),
                json.dumps(sorted(scanned_tokens)),
                last_scan_at.isoformat(),
                tokens_found_total,
                full_coverage_at.isoformat() if full_coverage_at else None,
                last_activity_at.isoformat() if last_activity_at else None,
            ),
        )
        await db.commit()


async def replace_archived_trades(wallet: str, token_addresses: set[str], trades: list) -> None:
    """Replaces archived trades for THESE specific token addresses.

    Never a plain append: the FIFO is recomputed in full from the token's
    complete history on every scan (cf. `_analyze_wallet_multi_token`), so
    re-inserting without purging first would duplicate the same historical trades on
    every pass. ``token_addresses`` expects PLAIN addresses (no chain
    prefix) -- same accepted tradeoff as elsewhere in ``smart_money.py`` (collision
    between two different chains judged negligible, ~2^160 address space).
    """
    await _ensure_tables()
    wallet_l = wallet.lower()
    addrs_l = {a.lower() for a in token_addresses}
    async with aiosqlite.connect(DB_PATH) as db:
        if addrs_l:
            placeholders = ",".join("?" for _ in addrs_l)
            await db.execute(
                f"DELETE FROM wallet_archived_trade WHERE wallet=? AND lower(token_address) IN ({placeholders})",
                (wallet_l, *addrs_l),
            )
        if trades:
            await db.executemany(
                """
                INSERT INTO wallet_archived_trade
                    (wallet, token_address, buy_ts, sell_ts, token_amount, buy_price, sell_price,
                     buy_price_exact, sell_price_exact)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        wallet_l, t.token_address, t.buy_ts.isoformat(), t.sell_ts.isoformat(),
                        t.token_amount, t.buy_price, t.sell_price,
                        int(getattr(t, "buy_price_exact", False)), int(getattr(t, "sell_price_exact", False)),
                    )
                    for t in trades
                ],
            )
        await db.commit()


async def list_archived_trades(wallet: str) -> list:
    """Rebuilds the archived ``ClosedTrade`` records (deferred import -- avoids an
    import cycle with ``smart_money.py``, which imports this module)."""
    from aria_core.services.smart_money import ClosedTrade

    await _ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        rows = await (
            await db.execute(
                "SELECT token_address, buy_ts, sell_ts, token_amount, buy_price, sell_price, "
                "buy_price_exact, sell_price_exact FROM wallet_archived_trade WHERE wallet=?",
                (wallet.lower(),),
            )
        ).fetchall()
    return [
        ClosedTrade(
            token_address=r[0],
            buy_ts=datetime.fromisoformat(r[1]),
            sell_ts=datetime.fromisoformat(r[2]),
            token_amount=r[3],
            buy_price=r[4],
            sell_price=r[5],
            buy_price_exact=bool(r[6]),
            sell_price_exact=bool(r[7]),
        )
        for r in rows
    ]
