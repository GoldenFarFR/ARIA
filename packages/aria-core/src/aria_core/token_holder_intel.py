"""Stockage local des holders enrichis extraits via Blockscout Pro (x402,
``services/blockscout_x402.py``) -- réponse à la demande opérateur (21/07) de
bâtir une intelligence wallet/entité EN INTERNE (même famille d'objectif que
Nansen/Arkham, diligenciés, jamais achetés) plutôt que de dépendre d'un
fournisseur tiers payant.

Stockage dans la base SQLite d'ARIA (``aria.db``, même fichier que
``screened_token``/``wallet_score_log``/``x402_spend_log``) -- JAMAIS dans un
dépôt Git (public ou privé) : un dépôt Git sert le CODE, pas un jeu de données
qui grossit en continu (même doctrine Sobriété déjà appliquée partout ailleurs
dans ce projet).

Snapshot, pas un journal append-only (contrairement à ``momentum_blacklist``/
``x402_spend_log``) : la liste des holders d'un token évolue dans le temps,
``store_holders`` REMPLACE l'instantané précédent pour ce (contract, chain) au
lieu de l'empiler -- sinon une requête ``get_holders`` mélangerait un état
d'il y a 3 semaines avec un état d'aujourd'hui sans aucun moyen de les
distinguer."""
from __future__ import annotations

import json
import logging

import aiosqlite

from aria_core.paths import aria_db_path

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())


def _normalize_contract(contract: str, chain: str) -> str:
    """Même correctif que ``momentum_entry.normalize_contract_case``/
    ``momentum_blacklist._normalize_contract`` (18/07, bug réel) : Base/Robinhood
    (hex EVM) tolèrent le lowercase, Solana (base58) non -- la casse y fait
    partie de la valeur. Dupliqué ici plutôt qu'importé (module de stockage
    générique, pas de dépendance vers un module momentum-spécifique) --
    corrige un vrai bug trouvé le 21/07 : un même token (cbBTC) stocké une
    fois en casse checksum et une fois en lowercase produisait DEUX lignes
    distinctes pour le même contrat réel."""
    contract = (contract or "").strip()
    if (chain or "").strip().lower() != "solana":
        contract = contract.lower()
    return contract


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS token_holder_intel (
                contract TEXT NOT NULL,
                chain TEXT NOT NULL,
                holder_address TEXT NOT NULL,
                holder_name TEXT,
                is_contract INTEGER NOT NULL DEFAULT 0,
                is_verified INTEGER NOT NULL DEFAULT 0,
                is_scam INTEGER NOT NULL DEFAULT 0,
                reputation TEXT,
                tags TEXT NOT NULL DEFAULT '[]',
                value TEXT,
                fetched_at TEXT NOT NULL,
                PRIMARY KEY (contract, chain, holder_address)
            )
            """
        )
        await db.commit()


async def store_holders(contract: str, chain: str, holders: list[dict]) -> int:
    """Remplace l'instantané holders de ce (contract, chain) -- transaction
    unique (DELETE puis INSERT), jamais un état partiel visible entre les deux.
    Retourne le nombre de lignes écrites. ``holders`` vide n'écrit rien et ne
    supprime rien non plus -- une extraction ratée (liste vide par dégradation
    dôme) ne doit jamais effacer un instantané valide précédent."""
    if not holders:
        return 0
    await _ensure_table()
    chain = (chain or "").strip().lower()
    contract = _normalize_contract(contract, chain)
    if not contract or not chain:
        return 0
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM token_holder_intel WHERE contract = ? AND chain = ?",
            (contract, chain),
        )
        rows = [
            (
                contract,
                chain,
                h.get("holder_address", ""),
                h.get("holder_name"),
                1 if h.get("is_contract") else 0,
                1 if h.get("is_verified") else 0,
                1 if h.get("is_scam") else 0,
                str(h.get("reputation")) if h.get("reputation") is not None else None,
                json.dumps(h.get("tags") or []),
                str(h.get("value")) if h.get("value") is not None else None,
            )
            for h in holders
            if h.get("holder_address")
        ]
        await db.executemany(
            """
            INSERT INTO token_holder_intel (
                contract, chain, holder_address, holder_name, is_contract,
                is_verified, is_scam, reputation, tags, value, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            rows,
        )
        await db.commit()
    return len(rows)


async def get_holders(contract: str, chain: str) -> list[dict]:
    await _ensure_table()
    chain = (chain or "").strip().lower()
    contract = _normalize_contract(contract, chain)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (
            await db.execute(
                "SELECT * FROM token_holder_intel WHERE contract = ? AND chain = ? "
                "ORDER BY CAST(value AS REAL) DESC",
                (contract, chain),
            )
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["tags"] = json.loads(d.get("tags") or "[]")
        except Exception:  # noqa: BLE001
            d["tags"] = []
        out.append(d)
    return out


async def last_extracted_at(contract: str, chain: str) -> str | None:
    """Fraîcheur de l'extraction pour ce token -- sert à décider si ça vaut la
    peine de repayer 0,002$ pour un token déjà extrait récemment (le batch
    d'extraction en masse s'appuie dessus pour ne jamais repayer deux fois le
    même token sans raison)."""
    await _ensure_table()
    chain = (chain or "").strip().lower()
    contract = _normalize_contract(contract, chain)
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (
            await db.execute(
                "SELECT MAX(fetched_at) FROM token_holder_intel WHERE contract = ? AND chain = ?",
                (contract, chain),
            )
        ).fetchone()
    return row[0] if row else None


async def list_extracted_contracts(chain: str = "base") -> list[dict]:
    """Vue d'ensemble -- un contrat par ligne, nombre de holders stockés et
    fraîcheur -- pour auditer la couverture déjà bâtie sans tout relire."""
    await _ensure_table()
    chain = (chain or "").strip().lower()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (
            await db.execute(
                "SELECT contract, COUNT(*) AS holder_count, MAX(fetched_at) AS fetched_at "
                "FROM token_holder_intel WHERE chain = ? GROUP BY contract "
                "ORDER BY fetched_at DESC",
                (chain,),
            )
        ).fetchall()
    return [dict(r) for r in rows]


async def wallet_cross_token_holdings(address: str, *, chain: str = "base") -> list[dict]:
    """Sur QUELS tokens déjà extraits (couverture partielle d'ARIA, jamais un
    scan exhaustif de la chaîne) cette adresse apparaît comme détenteur notable
    -- réponse au 21/07 : signal de coordination possible (market maker
    légitime OU cluster Sybil) pour `smart_money.py`, jamais un score de
    performance -- catégorie différente, voir ``WalletScoreCard.
    cross_token_holdings``, jamais mélangé au ``composite_percentile``.

    Un résultat vide ne veut jamais dire "ce wallet n'est nulle part" --
    seulement "pas trouvé dans les tokens qu'ARIA a déjà extraits à ce jour"."""
    await _ensure_table()
    address = (address or "").strip()
    chain = (chain or "").strip().lower()
    if not address:
        return []
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (
            await db.execute(
                "SELECT contract, is_contract, is_verified, tags, value, fetched_at "
                "FROM token_holder_intel WHERE LOWER(holder_address) = LOWER(?) AND chain = ? "
                "ORDER BY fetched_at DESC",
                (address, chain),
            )
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["tags"] = json.loads(d.get("tags") or "[]")
        except Exception:  # noqa: BLE001
            d["tags"] = []
        out.append(d)
    return out


# Labels d'entité connue (exchanges, burn, incidents) -- un wallet qui revient
# sur plusieurs tokens PARCE QU'IL EST une plateforme d'échange n'est pas un
# signal de "bon investisseur", juste une infrastructure de marché normale.
# Confirmé en conditions réelles (21/07) : sans ce filtre, le classement était
# entièrement dominé par des hot wallets Coinbase/Binance/Kraken/etc.
_INFRA_TAG_KEYWORDS = (
    "exchange", "hot wallet", "coinbase", "binance", "kraken", "bybit", "gate",
    "kucoin", "bitvavo", "mexc", "null", "burn", "phish", "hack",
)


def _has_infra_tag(tags_concat: str) -> bool:
    lowered = (tags_concat or "").lower()
    return any(kw in lowered for kw in _INFRA_TAG_KEYWORDS)


async def list_cross_token_candidates(*, min_token_count: int = 3, chain: str = "base") -> list[dict]:
    """Adresses (EOA) qui apparaissent comme détenteur notable sur au moins
    ``min_token_count`` tokens DISTINCTS déjà extraits -- population-wide,
    contrairement à ``wallet_cross_token_holdings`` qui répond pour UNE
    adresse déjà connue. C'est la découverte de candidats pour le classement
    "meilleurs investisseurs" (21/07, demande opérateur) -- exclut
    systématiquement toute adresse porteuse d'un label d'infrastructure connu
    (exchange/burn/incident), jamais un signal de conviction individuelle."""
    await _ensure_table()
    chain = (chain or "").strip().lower()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (
            await db.execute(
                "SELECT holder_address, COUNT(DISTINCT contract) AS token_count, "
                "GROUP_CONCAT(DISTINCT tags) AS all_tags "
                "FROM token_holder_intel WHERE chain = ? AND is_contract = 0 "
                "GROUP BY LOWER(holder_address) "
                "HAVING token_count >= ? "
                "ORDER BY token_count DESC",
                (chain, min_token_count),
            )
        ).fetchall()
    out = []
    for r in rows:
        if _has_infra_tag(r["all_tags"]):
            continue
        out.append({"holder_address": r["holder_address"], "token_count": r["token_count"]})
    return out
