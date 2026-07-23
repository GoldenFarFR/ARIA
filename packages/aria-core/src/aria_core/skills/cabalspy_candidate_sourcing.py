"""Candidate wallet sourcing from CabalSpy (23/07, explicit operator decision
-- an acknowledged policy change, see the `services/cabalspy.py` docstring).

Two clearly separated tracks, never mixed:
1. **Categorization** (`cabalspy_kol_wallets`): ALL labeled wallets fetched,
   ACROSS ALL chains (Base/BNB/Solana) -- a simple directory, doesn't
   prejudge their score in any way, never a trading signal.
2. **Real sourcing into scoring** (`wallet_scan_queue.enqueue_wallets`):
   Base wallets ONLY -- the only downstream pipeline (`smart_money.py`,
   Blockscout) that knows how to process them today (hardcoded Base-only,
   verified in the code). BNB (EVM, extension effort not yet verified) and
   Solana (different address format, no Blockscout, separate project) are
   categorized but never enqueued into scoring as long as this pipeline
   isn't extended -- avoid wrongly scoring an address with the wrong
   explorer rather than guessing at degraded behavior.

Type "kol" prioritized (complete identity: name/twitter/telegram, verified
real on Base -- 200 wallets). Type "smart" wired too (honest fallback) but
flagged as a likely duplicate of what `smart_money.py` already detects by
behavior, for free -- never recommended as a priority source."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import aiosqlite

from aria_core.paths import aria_db_path

DB_PATH = str(aria_db_path())

# Categorized chains (all) vs. chains actually sourced into scoring
# (Base only, downstream pipeline verified able to process them).
_CATALOGUED_BLOCKCHAINS = ("base", "bnb", "solana")
_SCORABLE_BLOCKCHAINS = ("base",)

# The KOL list doesn't change from one day to the next -- avoid re-fetching
# on every heartbeat cycle (saves CabalSpy credits, 300-10000/month depending
# on tier). A full sync once a week is plenty.
MIN_RESYNC_INTERVAL_DAYS = 7


def cabalspy_sourcing_enabled() -> bool:
    return os.environ.get("ARIA_CABALSPY_SOURCING_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


async def _ensure_tables() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS cabalspy_kol_wallets (
                wallet TEXT NOT NULL,
                blockchain TEXT NOT NULL,
                wallet_type TEXT NOT NULL,
                name TEXT NOT NULL DEFAULT '',
                twitter TEXT NOT NULL DEFAULT '',
                telegram TEXT NOT NULL DEFAULT '',
                sourced_at TEXT NOT NULL,
                PRIMARY KEY (wallet, blockchain, wallet_type)
            )
            """
        )
        await db.execute(
            "CREATE TABLE IF NOT EXISTS cabalspy_sourcing_state (id INTEGER PRIMARY KEY CHECK (id = 1), last_full_sync_at TEXT)"
        )
        await db.commit()


async def _last_full_sync_at() -> datetime | None:
    await _ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (await db.execute("SELECT last_full_sync_at FROM cabalspy_sourcing_state WHERE id = 1")).fetchone()
    if not row or not row[0]:
        return None
    try:
        return datetime.fromisoformat(row[0])
    except ValueError:
        return None


async def _mark_full_sync_done(now: datetime) -> None:
    await _ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO cabalspy_sourcing_state (id, last_full_sync_at) VALUES (1, ?) "
            "ON CONFLICT(id) DO UPDATE SET last_full_sync_at = excluded.last_full_sync_at",
            (now.isoformat(),),
        )
        await db.commit()


async def _store_wallets(wallets: list, *, now: datetime) -> int:
    if not wallets:
        return 0
    await _ensure_tables()
    stored = 0
    async with aiosqlite.connect(DB_PATH) as db:
        for w in wallets:
            cursor = await db.execute(
                "INSERT OR REPLACE INTO cabalspy_kol_wallets "
                "(wallet, blockchain, wallet_type, name, twitter, telegram, sourced_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (w.wallet_address.lower(), w.blockchain, w.type, w.name, w.twitter, w.telegram, now.isoformat()),
            )
            if cursor.rowcount:
                stored += 1
        await db.commit()
    return stored


async def catalogued_wallets(blockchain: str | None = None) -> list[dict]:
    """Categorized directory -- read-only, never a trading signal in
    itself. Filterable by chain."""
    await _ensure_tables()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if blockchain:
            cursor = await db.execute(
                "SELECT * FROM cabalspy_kol_wallets WHERE blockchain = ? ORDER BY sourced_at DESC", (blockchain,),
            )
        else:
            cursor = await db.execute("SELECT * FROM cabalspy_kol_wallets ORDER BY blockchain, sourced_at DESC")
        rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def run_cabalspy_candidate_sourcing_cycle(notifier=None, *, now: datetime | None = None) -> dict:
    """One pass: if the last full sync is less than
    `MIN_RESYNC_INTERVAL_DAYS` old, does nothing (saves credits). Otherwise,
    fetches the "kol" list for each categorized chain, stores EVERYTHING in
    the directory, then enqueues ONLY Base wallets into `wallet_scan_queue`
    (the only scoring pipeline that processes them today). Dedicated gate +
    downstream (queue/scoring), fail-closed, respects the kill-switch."""
    if not cabalspy_sourcing_enabled():
        return {"outcome": "skipped", "reason": "gate_off"}

    from aria_core.services.cabalspy import is_cabalspy_configured, list_wallets
    from aria_core.services.smart_money import wallet_scoring_enabled
    from aria_core.services.wallet_scan_queue import enqueue_wallets, wallet_scan_queue_enabled

    if not is_cabalspy_configured():
        return {"outcome": "skipped", "reason": "no_api_key"}

    if not wallet_scan_queue_enabled() or not wallet_scoring_enabled():
        return {"outcome": "skipped", "reason": "downstream_disabled"}

    from aria_core import outgoing_pause

    if outgoing_pause.is_paused():
        return {"outcome": "skipped", "reason": "paused"}

    now = now or datetime.now(timezone.utc)
    last_sync = await _last_full_sync_at()
    if last_sync is not None and (now - last_sync) < timedelta(days=MIN_RESYNC_INTERVAL_DAYS):
        return {"outcome": "skipped", "reason": "resync_not_due", "last_full_sync_at": last_sync.isoformat()}

    per_chain: dict[str, int] = {}
    total_stored = 0
    base_wallets: list[str] = []

    for blockchain in _CATALOGUED_BLOCKCHAINS:
        wallets = await list_wallets(blockchain, wallet_type="kol")
        if not wallets:
            per_chain[blockchain] = 0
            continue
        stored = await _store_wallets(wallets, now=now)
        per_chain[blockchain] = stored
        total_stored += stored
        if blockchain in _SCORABLE_BLOCKCHAINS:
            base_wallets.extend(w.wallet_address for w in wallets)

    await _mark_full_sync_done(now)

    added = await enqueue_wallets(base_wallets) if base_wallets else []

    if (total_stored or added) and notifier is not None:
        detail = ", ".join(f"{chain}:{count}" for chain, count in per_chain.items())
        await notifier(
            f"🔍 Sourcing CabalSpy -- {total_stored} wallet(s) KOL catalogué(s) ({detail}), "
            f"{len(added)} ajouté(s) à la file de scoring (Base uniquement)."
        )

    return {"outcome": "ok", "stored_per_chain": per_chain, "queued_for_scoring": len(added)}
