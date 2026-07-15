"""Persistance de la progression du scan `/walletscore` (#157 suite, 15/07).

Constat opérateur : un plafond fixe de tokens analysés (`WEIGHTS.max_tokens_analyzed`)
ne peut jamais couvrir un wallet très actif (ex. 680 tokens tradés) en un seul appel.
Ce module permet de couvrir l'historique complet en PLUSIEURS passages : chaque appel
`score_wallets` traite le prochain lot de tokens jamais encore vus (ou dont l'activité
a évolué depuis le dernier passage), et le score final se base sur TOUS les trades
clôturés jamais archivés pour ce wallet, pas seulement ceux du dernier lot.

Deux tables :
- `wallet_scan_checkpoint` : progression par wallet (tokens déjà vus, date du dernier
  scan, couverture complète atteinte ou non).
- `wallet_archived_trade` : trades clôturés (FIFO) archivés par token. Un token
  re-scanné voit ses trades REMPLACÉS (jamais ajoutés en double) -- le FIFO est
  recalculé en entier depuis l'historique complet du token à chaque scan.
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
                full_coverage_at TEXT
            )
            """
        )
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
        # Migration à chaud idempotente (15/07, revue Gemini -- price_confirmation_ratio)
        # -- même patron que vc_predictions.py/exam.py : une base déjà déployée avant ce
        # champ n'a pas ces colonnes, `CREATE TABLE IF NOT EXISTS` ne les ajoute pas
        # rétroactivement.
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

    @property
    def full_coverage(self) -> bool:
        return self.full_coverage_at is not None


async def get_checkpoint(wallet: str) -> ScanCheckpoint:
    await _ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (
            await db.execute(
                "SELECT scanned_tokens, last_scan_at, tokens_found_total, full_coverage_at "
                "FROM wallet_scan_checkpoint WHERE wallet=?",
                (wallet.lower(),),
            )
        ).fetchone()
    if row is None:
        return ScanCheckpoint()
    scanned_raw, last_scan_raw, tokens_found_total, full_coverage_raw = row
    return ScanCheckpoint(
        scanned_tokens=set(json.loads(scanned_raw or "[]")),
        last_scan_at=datetime.fromisoformat(last_scan_raw) if last_scan_raw else None,
        tokens_found_total=tokens_found_total or 0,
        full_coverage_at=datetime.fromisoformat(full_coverage_raw) if full_coverage_raw else None,
    )


async def save_checkpoint(
    wallet: str,
    *,
    scanned_tokens: set[str],
    last_scan_at: datetime,
    tokens_found_total: int,
    full_coverage_at: datetime | None,
) -> None:
    await _ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO wallet_scan_checkpoint
                (wallet, scanned_tokens, last_scan_at, tokens_found_total, full_coverage_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(wallet) DO UPDATE SET
                scanned_tokens=excluded.scanned_tokens,
                last_scan_at=excluded.last_scan_at,
                tokens_found_total=excluded.tokens_found_total,
                full_coverage_at=excluded.full_coverage_at
            """,
            (
                wallet.lower(),
                json.dumps(sorted(scanned_tokens)),
                last_scan_at.isoformat(),
                tokens_found_total,
                full_coverage_at.isoformat() if full_coverage_at else None,
            ),
        )
        await db.commit()


async def replace_archived_trades(wallet: str, token_addresses: set[str], trades: list) -> None:
    """Remplace les trades archivés pour CES adresses de token précisément.

    Jamais un simple append : le FIFO est recalculé en entier depuis l'historique
    complet du token à chaque scan (cf. `_analyze_wallet_multi_token`), donc
    ré-insérer sans purger d'abord dupliquerait les mêmes trades historiques à
    chaque passage. ``token_addresses`` attend des adresses PLATES (pas de préfixe
    chaîne) -- même tradeoff assumé qu'ailleurs dans ``smart_money.py`` (collision
    entre deux chaînes différentes jugée négligeable, ~2^160 espaces d'adresses).
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
    """Reconstruit les ``ClosedTrade`` archivés (import différé -- évite un cycle
    d'import avec ``smart_money.py``, qui importe ce module)."""
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
