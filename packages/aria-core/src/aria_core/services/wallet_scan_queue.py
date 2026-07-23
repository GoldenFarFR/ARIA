"""Background wallet scan queue (15/07, direct follow-up to #157).

Operator observation: on a very active wallet (e.g. 680 tokens traded), even
the persistent incremental scan (`wallet_scan_state.py`) requires dozens of
manual `/walletscore` reminders to reach full coverage -- impractical in
normal use. This module lets you INJECT a wallet just once (`/walletqueue`
command) and then lets the heartbeat advance it on its own, several tokens at
a time, until full coverage -- ARIA then notifies the final result on
Telegram with no further action needed.

PERMANENT tracking (15/07, follow-up 2 -- explicit operator observation): a
wallet that reaches 100% is never removed from the queue again. It switches
to MONITORING mode (a light check once a week, `MONITORING_INTERVAL_DAYS`)
-- ARIA spots any new activity (newly traded tokens) without ever
re-requesting full coverage (already acquired, `_needs_scan` only picks up
what's new). The only exit: if the wallet shows NO real on-chain activity at
all since `INACTIVITY_CUTOFF_DAYS` (3 months), monitoring stops and the
wallet is removed -- never before, never on a simple elapsed-time-in-queue
threshold. The score threshold (removing a wallet whose score drops too low)
remains a deferred operator decision, not yet built.

Nothing duplicated: each pass reuses `smart_money.score_wallets` +
`wallet_scan_state.py` as-is (the existing incremental engine), this queue
only adds the list of wallets to process, the progress-notification
threshold, and the post-100% monitoring cadence.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import aiosqlite

from aria_core.paths import aria_db_path

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())

# Progress notification every N cumulative covered tokens (15/07, explicit
# operator request) -- distinct from the per-pass cap
# (`WEIGHTS.max_tokens_analyzed`), even though the same value (50) was chosen
# for both at the time of writing (assumed coincidence, not a hardcoded
# coupling: the two constants live in different modules).
PROGRESS_NOTIFY_STEP = 50

# Wallets processed per heartbeat pass (sobriety -- avoid saturating external
# APIs in a single cycle if several wallets are queued). Lowered from 2 to 1
# on 15/07 (operator observation): ARIA's heartbeat processes its tasks in
# SEQUENCE, never in parallel -- a cycle with 2 wallets x 50 tokens x ~2.1s
# GeckoTerminal throttle can block ALL other enabled automations for up to
# ~50 minutes. At 1 wallet, the worst case drops to ~25 minutes. Operator
# explicitly not in a hurry -- prefers the safety margin on the rest of the
# heartbeat over the coverage speed of this particular cycle.
MAX_WALLETS_PER_CYCLE = 1

# Permanent tracking (15/07, follow-up 2, explicit operator decision): once
# full coverage is reached, a check once a week is enough -- nothing left to
# catch up on, just detecting possible new activity.
MONITORING_INTERVAL_DAYS = 7

# Inactivity threshold (15/07, explicit operator decision, "3 months")
# before stopping monitoring of a wallet -- measured on the real last
# on-chain activity (`WalletScoreCard.last_activity_at`), never on time
# spent in the queue.
INACTIVITY_CUTOFF_DAYS = 90


def wallet_scan_queue_enabled() -> bool:
    return os.environ.get("ARIA_WALLET_SCAN_QUEUE_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS wallet_scan_queue (
                wallet TEXT PRIMARY KEY,
                added_at TEXT NOT NULL,
                last_attempt_at TEXT,
                last_notified_milestone INTEGER NOT NULL DEFAULT 0,
                next_check_at TEXT,
                monitoring_since TEXT
            )
            """
        )
        # Idempotent hot migration (15/07, permanent tracking -- follow-up 2) --
        # a queue already populated before these columns never has
        # `next_check_at`: default = `added_at` (immediately due, unchanged
        # behavior for wallets already catching up). `monitoring_since` stays
        # NULL (catching up).
        cols = {row[1] for row in await (await db.execute("PRAGMA table_info(wallet_scan_queue)")).fetchall()}
        if "next_check_at" not in cols:
            await db.execute("ALTER TABLE wallet_scan_queue ADD COLUMN next_check_at TEXT")
            await db.execute("UPDATE wallet_scan_queue SET next_check_at = added_at WHERE next_check_at IS NULL")
        if "monitoring_since" not in cols:
            await db.execute("ALTER TABLE wallet_scan_queue ADD COLUMN monitoring_since TEXT")
        await db.commit()


@dataclass
class QueuedWallet:
    wallet: str
    added_at: datetime
    last_attempt_at: datetime | None
    last_notified_milestone: int
    next_check_at: datetime
    monitoring_since: datetime | None

    @property
    def is_monitoring(self) -> bool:
        return self.monitoring_since is not None


async def enqueue_wallets(addresses: list[str]) -> list[str]:
    """Adds addresses absent from the queue, immediately due (catch-up
    mode). Returns those actually added (duplicates already in the queue are
    silently ignored -- not an error, just nothing more to do, including for
    a wallet already in post-100% monitoring)."""
    await _ensure_table()
    now = datetime.now(timezone.utc).isoformat()
    added: list[str] = []
    async with aiosqlite.connect(DB_PATH) as db:
        for raw in addresses:
            wallet = raw.lower()
            cursor = await db.execute(
                "INSERT OR IGNORE INTO wallet_scan_queue (wallet, added_at, next_check_at) VALUES (?, ?, ?)",
                (wallet, now, now),
            )
            if cursor.rowcount:
                added.append(wallet)
        await db.commit()
    return added


async def queue_size() -> int:
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (await db.execute("SELECT COUNT(*) FROM wallet_scan_queue")).fetchone()
    return int(row[0]) if row else 0


async def queue_counts() -> dict:
    """Distinguishes wallets still in initial CATCH-UP from those in plain
    post-100% weekly MONITORING -- never say "remaining" about a wallet
    that's already fully covered (just monitored)."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (
            await db.execute(
                "SELECT "
                "SUM(CASE WHEN monitoring_since IS NULL THEN 1 ELSE 0 END), "
                "SUM(CASE WHEN monitoring_since IS NOT NULL THEN 1 ELSE 0 END) "
                "FROM wallet_scan_queue"
            )
        ).fetchone()
    return {"catching_up": row[0] or 0, "monitoring": row[1] or 0}


async def queue_status_summary() -> dict:
    """Verifiable truth about the queue's real progress (23/07, direct
    follow-up to #29 -- before this fix, no command let you know whether the
    queue was progressing or stuck without a manual SQL query).

    Explicitly distinguishes: never attempted at all (``last_attempt_at``
    empty) vs. already attempted at least once but not yet at 100% vs. in
    monitoring (100% already reached). ``oldest_never_attempted_days`` --
    how many days the oldest wallet ever touched has been waiting -- is the
    most direct signal of a real blockage (as opposed to plain normal
    slowness)."""
    await _ensure_table()
    now = datetime.now(timezone.utc)
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (
            await db.execute(
                "SELECT "
                "SUM(CASE WHEN last_attempt_at IS NULL THEN 1 ELSE 0 END), "
                "SUM(CASE WHEN last_attempt_at IS NOT NULL AND monitoring_since IS NULL THEN 1 ELSE 0 END), "
                "SUM(CASE WHEN monitoring_since IS NOT NULL THEN 1 ELSE 0 END), "
                "COUNT(*) "
                "FROM wallet_scan_queue"
            )
        ).fetchone()
        never_attempted, in_progress, monitoring, total = (
            row[0] or 0, row[1] or 0, row[2] or 0, row[3] or 0,
        )

        oldest_row = await (
            await db.execute(
                "SELECT wallet, added_at FROM wallet_scan_queue "
                "WHERE last_attempt_at IS NULL ORDER BY added_at ASC LIMIT 1"
            )
        ).fetchone()
        last_scored_row = await (
            await db.execute(
                "SELECT wallet, last_attempt_at FROM wallet_scan_queue "
                "WHERE last_attempt_at IS NOT NULL ORDER BY last_attempt_at DESC LIMIT 1"
            )
        ).fetchone()

    oldest_never_attempted_days = None
    if oldest_row:
        added = datetime.fromisoformat(oldest_row[1])
        oldest_never_attempted_days = (now - added).total_seconds() / 86400

    return {
        "total": total,
        "never_attempted": never_attempted,
        "in_progress": in_progress,
        "monitoring": monitoring,
        "oldest_never_attempted_wallet": oldest_row[0] if oldest_row else None,
        "oldest_never_attempted_days": oldest_never_attempted_days,
        "last_scored_wallet": last_scored_row[0] if last_scored_row else None,
        "last_scored_at": last_scored_row[1] if last_scored_row else None,
    }


async def list_pending(limit: int = MAX_WALLETS_PER_CYCLE) -> list[QueuedWallet]:
    """The DUE wallets (catch-up always immediately due, monitoring due
    weekly). Priority (21/07, operator request -- leaderboard capacity
    raised to 600): new candidates in CATCH-UP (`monitoring_since IS NULL`)
    ALWAYS go before plain weekly MONITORING rescans, regardless of their
    respective due date -- otherwise a large population already scored in
    monitoring (up to ~1 wallet/20min ~= 504 rescans/week of total capacity)
    could structurally starve the discovery of new candidates. Within each
    group, the longest overdue first (FIFO on `next_check_at`) -- never an
    arbitrary order."""
    await _ensure_table()
    now_iso = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        rows = await (
            await db.execute(
                "SELECT wallet, added_at, last_attempt_at, last_notified_milestone, "
                "next_check_at, monitoring_since FROM wallet_scan_queue "
                "WHERE next_check_at <= ? "
                "ORDER BY (monitoring_since IS NULL) DESC, next_check_at ASC LIMIT ?",
                (now_iso, limit),
            )
        ).fetchall()
    return [
        QueuedWallet(
            wallet=r[0],
            added_at=datetime.fromisoformat(r[1]),
            last_attempt_at=datetime.fromisoformat(r[2]) if r[2] else None,
            last_notified_milestone=r[3] or 0,
            next_check_at=datetime.fromisoformat(r[4]) if r[4] else datetime.fromisoformat(r[1]),
            monitoring_since=datetime.fromisoformat(r[5]) if r[5] else None,
        )
        for r in rows
    ]


async def mark_attempt(
    wallet: str,
    *,
    next_check_at: datetime,
    last_notified_milestone: int | None = None,
    monitoring_since: datetime | None = None,
) -> None:
    """Reschedules `wallet` -- `next_check_at` is ALWAYS updated (when this
    wallet should next be reviewed), the other two fields only if supplied."""
    await _ensure_table()
    now = datetime.now(timezone.utc).isoformat()
    fields = ["last_attempt_at=?", "next_check_at=?"]
    values: list = [now, next_check_at.isoformat()]
    if last_notified_milestone is not None:
        fields.append("last_notified_milestone=?")
        values.append(last_notified_milestone)
    if monitoring_since is not None:
        fields.append("monitoring_since=?")
        values.append(monitoring_since.isoformat())
    values.append(wallet.lower())
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE wallet_scan_queue SET {', '.join(fields)} WHERE wallet=?", values)
        await db.commit()


async def remove_from_queue(wallet: str) -> None:
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM wallet_scan_queue WHERE wallet=?", (wallet.lower(),))
        await db.commit()


async def _update_leaderboard_best_effort(wallet: str, card) -> None:
    """"Top investors" leaderboard (21/07) -- called ONLY on a wallet with
    full coverage (`full_coverage`), never on a partial score (same
    exclusion as `full_coverage=False` elsewhere in `smart_money.py` for the
    comparison population -- a score not yet reliable enough to compare is
    no more reliable for ranking). Best-effort: a leaderboard write failure
    must never break the scan cycle itself, proper gate checked inside the
    called function."""
    from aria_core.services import smart_money_leaderboard

    try:
        await smart_money_leaderboard.update_leaderboard(wallet, card.composite_percentile)
    except Exception:  # noqa: BLE001
        logger.warning("wallet_scan_queue: leaderboard update failed for %s", wallet)


async def _remove_from_leaderboard_best_effort(wallet: str, reason: str) -> None:
    """21/07 -- a wallet that drops out of monitoring (inactivity) must
    never keep its last score frozen indefinitely on the leaderboard,
    without ever being flagged as "no longer tracked". Best-effort, same
    doctrine as ``_update_leaderboard_best_effort``."""
    from aria_core.services import smart_money_leaderboard

    try:
        await smart_money_leaderboard.remove_and_archive(wallet, reason)
    except Exception:  # noqa: BLE001
        logger.warning("wallet_scan_queue: leaderboard removal failed for %s", wallet)


def _is_confirmed_underperformer(card) -> bool:
    """21/07, explicit operator request: a wallet whose percentile is
    genuinely MEASURED (never ``None`` -- a not-yet-comparable score must
    never be judged bad) and below the leaderboard eviction threshold is a
    confirmed bad investor -- no point continuing to rescan it every week
    forever."""
    from aria_core.services.smart_money_leaderboard import EVICTION_PERCENTILE_THRESHOLD

    return card.composite_percentile is not None and card.composite_percentile < EVICTION_PERCENTILE_THRESHOLD


async def _reject_wallet_permanently_best_effort(wallet: str, card) -> None:
    """21/07 -- a wallet confirmed as underperforming (measured percentile <
    eviction threshold) is marked PERMANENTLY rejected (``smart_money_leaderboard.
    mark_rejected``) IN ADDITION to being removed from the leaderboard --
    prevents any future rediscovery even if it reappears holding a new token
    (``discover_and_enqueue_candidates`` already filters out rejected
    wallets). Best-effort, same doctrine as the other leaderboard writes."""
    from aria_core.services import smart_money_leaderboard

    try:
        await smart_money_leaderboard.mark_rejected(
            wallet, card.composite_percentile, "percentile below 30 (confirmed at full coverage)",
        )
        await smart_money_leaderboard.remove_and_archive(wallet, "percentile below 30 (permanent rejection)")
    except Exception:  # noqa: BLE001
        logger.warning("wallet_scan_queue: permanent rejection failed for %s", wallet)


async def run_wallet_scan_queue_cycle(notifier=None) -> dict:
    """Advances each DUE wallet by one pass (up to
    `MAX_WALLETS_PER_CYCLE`):

    - Still catching up (`full_coverage=False`): notifies progress every
      `PROGRESS_NOTIFY_STEP` covered tokens, always due next cycle
      (`next_check_at=now`).
    - Reaches 100% for the FIRST time this pass: full final report, switches
      to weekly monitoring (`monitoring_since` set, NEVER removed from the
      queue again from here on -- permanent tracking, explicit operator
      decision of 15/07).
    - Already monitoring: if no real on-chain activity since
      `INACTIVITY_CUTOFF_DAYS`, monitoring stops (removed from the queue,
      notified). Otherwise rescheduled in `MONITORING_INTERVAL_DAYS`,
      notified ONLY if new activity was detected this pass (never a silent
      weekly noise with nothing new).

    Gate `ARIA_WALLET_SCAN_QUEUE_ENABLED` -- OFF by default, and has no
    effect anyway if the wallet evaluator itself
    (`ARIA_WALLET_SCORING_ENABLED`) is disabled (fail-closed, not a
    duplicate gate)."""
    from aria_core.services.geckoterminal import geckoterminal_client
    from aria_core.services.goplus import goplus_client
    from aria_core.services.smart_money import format_wallet_scoring_report, score_wallets, wallet_scoring_enabled

    if not wallet_scan_queue_enabled():
        return {"outcome": "skipped", "reason": "gate_off"}
    if not wallet_scoring_enabled():
        return {"outcome": "skipped", "reason": "wallet_scoring_disabled"}

    from aria_core import outgoing_pause

    if outgoing_pause.is_paused():
        return {"outcome": "skipped", "reason": "paused"}

    pending = await list_pending()
    if not pending:
        return {"outcome": "empty_queue"}

    processed: list[str] = []
    completed_first_time: list[str] = []
    dropped_inactive: list[str] = []
    rejected_wallets: list[str] = []
    now = datetime.now(timezone.utc)

    for queued in pending:
        report = await score_wallets([queued.wallet], gecko=geckoterminal_client, goplus=goplus_client)
        if not report.available or not report.wallets:
            await mark_attempt(queued.wallet, next_check_at=now)
            continue

        card = report.wallets[0]
        processed.append(queued.wallet)

        if not card.full_coverage:
            new_milestone = (card.tokens_scanned_cumulative // PROGRESS_NOTIFY_STEP) * PROGRESS_NOTIFY_STEP
            if new_milestone > queued.last_notified_milestone:
                await mark_attempt(queued.wallet, next_check_at=now, last_notified_milestone=new_milestone)
                if notifier is not None:
                    counts = await queue_counts()
                    await notifier(
                        f"📊 Scan en arrière-plan -- {queued.wallet}\n"
                        f"{card.tokens_scanned_cumulative}/{card.tokens_found} tokens couverts.\n"
                        f"File d'attente : {counts['catching_up']} en rattrapage, "
                        f"{counts['monitoring']} en surveillance."
                    )
            else:
                await mark_attempt(queued.wallet, next_check_at=now)
            continue

        if not queued.is_monitoring:
            if _is_confirmed_underperformer(card):
                # Measured percentile confirmed bad right from the 1st full
                # coverage -- removed ENTIRELY (never permanent monitoring)
                # and rejected forever, not just evicted from the
                # leaderboard (21/07, explicit operator request).
                await remove_from_queue(queued.wallet)
                await _reject_wallet_permanently_best_effort(queued.wallet, card)
                rejected_wallets.append(queued.wallet)
                if notifier is not None:
                    await notifier(
                        f"🚫 {queued.wallet} confirmé sous-performant (percentile "
                        f"{card.composite_percentile:.0f}e) -- retiré définitivement, "
                        "ne sera plus jamais re-scanné ni redécouvert."
                    )
                continue

            # First time this wallet reaches 100% -- full report, switches
            # to permanent monitoring (never removed here again).
            completed_first_time.append(queued.wallet)
            await mark_attempt(
                queued.wallet,
                next_check_at=now + timedelta(days=MONITORING_INTERVAL_DAYS),
                monitoring_since=now,
            )
            await _update_leaderboard_best_effort(queued.wallet, card)
            if notifier is not None:
                await notifier(
                    "✅ Scan en arrière-plan terminé (couverture complète) -- "
                    f"surveillance hebdomadaire activée\n{format_wallet_scoring_report(report)}"
                )
            continue

        # Already in permanent monitoring -- check inactivity before
        # rescheduling (never before, never based on time spent in queue).
        if (
            card.last_activity_at is not None
            and (now - card.last_activity_at) > timedelta(days=INACTIVITY_CUTOFF_DAYS)
        ):
            await remove_from_queue(queued.wallet)
            dropped_inactive.append(queued.wallet)
            await _remove_from_leaderboard_best_effort(
                queued.wallet, f"inactive wallet (>{INACTIVITY_CUTOFF_DAYS}d without on-chain activity)",
            )
            if notifier is not None:
                await notifier(
                    f"💤 Surveillance arrêtée -- {queued.wallet} inactif depuis plus de "
                    f"{INACTIVITY_CUTOFF_DAYS} jours (aucune activité on-chain détectée)."
                )
            continue

        if _is_confirmed_underperformer(card):
            # A wallet already in monitoring can degrade over time -- same
            # handling as the 1st full coverage: removed ENTIRELY and
            # rejected forever, not just evicted from the leaderboard
            # (21/07, explicit operator request).
            await remove_from_queue(queued.wallet)
            await _reject_wallet_permanently_best_effort(queued.wallet, card)
            rejected_wallets.append(queued.wallet)
            if notifier is not None:
                await notifier(
                    f"🚫 {queued.wallet} confirmé sous-performant (percentile "
                    f"{card.composite_percentile:.0f}e) -- retiré définitivement de la "
                    "surveillance, ne sera plus jamais re-scanné ni redécouvert."
                )
            continue

        next_check = now + timedelta(days=MONITORING_INTERVAL_DAYS)
        await mark_attempt(queued.wallet, next_check_at=next_check)
        await _update_leaderboard_best_effort(queued.wallet, card)
        if card.tokens_analyzed > 0 and notifier is not None:
            # New activity spotted during monitoring -- never a silent
            # weekly noise if nothing new was found.
            await notifier(
                f"🔄 Nouvelle activité détectée en surveillance -- {queued.wallet} "
                f"({card.tokens_analyzed} nouveau(x) token(s) couvert(s)). "
                f"Prochaine vérification dans {MONITORING_INTERVAL_DAYS}j."
            )

    return {
        "outcome": "ok",
        "processed": processed,
        "completed_first_time": completed_first_time,
        "dropped_inactive": dropped_inactive,
        "rejected_wallets": rejected_wallets,
    }
