"""Suivi du budget de crédits Blockscout Pro (palier GRATUIT authentifié) — 22/07.

Distinct de ``x402_budget.py`` : celui-ci plafonne des micropaiements réels en
dollars (5$/semaine) ; le forfait Blockscout Pro classique (celui qui a produit
le "Out of credits" du 22/07) est GRATUIT — 0$, aucune carte bancaire, mais
plafonné en CRÉDITS/JOUR par le fournisseur. Deux unités différentes, deux
mécanismes de suivi différents, jamais à confondre.

Forfait sourcé (vérifié via la doc officielle Blockscout, 22/07, pas un chiffre
supposé) : palier gratuit authentifié = 100 000 crédits/jour, 5 req/s. La
plupart des endpoints coûtent 20 crédits/appel (seuls deux endpoints identifiés
comme "lourds" -- résumé/trace de transaction, ~50 crédits -- ne sont PAS
utilisés par ``blockscout.py`` à ce jour). Doctrine "90% de la capacité réelle"
déjà en place ailleurs (CLAUDE.md, 21/07) : plafond dur fixé à 90 000/jour.

Même patron que ``x402_budget.py`` : fenêtre CALENDAIRE (minuit UTC, pas un
cumul glissant depuis toujours), append-only (aucune fonction UPDATE/DELETE),
fail-closed (en cas de doute sur le solde déjà consommé, on refuse plutôt que
de risquer un dépassement).

Usage attendu (``blockscout.py``) : PROACTIF, pas réactif -- vérifier
``can_spend()`` AVANT de tenter un appel Pro, pas seulement après un 402 déjà
reçu (le repli réactif sur 402 existe déjà et reste le filet de sécurité final
si ce budget se révélait mal calibré).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import aiosqlite

from aria_core.paths import aria_db_path

DB_PATH = str(aria_db_path())

# Sourcé (22/07) : blog.blockscout.com, palier gratuit authentifié = 100 000
# crédits/jour. 90% de marge, même doctrine que les autres clients calibrés
# le 21/07 (docs/api-rate-limit-calibration.md).
DAILY_CAP_CREDITS = 90_000

# Tarif standard confirmé par la doc officielle -- tous les endpoints utilisés
# par blockscout.py (adresses/tokens/holders/transferts/contrats) sont dans
# cette catégorie, aucun des deux endpoints "lourds" (résumé/trace de
# transaction, ~50 crédits) n'est appelé par notre client à ce jour.
DEFAULT_COST_PER_CALL = 20

_COLUMNS = ["id", "endpoint", "credits", "created_at"]


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS blockscout_credit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                endpoint TEXT NOT NULL DEFAULT '',
                credits INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        await db.commit()


def day_start(now: datetime | None = None) -> datetime:
    """Début du jour calendaire courant (00:00 UTC)."""
    ref = now if now is not None else datetime.now(timezone.utc)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    return ref.replace(hour=0, minute=0, second=0, microsecond=0)


async def spent_today(now: datetime | None = None) -> int:
    """Somme des crédits réellement consommés (appels Pro RÉUSSIS uniquement --
    un appel refusé/en échec ne débite jamais de crédits côté fournisseur)
    depuis minuit UTC."""
    await _ensure_table()
    start = day_start(now).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (
            await db.execute(
                "SELECT COALESCE(SUM(credits), 0) FROM blockscout_credit_log "
                "WHERE created_at >= ?",
                (start,),
            )
        ).fetchone()
    return int(row[0]) if row else 0


async def remaining_budget(now: datetime | None = None) -> int:
    spent = await spent_today(now)
    return max(0, DAILY_CAP_CREDITS - spent)


async def can_spend(credits: int = DEFAULT_COST_PER_CALL, now: datetime | None = None) -> bool:
    """Fail-closed : un montant non-positif est toujours refusé, et si le solde
    restant ne couvre pas le montant demandé, on refuse plutôt que d'approcher
    le plafond au plus près (laisse de la marge pour un appel concurrent déjà
    en vol au moment de la vérification)."""
    if credits <= 0:
        return False
    remaining = await remaining_budget(now)
    return credits <= remaining


async def record_spend(*, endpoint: str = "", credits: int = DEFAULT_COST_PER_CALL) -> None:
    """N'enregistrer QUE les appels Pro réellement réussis (200 OK) -- un appel
    qui échoue (402/429/5xx/timeout) n'a jamais consommé de crédit réel côté
    Blockscout, l'enregistrer serait fabriquer une donnée."""
    await _ensure_table()
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO blockscout_credit_log (endpoint, credits, created_at) VALUES (?, ?, ?)",
            (endpoint, credits, now),
        )
        await db.commit()


async def daily_status(now: datetime | None = None) -> dict:
    """Diagnostic lisible, même doctrine que ``x402_budget.weekly_status``."""
    spent = await spent_today(now)
    return {
        "cap_credits": DAILY_CAP_CREDITS,
        "spent_credits": spent,
        "remaining_credits": max(0, DAILY_CAP_CREDITS - spent),
        "day_started_at": day_start(now).isoformat(),
    }
