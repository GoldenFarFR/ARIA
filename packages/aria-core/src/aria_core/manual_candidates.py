"""Manually-submitted discovery candidates (Item #236, 30/07, real operator request:
"je vais en mettre deja une centaine manuellement de mon cote avec /add" --
a token spotted by the operator on DexScreener/elsewhere that the automated
discovery sources (GeckoTerminal top-N, Birdeye, DexScreener boosts/profiles)
haven't surfaced yet -- e.g. REPPO, a real liquid ($757K) Base token found
outside the top-100 GeckoTerminal window this session).

Deliberately NOT a buy shortcut: a manually-added contract joins the SAME
candidate pool as any automatically-discovered one (``discover_momentum_
candidates``), and goes through every existing hard gate (honeypot,
liquidity, volume, wash-trading, holder concentration, R/R) unchanged --
this module only fills the DISCOVERY gap, never the security/quality
gates. A contract that fails a gate is cached by ``momentum_rejection_
cache`` exactly like any other rejected candidate.

Persisted (survives redeployments), same doctrine as momentum_blacklist.py.
Expires after ``MANUAL_CANDIDATE_TTL_DAYS`` (7 days, same order of magnitude
as the weekly paper-trading reset) if never bought -- a token that never
forms a signal in a week isn't worth scanning forever, and an expired entry
is silently dropped (never a Telegram alert), same doctrine as a limit
order's own silent expiry."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import aiosqlite

from aria_core.paths import aria_db_path

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())

# 30/07, first-pass value (not yet calibrated against real outcomes) -- long
# enough to give a manually-added token a real chance to form a golden-pocket
# + RSI setup, short enough that a stale entry doesn't linger forever.
MANUAL_CANDIDATE_TTL_DAYS = 7.0


def _normalize_contract(contract: str, chain: str) -> str:
    """Same case-handling as momentum_blacklist.py (Base/EVM tolerates
    lowercase, Solana base58 does not) -- duplicated rather than imported,
    same anti-circular-import doctrine already documented there."""
    contract = (contract or "").strip()
    if (chain or "").strip().lower() != "solana":
        contract = contract.lower()
    return contract


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS manual_candidate_queue (
                contract TEXT NOT NULL,
                chain TEXT NOT NULL,
                added_at TEXT NOT NULL,
                PRIMARY KEY (contract, chain)
            )
            """
        )
        await db.commit()


async def add_manual_candidate(contract: str, chain: str = "base") -> bool:
    """Queues a contract for the next discovery cycle. Idempotent (``INSERT
    OR IGNORE`` -- an already-queued contract keeps its original
    ``added_at``, never re-extends its TTL by being submitted twice).
    Returns ``True`` if queued (new or already present), ``False`` if the
    contract/chain was empty."""
    await _ensure_table()
    chain = (chain or "base").strip().lower()
    contract = _normalize_contract(contract, chain)
    if not contract or not chain:
        return False
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO manual_candidate_queue (contract, chain, added_at) VALUES (?, ?, ?)",
            (contract, chain, datetime.now(timezone.utc).isoformat()),
        )
        await db.commit()
    return True


async def list_pending_manual_candidates() -> list[dict]:
    """Every still-fresh manually-queued contract -- checked by
    ``discover_momentum_candidates`` on every discovery cycle. Opportunistic
    purge of expired rows first (same low-cost hygiene as ``momentum_
    rejection_cache.record_rejection``), so this table never grows
    unbounded."""
    await _ensure_table()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=MANUAL_CANDIDATE_TTL_DAYS)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM manual_candidate_queue WHERE added_at < ?", (cutoff,))
        await db.commit()
        db.row_factory = aiosqlite.Row
        rows = await (
            await db.execute("SELECT * FROM manual_candidate_queue ORDER BY added_at ASC")
        ).fetchall()
    return [dict(r) for r in rows]


async def remove_manual_candidate(contract: str, chain: str) -> None:
    """Called once a manually-added candidate is bought -- no longer needs
    re-discovery every cycle (``paper_trader.has_open`` already prevents a
    double-buy, but there's no reason to keep re-scanning a filled order)."""
    await _ensure_table()
    chain = (chain or "").strip().lower()
    contract = _normalize_contract(contract, chain)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM manual_candidate_queue WHERE contract = ? AND chain = ?", (contract, chain),
        )
        await db.commit()
