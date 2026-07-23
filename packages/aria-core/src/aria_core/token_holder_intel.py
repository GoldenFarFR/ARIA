"""Local storage of enriched holders extracted via Blockscout Pro (x402,
``services/blockscout_x402.py``) -- response to the operator's request
(21/07) to build wallet/entity intelligence IN-HOUSE (same objective family
as Nansen/Arkham, diligenced, never purchased) rather than depending on a
paid third-party provider.

Stored in ARIA's SQLite database (``aria.db``, same file as
``screened_token``/``wallet_score_log``/``x402_spend_log``) -- NEVER in a
Git repo (public or private): a Git repo serves the CODE, not a dataset that
keeps growing (same Sobriety doctrine already applied everywhere else in
this project).

A snapshot, not an append-only journal (unlike ``momentum_blacklist``/
``x402_spend_log``): the list of a token's holders changes over time,
``store_holders`` REPLACES the previous snapshot for this (contract, chain)
instead of stacking it -- otherwise a ``get_holders`` query would mix a
state from 3 weeks ago with today's state with no way to tell them apart."""
from __future__ import annotations

import json
import logging

import aiosqlite

from aria_core.paths import aria_db_path

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())


def _normalize_contract(contract: str, chain: str) -> str:
    """Same fix as ``momentum_entry.normalize_contract_case``/
    ``momentum_blacklist._normalize_contract`` (18/07, real bug): Base/Robinhood
    (EVM hex) tolerate lowercase, Solana (base58) doesn't -- case is part of
    the value there. Duplicated here rather than imported (generic storage
    module, no dependency toward a momentum-specific module) -- fixes a real
    bug found on 21/07: the same token (cbBTC) stored once in checksum case
    and once in lowercase produced TWO distinct rows for the same real
    contract."""
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
    """Replaces the holders snapshot for this (contract, chain) -- a single
    transaction (DELETE then INSERT), never a partial state visible in
    between. Returns the number of rows written. Empty ``holders`` writes
    nothing and deletes nothing either -- a failed extraction (empty list
    from dome degradation) must never erase a previous valid snapshot."""
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
    """Freshness of the extraction for this token -- used to decide whether
    it's worth paying $0.002 again for a token already extracted recently
    (the mass-extraction batch relies on this to never pay for the same
    token twice without reason)."""
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
    """Overview -- one contract per row, number of holders stored and
    freshness -- to audit the coverage already built without re-reading everything."""
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
    """On WHICH already-extracted tokens (ARIA's partial coverage, never an
    exhaustive chain scan) this address appears as a notable holder --
    response to 21/07: a possible coordination signal (legitimate market
    maker OR Sybil cluster) for `smart_money.py`, never a performance score
    -- a different category, see ``WalletScoreCard.
    cross_token_holdings``, never mixed with ``composite_percentile``.

    An empty result never means "this wallet is nowhere" -- only "not found
    in the tokens ARIA has extracted so far"."""
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


# Known entity labels (exchanges, burn, incidents) -- a wallet that recurs
# across several tokens BECAUSE IT IS an exchange platform isn't a "good
# investor" signal, just normal market infrastructure. Confirmed in real
# conditions (21/07): without this filter, the leaderboard was entirely
# dominated by Coinbase/Binance/Kraken/etc. hot wallets.
_INFRA_TAG_KEYWORDS = (
    "exchange", "hot wallet", "coinbase", "binance", "kraken", "bybit", "gate",
    "kucoin", "bitvavo", "mexc", "null", "burn", "phish", "hack",
)


def _has_infra_tag(tags_concat: str) -> bool:
    lowered = (tags_concat or "").lower()
    return any(kw in lowered for kw in _INFRA_TAG_KEYWORDS)


async def list_cross_token_candidates(*, min_token_count: int = 3, chain: str = "base") -> list[dict]:
    """Addresses (EOA) that appear as a notable holder on at least
    ``min_token_count`` DISTINCT already-extracted tokens -- population-wide,
    unlike ``wallet_cross_token_holdings`` which answers for ONE already-known
    address. This is candidate discovery for the "top investors" leaderboard
    (21/07, operator request) -- systematically excludes any address carrying
    a known infrastructure label (exchange/burn/incident), never an
    individual conviction signal."""
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
