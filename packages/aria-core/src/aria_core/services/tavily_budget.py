"""Suivi du budget de crédits Tavily (palier "Researcher" gratuit) — 22/07.

Même famille que ``blockscout_credit_budget.py`` mais fenêtre MENSUELLE, pas
journalière -- structure réelle du forfait Tavily (vérifiée sur le dashboard
billing réel, 22/07, cf. ``docs/api-rate-limit-calibration.md``) : **1000
crédits/mois, "use it or lose it"** (aucun report au mois suivant), 1 crédit
par recherche "basic", 2 par recherche "advanced". Aucun rate-limit en req/min
documenté nulle part pour ce fournisseur -- ce budget protège contre
l'ÉPUISEMENT du forfait mensuel, pas contre un 429 (cf. section "Deux familles
de contrainte" de la doc de calibration).

Doctrine "90% de la capacité réelle" (CLAUDE.md, 21/07) : plafond dur fixé à
900 (90% de 1000).

PARTAGÉ entre TOUS les appelants Tavily (``web_verify.fetch_web_snippets``
pour les questions factuelles opérateur/visiteur, et le futur cycle
d'auto-formation ``tavily_learning.py``) -- un seul point de coordination du
débit, jamais deux compteurs indépendants qui s'additionnent silencieusement
(même doctrine que le throttle GeckoTerminal partagé, incident du 21/07).
Câblé directement dans ``services/tavily.py::TavilyClient.search()``, jamais
dans chaque appelant individuellement.

Le log ``tavily_search_log`` sert un DOUBLE usage : (1) calcul du budget
consommé, (2) traçabilité -- l'opérateur peut voir QUOI a été recherché et
PAR QUI (``caller``), pas seulement combien de crédits ont été dépensés
(demande opérateur explicite, 22/07 : "il faudra aussi que je puisse savoir
sur quoi aria fait des recherche sur tavily").
"""
from __future__ import annotations

from datetime import datetime, timezone

import aiosqlite

from aria_core.paths import aria_db_path

DB_PATH = str(aria_db_path())

# Sourcé (22/07, dashboard billing réel Tavily, plan "Researcher") : 1000
# crédits/mois, use-it-or-lose-it. 90% de marge, doctrine CLAUDE.md.
MONTHLY_CAP_CREDITS = 900

# Sourcé (doc officielle Tavily) : recherche "basic" = 1 crédit, "advanced" = 2.
COST_BASIC = 1
COST_ADVANCED = 2


def cost_for_search(search_depth: str) -> int:
    """Coût réel en crédits pour cette profondeur de recherche."""
    return COST_ADVANCED if (search_depth or "").strip().lower() == "advanced" else COST_BASIC


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS tavily_search_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                caller TEXT NOT NULL DEFAULT '',
                query TEXT NOT NULL DEFAULT '',
                credits INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        await db.commit()


def month_start(now: datetime | None = None) -> datetime:
    """Début du mois calendaire courant (UTC) -- fenêtre "use it or lose it"
    du fournisseur, jamais un cumul glissant depuis toujours."""
    ref = now if now is not None else datetime.now(timezone.utc)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    return ref.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


async def spent_this_month(now: datetime | None = None) -> int:
    """Somme des crédits réellement consommés (recherches RÉUSSIES uniquement
    -- un échec ne débite jamais de crédit réel côté Tavily) depuis le début
    du mois calendaire courant."""
    await _ensure_table()
    start = month_start(now).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (
            await db.execute(
                "SELECT COALESCE(SUM(credits), 0) FROM tavily_search_log WHERE created_at >= ?",
                (start,),
            )
        ).fetchone()
    return int(row[0]) if row else 0


async def remaining_budget(now: datetime | None = None) -> int:
    spent = await spent_this_month(now)
    return max(0, MONTHLY_CAP_CREDITS - spent)


async def can_spend(credits: int = COST_BASIC, now: datetime | None = None) -> bool:
    """Fail-closed : un montant non-positif est toujours refusé ; si le solde
    restant ne couvre pas le montant demandé, on refuse plutôt que d'approcher
    le plafond au plus près."""
    if credits <= 0:
        return False
    remaining = await remaining_budget(now)
    return credits <= remaining


async def record_spend(*, caller: str = "", query: str = "", credits: int = COST_BASIC) -> None:
    """N'enregistrer QUE les recherches réellement réussies. ``query`` est
    tronquée (donnée opérationnelle d'ARIA elle-même, jamais de la PII
    utilisateur) -- sert la traçabilité, pas seulement le calcul du budget."""
    await _ensure_table()
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO tavily_search_log (caller, query, credits, created_at) VALUES (?, ?, ?, ?)",
            (caller[:60], query[:300], credits, now),
        )
        await db.commit()


async def monthly_status(now: datetime | None = None) -> dict:
    """Diagnostic lisible, même doctrine que ``blockscout_credit_budget.daily_status``."""
    spent = await spent_this_month(now)
    return {
        "cap_credits": MONTHLY_CAP_CREDITS,
        "spent_credits": spent,
        "remaining_credits": max(0, MONTHLY_CAP_CREDITS - spent),
        "month_started_at": month_start(now).isoformat(),
    }


async def recent_searches(limit: int = 20) -> list[dict]:
    """Traçabilité : les dernières recherches réellement exécutées (query
    tronquée, appelant, coût, horodatage) -- répond à "sur quoi ARIA
    fait-elle des recherches sur Tavily", pas seulement au budget consommé."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT caller, query, credits, created_at FROM tavily_search_log "
            "ORDER BY id DESC LIMIT ?",
            (max(1, min(limit, 200)),),
        )
        rows = await cursor.fetchall()
    return [
        {"caller": row[0], "query": row[1], "credits": row[2], "created_at": row[3]}
        for row in rows
    ]
