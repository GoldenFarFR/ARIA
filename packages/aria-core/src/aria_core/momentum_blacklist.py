"""Liste noire de contrats pour le pipeline momentum (#194) -- demande opérateur
explicite (17/07), suite directe de la perte réelle sur BRIAN (-17,9 %, -8 962 $,
stop suiveur) : le contrat fait partie d'un essaim de décoys narratifs identifié
par VPS Research le même soir (préfixe vanity ``0xB200000000000000000000...``,
``token_name`` générique masqué derrière un ticker narratif -- "Coinbase Man"
pour BRIAN, même patron que "Base Man"/COBIE et "Coinbase Woman"/EMILIE --
wash-trading ~91x liquidité sur le pool principal). Le honeypot GoPlus seul ne
détecte pas ce pattern (le contrat n'est pas un honeypot technique, juste un
piège de visibilité) -- cette liste comble le trou pour les cas déjà confirmés,
en complément du plafond ratio volume/liquidité (défense générique, voir
``_check_wash_trading_ratio`` dans ``momentum_entry.py``) qui vise le PATTERN.

Persisté (survit aux redéploiements, contrairement à une constante Python) --
même doctrine que les autres journaux ARIA (append-only en pratique, jamais de
suppression : un contrat banni le reste, l'ajout d'une nouvelle entrée est le
seul point d'écriture)."""
from __future__ import annotations

import logging

import aiosqlite

from aria_core.paths import aria_db_path

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())

# Amorçage idempotent (INSERT OR IGNORE) -- un contrat déjà présent n'est jamais
# réécrit, une nouvelle session ne perd jamais un ban déjà décidé ailleurs.
_SEED_ENTRIES = [
    (
        "0xb2000000000000000000007bf6d5cbb0e24cb301", "base",
        "Décoy narratif BRIAN/\"Coinbase Man\" -- essaim vanity-prefix 0xB200... "
        "(VPS Research, 17/07), 44 holders, wash-trading ~91x liquidité. "
        "Perte réelle ARIA : -17,9 % / -8 962 $ (stop suiveur, 17/07).",
    ),
]


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS momentum_blacklist (
                contract TEXT NOT NULL,
                chain TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                added_at TEXT NOT NULL,
                PRIMARY KEY (contract, chain)
            )
            """
        )
        for contract, chain, reason in _SEED_ENTRIES:
            await db.execute(
                "INSERT OR IGNORE INTO momentum_blacklist (contract, chain, reason, added_at) "
                "VALUES (?, ?, ?, datetime('now'))",
                (contract, chain, reason),
            )
        await db.commit()


async def is_blacklisted(contract: str, chain: str) -> bool:
    """Vérifié EN PREMIER dans ``evaluate_momentum_entry`` -- aucun appel réseau,
    le check le plus rapide et le plus définitif du pipeline."""
    await _ensure_table()
    contract = (contract or "").strip().lower()
    chain = (chain or "").strip().lower()
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (
            await db.execute(
                "SELECT 1 FROM momentum_blacklist WHERE contract = ? AND chain = ?",
                (contract, chain),
            )
        ).fetchone()
    return row is not None


async def add_to_blacklist(contract: str, chain: str, reason: str) -> None:
    """Bannit un contrat -- jamais de suppression symétrique par design (un
    contrat banni le reste ; une levée de ban, si jamais nécessaire, serait une
    décision opérateur explicite à tracer séparément, pas une fonction ici)."""
    await _ensure_table()
    contract = (contract or "").strip().lower()
    chain = (chain or "").strip().lower()
    if not contract or not chain:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO momentum_blacklist (contract, chain, reason, added_at) "
            "VALUES (?, ?, ?, datetime('now'))",
            (contract, chain, reason),
        )
        await db.commit()


async def list_blacklist() -> list[dict]:
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (
            await db.execute("SELECT * FROM momentum_blacklist ORDER BY added_at DESC")
        ).fetchall()
    return [dict(r) for r in rows]
