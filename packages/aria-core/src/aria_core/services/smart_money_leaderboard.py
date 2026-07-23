""""Top investors" leaderboard (21/07, explicit operator request) -- top 600
EOA wallets spotted by cross-recurrence (>=3 tokens held among those already
extracted, see ``token_holder_intel.list_cross_token_candidates``), ranked by
REAL ``composite_percentile`` (``smart_money.py``, trading performance --
never a coordination/Sybil score, a different category, see
``WalletScoreCard.cross_token_holder_count``, never mixed in here).

Distinct from ``momentum_blacklist.py`` (security -- bans token CONTRACTS for
confirmed wash-trading, BRIAN incident): here we DEMOTE WALLETS for trading
underperformance, never a security/fraud matter -- terminology deliberately
kept separate ("leaderboard"/"archived", never "banned") so the two
mechanisms are never confused in the code or when re-reading it.

Rules (operator decision, 21/07, clarified after follow-up):
- A wallet only joins the leaderboard IF its ``composite_percentile`` is a
  real measured number (never a fixed default like 50/100 while the
  comparison population grows -- same "unavailable rather than fabricated"
  doctrine as everywhere else in ``smart_money.py``).
- Hard capacity: 600 (raised from 50 on 21/07). Beyond that, the lowest
  percentile(s) are removed and archived (reason "outside top 600 --
  capacity").
- Immediate eviction if ``composite_percentile < 30``, regardless of the
  leaderboard's current size (reason "percentile below 30").
- The leaderboard is re-evaluated on EVERY new score produced by
  ``wallet_scan_queue.run_wallet_scan_queue_cycle`` (full coverage reached
  only -- a partial score is no more reliable for ranking than it is for
  comparison, same exclusion as ``full_coverage=False`` elsewhere in
  ``smart_money.py``).

At a capacity of 600, the scan queue (``wallet_scan_queue.py``) can hold
almost as many wallets in weekly monitoring as its own weekly throughput
(1 wallet/20min ~= 504 scans/week) -- fixed the same day: ``list_pending()``
now prioritizes new candidates (catch-up) over plain monitoring rescans, so
discovery is never structurally starved."""
from __future__ import annotations

import os
from datetime import datetime, timezone

import aiosqlite

from aria_core.paths import aria_db_path

DB_PATH = str(aria_db_path())

MAX_LEADERBOARD_SIZE = 600
EVICTION_PERCENTILE_THRESHOLD = 30.0


def smart_money_leaderboard_enabled() -> bool:
    return os.environ.get("ARIA_SMART_MONEY_LEADERBOARD_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS smart_money_leaderboard (
                wallet TEXT PRIMARY KEY,
                composite_percentile REAL NOT NULL,
                joined_at TEXT NOT NULL,
                last_updated_at TEXT NOT NULL
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS smart_money_leaderboard_archive (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wallet TEXT NOT NULL,
                percentile_at_removal REAL,
                removed_at TEXT NOT NULL,
                reason TEXT NOT NULL
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS smart_money_rejected_wallets (
                wallet TEXT PRIMARY KEY,
                percentile_at_rejection REAL,
                rejected_at TEXT NOT NULL,
                reason TEXT NOT NULL
            )
            """
        )
        await db.commit()


async def is_rejected(wallet: str) -> bool:
    """A wallet confirmed as underperforming (measured percentile < 30) once
    is rejected PERMANENTLY -- checked by ``discover_and_enqueue_candidates``
    before any enqueuing, so it never reappears simply because it holds a
    NEW token discovered later (21/07, explicit operator request)."""
    await _ensure_table()
    wallet = (wallet or "").strip().lower()
    if not wallet:
        return False
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (
            await db.execute("SELECT 1 FROM smart_money_rejected_wallets WHERE wallet = ?", (wallet,))
        ).fetchone()
    return row is not None


async def mark_rejected(wallet: str, percentile: float | None, reason: str) -> None:
    """PERMANENT rejection -- no symmetric un-reject function, same doctrine
    as ``momentum_blacklist.py`` (a banned contract stays banned; here, a
    wallet confirmed bad stays bad). Idempotent (``INSERT OR IGNORE`` -- an
    already-rejected wallet is never overwritten)."""
    await _ensure_table()
    wallet = (wallet or "").strip().lower()
    if not wallet:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO smart_money_rejected_wallets "
            "(wallet, percentile_at_rejection, rejected_at, reason) VALUES (?, ?, ?, ?)",
            (wallet, percentile, datetime.now(timezone.utc).isoformat(), reason),
        )
        await db.commit()


async def _archive(db: aiosqlite.Connection, wallet: str, percentile: float | None, reason: str) -> None:
    await db.execute(
        "INSERT INTO smart_money_leaderboard_archive "
        "(wallet, percentile_at_removal, removed_at, reason) VALUES (?, ?, ?, ?)",
        (wallet, percentile, datetime.now(timezone.utc).isoformat(), reason),
    )


async def update_leaderboard(wallet: str, composite_percentile: float | None) -> str:
    """Inserts/updates/evicts a wallet based on its most recent REAL
    ``composite_percentile``. Returns the action taken (never an opaque
    boolean): ``no_percentile`` / ``not_eligible`` / ``added`` / ``updated`` /
    ``evicted_low_score`` / ``evicted_capacity``.

    ``composite_percentile=None`` (not enough population to compare):
    NO-OP, never a fabricated score to force an entry."""
    if composite_percentile is None:
        return "no_percentile"
    await _ensure_table()
    wallet = (wallet or "").strip().lower()
    if not wallet:
        return "no_percentile"

    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        if composite_percentile < EVICTION_PERCENTILE_THRESHOLD:
            existing = await (
                await db.execute("SELECT 1 FROM smart_money_leaderboard WHERE wallet = ?", (wallet,))
            ).fetchone()
            if not existing:
                return "not_eligible"
            await db.execute("DELETE FROM smart_money_leaderboard WHERE wallet = ?", (wallet,))
            await _archive(db, wallet, composite_percentile, "percentile below 30")
            await db.commit()
            return "evicted_low_score"

        row = await (
            await db.execute("SELECT 1 FROM smart_money_leaderboard WHERE wallet = ?", (wallet,))
        ).fetchone()
        if row:
            await db.execute(
                "UPDATE smart_money_leaderboard SET composite_percentile = ?, last_updated_at = ? WHERE wallet = ?",
                (composite_percentile, now, wallet),
            )
            action = "updated"
        else:
            await db.execute(
                "INSERT INTO smart_money_leaderboard "
                "(wallet, composite_percentile, joined_at, last_updated_at) VALUES (?, ?, ?, ?)",
                (wallet, composite_percentile, now, now),
            )
            action = "added"
        await db.commit()

        # Hard capacity: beyond 50, remove the lowest percentile(s).
        rows = await (
            await db.execute(
                "SELECT wallet, composite_percentile FROM smart_money_leaderboard "
                "ORDER BY composite_percentile DESC"
            )
        ).fetchall()
        if len(rows) > MAX_LEADERBOARD_SIZE:
            overflow = rows[MAX_LEADERBOARD_SIZE:]
            overflow_wallets = {w for w, _ in overflow}
            for w, pct in overflow:
                await db.execute("DELETE FROM smart_money_leaderboard WHERE wallet = ?", (w,))
                await _archive(db, w, pct, f"outside top {MAX_LEADERBOARD_SIZE} (capacity)")
            await db.commit()
            if wallet in overflow_wallets:
                action = "evicted_capacity"
        return action


async def remove_and_archive(wallet: str, reason: str) -> str:
    """EXPLICIT removal, independent of the percentile (unlike
    ``update_leaderboard``) -- fixes the gap found on 21/07: a wallet
    removed from ``wallet_scan_queue`` for inactivity (90d+ without real
    on-chain activity) kept its last score in the leaderboard forever, never
    flagged as "no longer tracked". Returns ``removed`` or ``not_present``
    (never an error if the wallet wasn't on the leaderboard -- nothing more
    to do)."""
    await _ensure_table()
    wallet = (wallet or "").strip().lower()
    if not wallet:
        return "not_present"
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (
            await db.execute(
                "SELECT composite_percentile FROM smart_money_leaderboard WHERE wallet = ?", (wallet,)
            )
        ).fetchone()
        if not row:
            return "not_present"
        await db.execute("DELETE FROM smart_money_leaderboard WHERE wallet = ?", (wallet,))
        await _archive(db, wallet, row[0], reason)
        await db.commit()
    return "removed"


async def get_leaderboard() -> list[dict]:
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (
            await db.execute(
                "SELECT wallet, composite_percentile, joined_at, last_updated_at "
                "FROM smart_money_leaderboard ORDER BY composite_percentile DESC"
            )
        ).fetchall()
    out = []
    for i, r in enumerate(rows, 1):
        d = dict(r)
        d["rank"] = i
        out.append(d)
    return out


async def get_archive(limit: int = 50) -> list[dict]:
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (
            await db.execute(
                "SELECT wallet, percentile_at_removal, removed_at, reason "
                "FROM smart_money_leaderboard_archive ORDER BY removed_at DESC LIMIT ?",
                (limit,),
            )
        ).fetchall()
    return [dict(r) for r in rows]


async def discover_and_enqueue_candidates(*, min_token_count: int = 3) -> dict:
    """Spots recurring EOA wallets (``token_holder_intel``, free -- pure
    local read) and enqueues them into ``wallet_scan_queue.py`` for real
    scoring -- a leaderboard trigger, never a score in itself. Idempotent
    (``enqueue_wallets`` already ignores duplicates -- a wallet already in
    the queue, catching up or monitored, is never re-enqueued).

    Triple gate -- ``ARIA_SMART_MONEY_LEADERBOARD_ENABLED`` on top of
    ``ARIA_WALLET_SCAN_QUEUE_ENABLED``/``ARIA_WALLET_SCORING_ENABLED`` (all
    OFF by default), same pattern as ``wallet_candidate_sourcing.py``."""
    if not smart_money_leaderboard_enabled():
        return {"outcome": "skipped", "reason": "gate_off"}

    from aria_core.services.smart_money import wallet_scoring_enabled
    from aria_core.services.wallet_scan_queue import enqueue_wallets, wallet_scan_queue_enabled

    if not wallet_scan_queue_enabled() or not wallet_scoring_enabled():
        return {"outcome": "skipped", "reason": "downstream_disabled"}

    from aria_core import outgoing_pause

    if outgoing_pause.is_paused():
        return {"outcome": "skipped", "reason": "paused"}

    from aria_core import token_holder_intel

    candidates = await token_holder_intel.list_cross_token_candidates(min_token_count=min_token_count)
    if not candidates:
        return {"outcome": "no_candidate"}

    # A wallet already rejected PERMANENTLY (confirmed percentile < 30) must
    # never reappear simply because it holds a new token discovered later
    # (21/07, explicit operator request).
    addresses = []
    already_rejected = 0
    for c in candidates:
        if await is_rejected(c["holder_address"]):
            already_rejected += 1
            continue
        addresses.append(c["holder_address"])
    if not addresses:
        return {"outcome": "no_candidate", "already_rejected": already_rejected}

    added = await enqueue_wallets(addresses)
    return {
        "outcome": "ok",
        "candidates_found": len(candidates),
        "already_rejected": already_rejected,
        "added_to_queue": len(added),
    }
