"""File d'attente de scan wallet en arrière-plan (15/07, suite directe de #157).

Constat opérateur : sur un wallet très actif (ex. 680 tokens tradés), même le
scan incrémental persistant (`wallet_scan_state.py`) exige des dizaines de
rappels manuels de `/walletscore` pour atteindre la couverture complète --
impraticable en usage normal. Ce module permet d'INJECTER un wallet une seule
fois (commande `/walletqueue`) puis de laisser le heartbeat le faire avancer
tout seul, plusieurs tokens à la fois, jusqu'à couverture complète -- ARIA
notifie alors le résultat final sur Telegram sans action supplémentaire.

Suivi PERMANENT (15/07, suite 2 -- constat opérateur explicite) : un wallet qui
atteint 100% n'est plus jamais retiré de la file. Il bascule en mode
SURVEILLANCE (une vérification légère par semaine, `MONITORING_INTERVAL_DAYS`)
-- ARIA repère toute nouvelle activité (nouveaux tokens tradés) sans jamais
redemander une couverture complète (déjà acquise, `_needs_scan` ne reprend que
le neuf). Seule sortie : si le wallet ne montre plus AUCUNE activité on-chain
réelle depuis `INACTIVITY_CUTOFF_DAYS` (3 mois), la surveillance s'arrête et le
wallet est retiré -- jamais avant, jamais sur un simple seuil de temps passé
dans la file. Le seuil de score (retirer un wallet dont la note descend trop
bas) reste une décision opérateur différée, pas encore construit.

Rien de dupliqué : chaque passage réutilise `smart_money.score_wallets` +
`wallet_scan_state.py` tels quels (le moteur incrémental existant), cette
file d'attente ajoute uniquement la liste des wallets à traiter, le seuil de
notification de progression, et la cadence de surveillance post-100%.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

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

# Suivi permanent (15/07, suite 2, décision opérateur explicite) : une fois
# la couverture complète atteinte, une vérification par semaine suffit --
# plus rien à rattraper, juste détecter une éventuelle nouvelle activité.
MONITORING_INTERVAL_DAYS = 7

# Seuil d'inactivité (15/07, décision opérateur explicite, "3 mois") avant
# d'arrêter la surveillance d'un wallet -- mesuré sur la vraie dernière
# activité on-chain (`WalletScoreCard.last_activity_at`), jamais sur la durée
# passée dans la file.
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
        # Migration à chaud idempotente (15/07, suivi permanent -- suite 2) --
        # une file déjà peuplée avant ces colonnes n'a jamais `next_check_at` :
        # défaut = `added_at` (immédiatement dû, comportement inchangé pour les
        # wallets déjà en rattrapage). `monitoring_since` reste NULL (rattrapage).
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
    """Ajoute les adresses absentes de la file, immédiatement dues (mode
    rattrapage). Retourne celles réellement ajoutées (les doublons déjà en
    file sont silencieusement ignorés -- pas une erreur, juste rien à faire
    de plus, y compris pour un wallet déjà en surveillance post-100%)."""
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
    """Distingue les wallets encore en RATTRAPAGE initial de ceux en simple
    SURVEILLANCE hebdomadaire post-100% -- jamais dire "restant" sur un
    wallet déjà entièrement couvert (juste surveillé)."""
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


async def list_pending(limit: int = MAX_WALLETS_PER_CYCLE) -> list[QueuedWallet]:
    """Les wallets DUS (rattrapage toujours dû immédiatement, surveillance due
    chaque semaine), les plus anciennement dus d'abord (FIFO sur
    `next_check_at`) -- jamais un ordre arbitraire."""
    await _ensure_table()
    now_iso = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        rows = await (
            await db.execute(
                "SELECT wallet, added_at, last_attempt_at, last_notified_milestone, "
                "next_check_at, monitoring_since FROM wallet_scan_queue "
                "WHERE next_check_at <= ? ORDER BY next_check_at ASC LIMIT ?",
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
    """Reprogramme `wallet` -- `next_check_at` est TOUJOURS mis à jour (quand
    doit-on revoir ce wallet), les deux autres champs seulement si fournis."""
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


async def run_wallet_scan_queue_cycle(notifier=None) -> dict:
    """Fait avancer d'un passage chaque wallet DU (jusqu'à
    `MAX_WALLETS_PER_CYCLE`) :

    - Encore en rattrapage (`full_coverage=False`) : notifie une progression
      tous les `PROGRESS_NOTIFY_STEP` tokens couverts, toujours dû au prochain
      cycle (`next_check_at=now`).
    - Atteint 100% pour la PREMIÈRE fois cette passe : rapport final complet,
      bascule en surveillance hebdomadaire (`monitoring_since` posé, plus
      JAMAIS retiré de la file à partir d'ici -- suivi permanent, décision
      opérateur explicite du 15/07).
    - Déjà en surveillance : si aucune activité on-chain réelle depuis
      `INACTIVITY_CUTOFF_DAYS`, la surveillance s'arrête (retiré de la file,
      notifié). Sinon reprogrammé dans `MONITORING_INTERVAL_DAYS`, notifié
      SEULEMENT si une nouvelle activité a été détectée cette passe (jamais un
      bruit hebdomadaire silencieux sans rien de neuf).

    Gate `ARIA_WALLET_SCAN_QUEUE_ENABLED` -- OFF par défaut, et n'a de toute
    façon aucun effet si l'évaluateur wallet lui-même
    (`ARIA_WALLET_SCORING_ENABLED`) est désactivé (fail-closed, pas un doublon
    de gate)."""
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
            # Première fois que ce wallet atteint 100% -- rapport complet,
            # bascule en surveillance permanente (jamais plus retiré ici).
            completed_first_time.append(queued.wallet)
            await mark_attempt(
                queued.wallet,
                next_check_at=now + timedelta(days=MONITORING_INTERVAL_DAYS),
                monitoring_since=now,
            )
            if notifier is not None:
                await notifier(
                    "✅ Scan en arrière-plan terminé (couverture complète) -- "
                    f"surveillance hebdomadaire activée\n{format_wallet_scoring_report(report)}"
                )
            continue

        # Déjà en surveillance permanente -- vérifie l'inactivité avant de
        # reprogrammer (jamais avant, jamais sur la durée passée en file).
        if (
            card.last_activity_at is not None
            and (now - card.last_activity_at) > timedelta(days=INACTIVITY_CUTOFF_DAYS)
        ):
            await remove_from_queue(queued.wallet)
            dropped_inactive.append(queued.wallet)
            if notifier is not None:
                await notifier(
                    f"💤 Surveillance arrêtée -- {queued.wallet} inactif depuis plus de "
                    f"{INACTIVITY_CUTOFF_DAYS} jours (aucune activité on-chain détectée)."
                )
            continue

        next_check = now + timedelta(days=MONITORING_INTERVAL_DAYS)
        await mark_attempt(queued.wallet, next_check_at=next_check)
        if card.tokens_analyzed > 0 and notifier is not None:
            # Nouvelle activité repérée pendant la surveillance -- jamais un
            # bruit hebdomadaire silencieux si rien de neuf n'a été trouvé.
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
    }
