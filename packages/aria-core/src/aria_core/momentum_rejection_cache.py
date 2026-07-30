"""Rejection cache for the momentum discovery pipeline (Item #193, 30/07,
operator-raised concern: "pourquoi les meme token sont re scanne c'est
inutile" -- a token that just failed a STABLE hard gate (liquidity, volume,
wash-trading ratio, no verified profile, holder concentration) was
re-evaluated from scratch on every subsequent cycle, wasting a network
round-trip (``fetch_token_pairs`` + whatever paid checks already ran) for a
verdict that hasn't had time to change.

Deliberately narrow: only STABLE rejection reasons are cached (see
``CACHEABLE_REASONS`` below) -- never ``already_parabolic`` (price moves
every minute, caching it would delay noticing a real pullback), never
``blacklisted``/any honeypot code (both already have their own permanent/
retry-oriented handling elsewhere: ``momentum_blacklist.py`` for a confirmed
scam, ``momentum_entry._check_honeypot``'s own retry logic for an infra
hiccup that must be retried ASAP, never suppressed for hours).

Persisted (survives redeployments), same doctrine as
``momentum_blacklist.py`` -- but with an expiry, unlike a permanent ban."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import aiosqlite

from aria_core.paths import aria_db_path

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())

# 30/07 -- first-pass TTL, not yet calibrated against real outcomes (same
# doctrine as other first-cut constants in this module): long enough to
# spare repeated network round-trips across several 15-30min discovery
# cycles, short enough that a real change (volume picking up, holders
# diversifying) is noticed within a few hours rather than staying invisible
# for a full day.
REJECTION_CACHE_TTL_SECONDS = 2 * 3600

# Only hold_reason values that reflect a SLOW-MOVING property of the
# pool/project -- never a price-level check (``already_parabolic``) or an
# infrastructure-availability code (``honeypot_unavailable``/
# ``chain_not_covered``), which must be retried as soon as possible, not
# suppressed for hours.
CACHEABLE_REASONS = frozenset({
    "insufficient_liquidity",
    "volume_too_low",
    "wash_trading_ratio",
    "no_verified_profile",
    "holder_concentration",
})


def _normalize_contract(contract: str, chain: str) -> str:
    """Same case-handling as ``momentum_blacklist.py`` (Base/EVM tolerates
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
            CREATE TABLE IF NOT EXISTS momentum_rejection_cache (
                contract TEXT NOT NULL,
                chain TEXT NOT NULL,
                reason TEXT NOT NULL,
                rejected_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                PRIMARY KEY (contract, chain)
            )
            """
        )
        await db.commit()


async def record_rejection(contract: str, chain: str, reason: str) -> None:
    """Called right before ``evaluate_hard_gates`` returns a HOLD verdict
    whose ``hold_reason`` is in ``CACHEABLE_REASONS``. Silently a no-op for
    any other reason -- the caller is expected to consult
    ``CACHEABLE_REASONS`` itself; this is just the write, never a second
    gate. Also opportunistically purges expired rows (reuses the already-open
    connection, same low-cost hygiene as ``momentum_timing._purge_expired_
    evaluations``) so this table never grows unbounded."""
    if reason not in CACHEABLE_REASONS:
        return
    await _ensure_table()
    chain = (chain or "").strip().lower()
    contract = _normalize_contract(contract, chain)
    if not contract or not chain:
        return
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=REJECTION_CACHE_TTL_SECONDS)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO momentum_rejection_cache "
            "(contract, chain, reason, rejected_at, expires_at) VALUES (?, ?, ?, ?, ?)",
            (contract, chain, reason, now.isoformat(), expires_at.isoformat()),
        )
        await db.execute(
            "DELETE FROM momentum_rejection_cache WHERE expires_at < ?", (now.isoformat(),),
        )
        await db.commit()


async def recently_rejected(contract: str, chain: str) -> str | None:
    """Checked FIRST in ``evaluate_hard_gates``, before any network call --
    returns the cached ``hold_reason`` if still within its TTL, else
    ``None`` (never seen, or expired -- a fresh evaluation is warranted
    either way). Never raises, never blocks a fresh evaluation on a lookup
    failure."""
    await _ensure_table()
    chain = (chain or "").strip().lower()
    contract = _normalize_contract(contract, chain)
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (
            await db.execute(
                "SELECT reason, expires_at FROM momentum_rejection_cache "
                "WHERE contract = ? AND chain = ?",
                (contract, chain),
            )
        ).fetchone()
    if row is None:
        return None
    reason, expires_at_raw = row
    try:
        expires_at = datetime.fromisoformat(expires_at_raw)
    except (TypeError, ValueError):
        return None
    if datetime.now(timezone.utc) >= expires_at:
        return None
    return reason
