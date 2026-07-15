"""File d'attente de scan wallet en arrière-plan (15/07, suite directe de #157).

Constat opérateur : sur un wallet très actif (ex. 680 tokens tradés), même le
scan incrémental persistant (`wallet_scan_state.py`) exige des dizaines de
rappels manuels de `/walletscore` pour atteindre la couverture complète --
impraticable en usage normal. Ce module permet d'INJECTER un wallet une seule
fois (commande `/walletqueue`) puis de laisser le heartbeat le faire avancer
tout seul, plusieurs tokens à la fois, jusqu'à couverture complète -- ARIA
notifie alors le résultat final sur Telegram sans action supplémentaire.

Rien de dupliqué : chaque passage réutilise `smart_money.score_wallets` +
`wallet_scan_state.py` tels quels (le moteur incrémental existant), cette
file d'attente ajoute uniquement la liste des wallets à traiter et le seuil
de notification de progression.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone

import aiosqlite

from aria_core.paths import aria_db_path

DB_PATH = str(aria_db_path())

# Notification de progression tous les N tokens couverts cumulés (15/07,
# demande opérateur explicite) -- distinct du plafond par passage
# (`WEIGHTS.max_tokens_analyzed`), même si la même valeur (50) a été choisie
# pour les deux au moment de l'écriture (coïncidence assumée, pas un couplage
# en dur : les deux constantes vivent dans des modules différents).
PROGRESS_NOTIFY_STEP = 50

# Wallets traités par passage de heartbeat (sobriété -- éviter de saturer les
# API externes en un seul cycle si plusieurs wallets sont en file). Ramené de
# 2 à 1 le 15/07 (constat opérateur) : le heartbeat d'ARIA traite ses tâches
# en SÉQUENCE, jamais en parallèle -- un cycle à 2 wallets x 50 tokens x ~2,1s
# de throttle GeckoTerminal peut bloquer TOUTES les autres automatisations
# activées jusqu'à ~50 minutes. À 1 wallet, le pire cas tombe à ~25 minutes.
# Opérateur explicitement pas pressé -- préfère la marge de sécurité sur le
# reste du heartbeat à la vitesse de couverture de ce cycle précis.
MAX_WALLETS_PER_CYCLE = 1


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
                last_notified_milestone INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        await db.commit()


@dataclass
class QueuedWallet:
    wallet: str
    added_at: datetime
    last_attempt_at: datetime | None
    last_notified_milestone: int


async def enqueue_wallets(addresses: list[str]) -> list[str]:
    """Ajoute les adresses absentes de la file. Retourne celles réellement
    ajoutées (les doublons déjà en file sont silencieusement ignorés -- pas
    une erreur, juste rien à faire de plus)."""
    await _ensure_table()
    now = datetime.now(timezone.utc).isoformat()
    added: list[str] = []
    async with aiosqlite.connect(DB_PATH) as db:
        for raw in addresses:
            wallet = raw.lower()
            cursor = await db.execute(
                "INSERT OR IGNORE INTO wallet_scan_queue (wallet, added_at) VALUES (?, ?)",
                (wallet, now),
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


async def list_pending(limit: int = MAX_WALLETS_PER_CYCLE) -> list[QueuedWallet]:
    """Les wallets les plus anciennement ajoutés d'abord (FIFO) -- jamais un
    ordre arbitraire, pour qu'un wallet injecté en premier ne reste jamais
    indéfiniment derrière des arrivées plus récentes."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        rows = await (
            await db.execute(
                "SELECT wallet, added_at, last_attempt_at, last_notified_milestone "
                "FROM wallet_scan_queue ORDER BY added_at ASC LIMIT ?",
                (limit,),
            )
        ).fetchall()
    return [
        QueuedWallet(
            wallet=r[0],
            added_at=datetime.fromisoformat(r[1]),
            last_attempt_at=datetime.fromisoformat(r[2]) if r[2] else None,
            last_notified_milestone=r[3] or 0,
        )
        for r in rows
    ]


async def mark_attempt(wallet: str, *, last_notified_milestone: int | None = None) -> None:
    await _ensure_table()
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        if last_notified_milestone is not None:
            await db.execute(
                "UPDATE wallet_scan_queue SET last_attempt_at=?, last_notified_milestone=? WHERE wallet=?",
                (now, last_notified_milestone, wallet.lower()),
            )
        else:
            await db.execute(
                "UPDATE wallet_scan_queue SET last_attempt_at=? WHERE wallet=?",
                (now, wallet.lower()),
            )
        await db.commit()


async def remove_from_queue(wallet: str) -> None:
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM wallet_scan_queue WHERE wallet=?", (wallet.lower(),))
        await db.commit()


async def run_wallet_scan_queue_cycle(notifier=None) -> dict:
    """Fait avancer d'un passage chaque wallet en tête de file (jusqu'à
    `MAX_WALLETS_PER_CYCLE`). Notifie une progression tous les
    `PROGRESS_NOTIFY_STEP` tokens couverts, et le rapport final complet dès la
    couverture complète (le wallet quitte alors la file). Gate
    `ARIA_WALLET_SCAN_QUEUE_ENABLED` -- OFF par défaut, et n'a de toute façon
    aucun effet si l'évaluateur wallet lui-même (`ARIA_WALLET_SCORING_ENABLED`)
    est désactivé (fail-closed, pas un doublon de gate)."""
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
    completed: list[str] = []
    for queued in pending:
        report = await score_wallets([queued.wallet], gecko=geckoterminal_client, goplus=goplus_client)
        if not report.available or not report.wallets:
            await mark_attempt(queued.wallet)
            continue

        card = report.wallets[0]
        processed.append(queued.wallet)

        if card.full_coverage:
            await remove_from_queue(queued.wallet)
            completed.append(queued.wallet)
            remaining = await queue_size()
            if notifier is not None:
                await notifier(
                    "✅ Scan en arrière-plan terminé (couverture complète)\n"
                    + format_wallet_scoring_report(report)
                    + f"\n\nFile d'attente : {remaining} wallet(s) restant(s)."
                )
            continue

        new_milestone = (card.tokens_scanned_cumulative // PROGRESS_NOTIFY_STEP) * PROGRESS_NOTIFY_STEP
        if new_milestone > queued.last_notified_milestone:
            await mark_attempt(queued.wallet, last_notified_milestone=new_milestone)
            remaining = await queue_size()
            if notifier is not None:
                await notifier(
                    f"📊 Scan en arrière-plan -- {queued.wallet}\n"
                    f"{card.tokens_scanned_cumulative}/{card.tokens_found} tokens couverts.\n"
                    f"File d'attente : {remaining} wallet(s) restant(s) (dont celui-ci)."
                )
        else:
            await mark_attempt(queued.wallet)

    return {"outcome": "ok", "processed": processed, "completed": completed}
