"""Plafond de requêtes X pour la diligence de conviction du pipeline momentum (19/07).

Réactive la lecture X (coupée le 11/07 pour maîtrise du coût pay-per-use, cf. CLAUDE.md)
mais BORNÉE -- même doctrine que ``x402_budget.py`` : plafond dur, jamais dépassé,
semaine calendaire glissante (lundi 00:00 UTC), append-only.

Différence assumée avec x402_budget.py : celui-ci compte des REQUÊTES, pas des dollars.
Le coût exact par appel de lecture X dépend du palier d'abonnement réel de l'opérateur
(``x_publication_policy.py`` documente déjà un abonnement 5$/mois pour la PUBLICATION,
un poste distinct) -- jamais vérifié pour la LECTURE dans cette session, donc jamais
inventé ici. ``WEEKLY_REQUEST_CAP`` est un plafond conservateur, prudent par design ;
à ajuster une fois le palier réel de lecture connu, pas avant.

Ne compte QUE les appels X (``search_recent_tweets``/``fetch_user_recent_tweets``) --
jamais les appels Tavily (déjà un fournisseur/budget séparé, sans rapport avec la
coupure X du 11/07)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import aiosqlite

from aria_core.paths import aria_db_path

DB_PATH = str(aria_db_path())

WEEKLY_REQUEST_CAP = 100

_COLUMNS = ["id", "purpose", "contract", "status", "reason", "created_at"]


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS x_research_request_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                purpose TEXT NOT NULL,
                contract TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            )
            """
        )
        await db.commit()


def week_start(now: datetime | None = None) -> datetime:
    """Début de la semaine calendaire courante (lundi 00:00 UTC) -- même formule que
    x402_budget.week_start, jamais dupliquée en dérivant, réécrite ici volontairement
    car les deux modules restent structurellement séparés (portées différentes)."""
    ref = now if now is not None else datetime.now(timezone.utc)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    monday = ref - timedelta(days=ref.weekday())
    return monday.replace(hour=0, minute=0, second=0, microsecond=0)


async def used_this_week(now: datetime | None = None) -> int:
    """Compte les requêtes RÉELLEMENT effectuées (status='ok') depuis le début de la
    semaine calendaire. Les tentatives 'blocked' ne comptent jamais contre le plafond."""
    await _ensure_table()
    start = week_start(now).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (
            await db.execute(
                "SELECT COUNT(*) FROM x_research_request_log WHERE status = 'ok' AND created_at >= ?",
                (start,),
            )
        ).fetchone()
    return int(row[0]) if row else 0


async def remaining_budget(now: datetime | None = None) -> int:
    used = await used_this_week(now)
    return max(0, WEEKLY_REQUEST_CAP - used)


async def can_spend(now: datetime | None = None) -> bool:
    """Fail-closed : en cas de doute, on refuse plutôt que de risquer un dépassement."""
    remaining = await remaining_budget(now)
    return remaining > 0


async def record_request(*, purpose: str, contract: str = "", status: str, reason: str = "") -> None:
    """Journalise une tentative de requête X (``status`` in {"ok", "blocked"}) --
    jamais seulement les succès, un refus de plafond doit rester tracé."""
    await _ensure_table()
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO x_research_request_log (purpose, contract, status, reason, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (purpose, contract, status, reason, now),
        )
        await db.commit()


async def weekly_status(now: datetime | None = None) -> dict:
    used = await used_this_week(now)
    return {
        "cap_requests": WEEKLY_REQUEST_CAP,
        "used_requests": used,
        "remaining_requests": max(0, WEEKLY_REQUEST_CAP - used),
        "week_started_at": week_start(now).isoformat(),
    }
