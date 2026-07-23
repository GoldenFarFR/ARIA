"""Candidate selection for holder extraction (21/07, explicit operator
request) -- replaces the previous source (``screened_token``, the
VC-thesis pool) with the discovery flow ALREADY built for the momentum
pipeline (``momentum_entry.discover_momentum_candidates``, DexScreener
boosts/profiles + GeckoTerminal Base, continuous, no upstream security
filter).

Exact flow requested by the operator:
  1. Raw discovery (DexScreener + GeckoTerminal, ``discover_momentum_candidates``).
  2. Filter: GoPlus (honeypot, ``momentum_entry.check_honeypot`` -- same
     fail-closed hard gate as the trading pipeline, never a lightened
     version) + liquidity ≥$50,000 + 24h volume ≥$1,000 (the SAME
     thresholds as the momentum pipeline -- "it moves a lot at this low
     liquidity", a deliberately targeted volatile zone).
  3. OK → eligible for Blockscout x402 extraction.
  4. Not OK (confirmed honeypot) → PERMANENT blacklist (``token_candidate_blacklist``,
     same doctrine as ``momentum_blacklist.py``/``smart_money_rejected_wallets``
     -- no symmetric removal function, never retested).
     Insufficient liquidity/volume → NOT blacklisted (can grow and become
     eligible again later) -- only a CONFIRMED security signal (honeypot) is
     permanent, never a simple current lack of traction.
  5. Already extracted (``token_holder_intel``) → skipped, never recounted or reblacklisted.

Distinct from ``momentum_blacklist.py`` (contracts banned from the TRADING
pipeline for confirmed wash-trading): here we ban candidates for holder
EXTRACTION -- same risk (honeypot), different context, separate table so
the two mechanisms are never confused when reread later."""
from __future__ import annotations

import logging
import os

import aiosqlite

from aria_core.paths import aria_db_path

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())

# Chains covered by this screening -- Base only for now, consistent with
# token_holder_extraction_cycle.py (Blockscout x402 verified on Base only
# to date).
_CHAIN = "base"


def _normalize_contract(contract: str) -> str:
    return (contract or "").strip().lower()


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS token_candidate_blacklist (
                contract TEXT NOT NULL,
                chain TEXT NOT NULL,
                reason TEXT NOT NULL,
                blacklisted_at TEXT NOT NULL,
                PRIMARY KEY (contract, chain)
            )
            """
        )
        await db.commit()


async def is_candidate_blacklisted(contract: str, chain: str = _CHAIN) -> bool:
    await _ensure_table()
    contract = _normalize_contract(contract)
    chain = (chain or "").strip().lower()
    if not contract:
        return False
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (
            await db.execute(
                "SELECT 1 FROM token_candidate_blacklist WHERE contract = ? AND chain = ?",
                (contract, chain),
            )
        ).fetchone()
    return row is not None


async def _blacklist_candidate(contract: str, chain: str, reason: str) -> None:
    """Permanent -- same doctrine as ``momentum_blacklist.py`` (trading
    contracts) and ``smart_money_leaderboard.mark_rejected`` (wallets): no
    symmetric removal function, a confirmed-dangerous candidate stays that
    way."""
    await _ensure_table()
    contract = _normalize_contract(contract)
    chain = (chain or "").strip().lower()
    if not contract or not chain:
        return
    from datetime import datetime, timezone

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO token_candidate_blacklist (contract, chain, reason, blacklisted_at) "
            "VALUES (?, ?, ?, ?)",
            (contract, chain, reason, datetime.now(timezone.utc).isoformat()),
        )
        await db.commit()


async def list_blacklisted_candidates(limit: int = 100) -> list[dict]:
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (
            await db.execute(
                "SELECT contract, chain, reason, blacklisted_at FROM token_candidate_blacklist "
                "ORDER BY blacklisted_at DESC LIMIT ?",
                (limit,),
            )
        ).fetchall()
    return [dict(r) for r in rows]


async def screen_and_select_candidates(limit: int) -> list[tuple[str, str]]:
    """Discovers (DexScreener/GeckoTerminal), filters (GoPlus + liquidity/volume),
    blacklists confirmed security failures, and returns up to ``limit``
    candidates ready for holder extraction -- ``[(contract, symbol), ...]``,
    same shape as the old ``_select_next_tokens``/``screened_token`` source
    for a direct (drop-in) wiring into ``token_holder_extraction_cycle.py``.

    Best-effort at every stage (a discovery/filter failure on ONE candidate
    never blocks the others, cf. the standard dome of the rest of the
    momentum pipeline)."""
    from aria_core import token_holder_intel
    from aria_core.momentum_entry import (
        _MIN_LIQUIDITY_USD,
        _MIN_VOLUME_24H_USD,
        check_honeypot,
        discover_momentum_candidates,
    )
    from aria_core.services.dexscreener import fetch_tokens_batch

    try:
        raw = await discover_momentum_candidates(chains=(_CHAIN,))
    except Exception as exc:  # noqa: BLE001
        logger.info("token_candidate_screening: discovery failed (%s)", exc)
        return []
    if not raw:
        return []

    already_extracted = {
        c["contract"].lower() for c in await token_holder_intel.list_extracted_contracts(_CHAIN)
    }

    candidates: list[dict] = []
    for c in raw:
        addr = c["contract"].lower()
        if addr in already_extracted:
            continue
        if await is_candidate_blacklisted(addr, _CHAIN):
            continue
        candidates.append(c)
    if not candidates:
        return []

    # Batch enrichment (DexScreener, free, 30 addresses/call) -- real
    # liquidity + volume for the filter, never guessed from the discovery
    # pre-filter (which only checks liquidity, never volume).
    pairs_by_contract: dict[str, object] = {}
    for i in range(0, len(candidates), 30):
        chunk = [c["contract"] for c in candidates[i : i + 30]]
        try:
            pairs = await fetch_tokens_batch(chunk, chain=_CHAIN)
        except Exception as exc:  # noqa: BLE001
            logger.info("token_candidate_screening: DexScreener enrichment failed (%s)", exc)
            continue
        for p in pairs:
            addr = (p.base_address or "").lower()
            if not addr:
                continue
            existing = pairs_by_contract.get(addr)
            if existing is None or p.liquidity_usd > existing.liquidity_usd:
                pairs_by_contract[addr] = p

    selected: list[tuple[str, str]] = []
    for c in candidates:
        if len(selected) >= limit:
            break
        addr = c["contract"].lower()
        pair = pairs_by_contract.get(addr)
        if pair is None:
            continue  # no resolved pair -- never a candidate kept without real data
        if pair.liquidity_usd < _MIN_LIQUIDITY_USD or pair.volume_24h_usd < _MIN_VOLUME_24H_USD:
            continue  # not enough traction for now -- NOT blacklisted, can grow

        try:
            clear, reason, code = await check_honeypot(addr, _CHAIN)
        except Exception as exc:  # noqa: BLE001
            logger.info("token_candidate_screening: honeypot check failed for %s (%s)", addr, exc)
            continue  # technical failure -- neither kept nor blacklisted, retried next cycle

        if not clear:
            if code == "honeypot_rejected":
                await _blacklist_candidate(addr, _CHAIN, reason)
            continue  # infra failure (unavailable/chain_not_covered) -- never blacklisted

        selected.append((c["contract"], pair.base_symbol or ""))

    return selected
