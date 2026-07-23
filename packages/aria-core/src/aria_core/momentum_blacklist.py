"""Contract blacklist for the momentum pipeline (#194) -- explicit operator
request (17/07), direct follow-up to the real loss on BRIAN (-17.9%, -$8,962,
trailing stop): the contract is part of a swarm of narrative decoys identified
by VPS Research that same evening (vanity prefix ``0xB200000000000000000000...``,
generic ``token_name`` masked behind a narrative ticker -- "Coinbase Man"
for BRIAN, same pattern as "Base Man"/COBIE and "Coinbase Woman"/EMILIE --
wash-trading ~91x the liquidity on the main pool). The GoPlus honeypot check alone
does not detect this pattern (the contract isn't a technical honeypot, just a
visibility trap) -- this list fills the gap for already-confirmed cases,
on top of the volume/liquidity ratio cap (generic defense, see
``_check_wash_trading_ratio`` in ``momentum_entry.py``) which targets the PATTERN.

Persisted (survives redeployments, unlike a Python constant) -- same
doctrine as ARIA's other logs (append-only in practice, never
deleted: a banned contract stays banned, adding a new entry is the
only write point)."""
from __future__ import annotations

import logging

import aiosqlite

from aria_core.paths import aria_db_path

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())


def _normalize_contract(contract: str, chain: str) -> str:
    """Same fix as ``momentum_entry._normalize_contract`` (18/07, real bug):
    Base/Robinhood (EVM hex) tolerate lowercase, Solana (base58) does not -- case
    is part of the value there. Duplicated here (not imported from momentum_entry,
    which already imports this module -- would create a cycle): no Solana entry
    exists yet in this guardrail, but writing AND reading must stay
    consistent from the very first one, to never silently introduce the same
    trap found on the GoPlus/RugCheck side."""
    contract = (contract or "").strip()
    if (chain or "").strip().lower() != "solana":
        contract = contract.lower()
    return contract

# Idempotent seeding (INSERT OR IGNORE) -- a contract already present is never
# rewritten, a new session never loses a ban already decided elsewhere.
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
    """Checked FIRST in ``evaluate_momentum_entry`` -- no network call,
    the fastest and most definitive check in the pipeline.

    Case-insensitive comparison (18/07) -- even though the only real caller
    today (``evaluate_momentum_entry``) already preserves consistent casing end
    to end, a SECURITY guardrail should never depend on identical casing by
    convention: a future caller (manual command, import) that passed a different
    case must never silently produce a FALSE NEGATIVE."""
    await _ensure_table()
    chain = (chain or "").strip().lower()
    contract = _normalize_contract(contract, chain).lower()
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (
            await db.execute(
                "SELECT 1 FROM momentum_blacklist WHERE LOWER(contract) = ? AND chain = ?",
                (contract, chain),
            )
        ).fetchone()
    return row is not None


async def add_to_blacklist(contract: str, chain: str, reason: str) -> None:
    """Bans a contract -- never a symmetric removal by design (a
    banned contract stays banned; lifting a ban, if ever necessary, would be an
    explicit operator decision to be tracked separately, not a function here)."""
    await _ensure_table()
    chain = (chain or "").strip().lower()
    contract = _normalize_contract(contract, chain)
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
