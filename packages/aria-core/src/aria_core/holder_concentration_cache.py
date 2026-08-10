"""Persisted long-TTL verdict cache for
``momentum_entry._check_holder_concentration`` (06/08, operator request
during a real Blockscout outage: "coupe blockscout et laisse passer les
tokens ils sont déjà vérifié pas besoin de re passer à chaque fois dessus").

Deliberately distinct from ``momentum_rejection_cache.py``: that module
skips the WHOLE hard-gate pass for a stable rejection (2h TTL, in-memory
scope is the discovery pipeline's own re-scan cadence) and explicitly never
caches an infra-unavailability code ("must be retried ASAP, never
suppressed for hours" -- see its own CACHEABLE_REASONS comment). This module
solves a different problem: a token ALREADY verified once (cleared OR
rejected on real Blockscout data) must never be re-hit against a now-DOWN
Blockscout for hours/days, while a token NEVER successfully verified stays
exactly as blocked as before (fail-closed doctrine untouched, 03/08
decision not reverted). Only a REAL verdict (from live data) is ever
written here -- an "unavailable" read is never cached, so it keeps retrying
every cycle and picks up Blockscout's real recovery immediately."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import aiosqlite

from aria_core.paths import aria_db_path

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())

# 10/08 -- raised from 24h to 7 days, explicit operator decision (first
# set to 30 days, then narrowed to 7 the same day): holder concentration
# is a slow-moving property (who holds the token doesn't meaningfully
# shift for the low-cap tokens this pipeline targets) -- a token already
# verified once (cleared or rejected on real data) doesn't need re-hitting
# Blockscout/x402 daily, a weekly refresh is enough.
HOLDER_CONCENTRATION_CACHE_TTL_SECONDS = 7 * 24 * 3600


def _normalize_contract(contract: str, chain: str) -> str:
    """Same case-handling as momentum_rejection_cache.py/momentum_blacklist.py
    (Base/EVM tolerates lowercase, Solana base58 does not) -- duplicated
    rather than imported, same anti-circular-import doctrine already
    documented there."""
    contract = (contract or "").strip()
    if (chain or "").strip().lower() != "solana":
        contract = contract.lower()
    return contract


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS holder_concentration_verdict_cache (
                contract TEXT NOT NULL,
                chain TEXT NOT NULL,
                too_concentrated INTEGER NOT NULL,
                reason TEXT NOT NULL,
                verified_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                PRIMARY KEY (contract, chain)
            )
            """
        )
        await db.commit()


async def record_verdict(contract: str, chain: str, too_concentrated: bool, reason: str) -> None:
    """Called only after a REAL verdict was computed from live holder data
    (never for the ``_HOLDER_DATA_UNAVAILABLE_REASON`` sentinel -- that case
    is not a verified answer, see this module's own docstring). Also
    opportunistically purges expired rows, same low-cost hygiene as
    ``momentum_rejection_cache.record_rejection``."""
    await _ensure_table()
    chain = (chain or "").strip().lower()
    contract = _normalize_contract(contract, chain)
    if not contract or not chain:
        return
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=HOLDER_CONCENTRATION_CACHE_TTL_SECONDS)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO holder_concentration_verdict_cache "
            "(contract, chain, too_concentrated, reason, verified_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (contract, chain, int(too_concentrated), reason, now.isoformat(), expires_at.isoformat()),
        )
        await db.execute(
            "DELETE FROM holder_concentration_verdict_cache WHERE expires_at < ?", (now.isoformat(),),
        )
        await db.commit()


async def cached_verdict(contract: str, chain: str) -> tuple[bool, str] | None:
    """Checked FIRST in ``_check_holder_concentration``, before any network
    call -- returns the cached ``(too_concentrated, reason)`` if still
    within its TTL, else ``None`` (never verified, or expired -- a fresh
    evaluation is warranted either way). Never raises, never blocks a fresh
    evaluation on a lookup failure."""
    await _ensure_table()
    chain = (chain or "").strip().lower()
    contract = _normalize_contract(contract, chain)
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (
            await db.execute(
                "SELECT too_concentrated, reason, expires_at FROM holder_concentration_verdict_cache "
                "WHERE contract = ? AND chain = ?",
                (contract, chain),
            )
        ).fetchone()
    if row is None:
        return None
    too_concentrated, reason, expires_at_raw = row
    try:
        expires_at = datetime.fromisoformat(expires_at_raw)
    except (TypeError, ValueError):
        return None
    if datetime.now(timezone.utc) >= expires_at:
        return None
    return bool(too_concentrated), reason
