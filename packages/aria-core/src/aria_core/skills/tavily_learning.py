"""Auto-formation continue d'ARIA via Tavily -- 22/07.

Comble un vrai trou : ``curiosity.py::run_curiosity_cycle`` (le pipeline
existant fetch -> triage Groq -> pending -> approbation Telegram -> ingestion
LanceDB) dépend de l'API X officielle (``x_bearer_token``/``x_api_key``),
volontairement coupée depuis début juillet (coût pay-per-use, cf. CLAUDE.md).
Ce module ajoute une SOURCE alternative -- Tavily (déjà payé, budget mensuel
partagé) -- pour DEUX types de contenu que l'API X ne couvrait de toute façon
pas complètement :

1. Comptes X déjà suivis (``x_watchlist.py``, watchlist EXISTANTE, rien
   dupliqué) -- via ``include_domains=["twitter.com","x.com"]`` (vérifié en
   conditions réelles, 22/07).
2. Sujets d'auto-formation générale -- macro-économie (prioritaire),
   psychologie de trading, documentation/outils (``learning_topics.yaml``,
   NOUVEAU -- l'API X n'a jamais couvert ça, ce n'est pas un repli, c'est une
   vraie extension).

Réutilise INTÉGRALEMENT le reste du pipeline existant (triage, stockage
pending, approbation Telegram réutilisant le même tag ``learn_knowledge``,
ingestion LanceDB à l'approbation via ``cognitive.approve_knowledge`` ->
``ingest.ingest_approved_item``) -- seule la source change. Volontairement
UNE cycle DISTINCTE de ``x_curiosity`` (pas une modification de celle-ci) :
son gate est lié à la décision opérateur sur l'API X officielle (question
distincte, jamais présumée réactivée), ce nouveau gate lui est propre.

Cadence QUOTIDIENNE, budget BORNÉ : 1 compte X + 1 sujet par passage (2
recherches Tavily "basic" = 2 crédits/jour, ~60/mois sur un budget partagé de
900 -- large marge pour les appels ad-hoc existants, web_verify/
conviction_research). Round-robin PERSISTÉ (SQLite) : chaque compte/sujet de
la liste est couvert à tour de rôle sur plusieurs jours, jamais toujours le
même en tête.

Auto-curation de la liste de sujets par ARIA elle-même (proposition d'ajout,
même doctrine que knowledge_inbox.py) : DIFFÉRÉE, pas encore construite --
liste éditée manuellement pour l'instant (``learning_topics.yaml``).
"""
from __future__ import annotations

import logging
import os
from typing import Any

import aiosqlite

from aria_core.paths import aria_db_path

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())

# Par passage : 1 compte X + 1 sujet -- 2 recherches "basic" (1 crédit
# chacune) au maximum. Volontairement petit et fixe (v1) -- cf. docstring
# module pour le calcul de marge sur le budget mensuel partagé.
_MAX_SNIPPETS_PER_SEARCH = 3
_MIN_SNIPPET_CHARS = 20


def tavily_learning_enabled() -> bool:
    from aria_core.services.tavily import is_tavily_configured

    if not is_tavily_configured():
        return False
    return os.environ.get("ARIA_TAVILY_LEARNING_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


async def _ensure_cursor_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "CREATE TABLE IF NOT EXISTS tavily_learning_cursor ("
            "list_name TEXT PRIMARY KEY, last_index INTEGER NOT NULL DEFAULT -1)"
        )
        await db.commit()


async def _next_item(list_name: str, items: list[Any]) -> Any | None:
    """Round-robin PERSISTÉ : avance le curseur d'une liste et boucle à la
    fin. ``None`` si la liste est vide (jamais une erreur -- une watchlist ou
    une liste de sujets momentanément vide ne casse jamais le cycle)."""
    if not items:
        return None
    await _ensure_cursor_table()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT last_index FROM tavily_learning_cursor WHERE list_name = ?",
            (list_name,),
        )
        row = await cursor.fetchone()
        last_index = row[0] if row else -1
        next_index = (last_index + 1) % len(items)
        await db.execute(
            "INSERT INTO tavily_learning_cursor (list_name, last_index) VALUES (?, ?) "
            "ON CONFLICT(list_name) DO UPDATE SET last_index = excluded.last_index",
            (list_name, next_index),
        )
        await db.commit()
    return items[next_index]


async def _triage_and_store_x(snippets, *, topic: str) -> int:
    from aria_core.knowledge.cognitive import add_knowledge
    from aria_core.knowledge.x_insight_relevance import assess_x_insight_for_memory

    stored = 0
    for text, _url, _published in snippets[:_MAX_SNIPPETS_PER_SEARCH]:
        if len(text) < _MIN_SNIPPET_CHARS:
            continue
        assessment = await assess_x_insight_for_memory(text, source="tavily_x")
        if not assessment.store:
            continue
        await add_knowledge(
            source="tavily_x",
            topic=topic,
            content=text[:500],
            confidence=assessment.confidence,
            approved=False,
        )
        stored += 1
    return stored


async def _triage_and_store_market(snippets, *, topic: str) -> int:
    from aria_core.knowledge.cognitive import add_knowledge
    from aria_core.knowledge.x_insight_relevance import assess_market_knowledge_for_memory

    stored = 0
    for text, _url, _published in snippets[:_MAX_SNIPPETS_PER_SEARCH]:
        if len(text) < _MIN_SNIPPET_CHARS:
            continue
        assessment = await assess_market_knowledge_for_memory(text, source="tavily_learning")
        if not assessment.store:
            continue
        await add_knowledge(
            source="tavily_learning",
            topic=topic,
            content=text[:500],
            confidence=assessment.confidence,
            approved=False,
        )
        stored += 1
    return stored


async def run_tavily_learning_cycle() -> dict:
    """Un passage : 1 compte X (watchlist existante) + 1 sujet
    (macro/psychologie/doc), round-robin persisté, triage Groq réutilisé,
    stockage pending + approbation Telegram réutilisés tels quels."""
    if not tavily_learning_enabled():
        return {"outcome": "skipped_disabled"}

    from aria_core.services import tavily_budget
    from aria_core.services.tavily import tavily_client

    # 2 recherches "basic" max ce passage (1 crédit chacune) -- vérifié
    # AVANT de commencer, jamais après coup (même doctrine que blockscout.py).
    if not await tavily_budget.can_spend(2 * tavily_budget.COST_BASIC):
        return {"outcome": "budget_exhausted"}

    from aria_core.knowledge.learning_topics import all_learning_topics
    from aria_core.knowledge.x_watchlist import all_curiosity_handles

    handles = list(all_curiosity_handles())
    topics = all_learning_topics()

    new_insights = 0
    picked: dict[str, str] = {}

    handle = await _next_item("x_handle", handles)
    if handle:
        picked["x_handle"] = handle
        try:
            result = await tavily_client.search(
                f"{handle} latest posts analysis",
                include_domains=["twitter.com", "x.com"],
                max_results=_MAX_SNIPPETS_PER_SEARCH,
                caller="tavily_learning",
            )
        except Exception as exc:  # noqa: BLE001 -- un échec réseau ne casse jamais le cycle
            logger.warning("tavily_learning: recherche X échouée pour %s: %s", handle, exc)
            result = None
        if result is not None and result.available:
            new_insights += await _triage_and_store_x(result.snippets, topic=f"@{handle}")

    topic_entry = await _next_item("topic", topics)
    if topic_entry:
        picked["topic"] = topic_entry["query"]
        try:
            result = await tavily_client.search(
                topic_entry["query"],
                max_results=_MAX_SNIPPETS_PER_SEARCH,
                caller="tavily_learning",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("tavily_learning: recherche sujet échouée (%s): %s", topic_entry["query"], exc)
            result = None
        if result is not None and result.available:
            new_insights += await _triage_and_store_market(
                result.snippets, topic=topic_entry["category"]
            )

    if new_insights > 0:
        from aria_core.gateway.telegram_bot import request_approval
        from aria_core.knowledge.cognitive import get_pending
        from aria_core.runtime import settings

        pending = await get_pending(limit=3)
        preview = "\n".join(f"- [{k.id}] {k.content[:120]}..." for k in pending)
        if settings.aria_autonomous:
            desc = f"Auto-formation Tavily : {new_insights} insight(s) intégrés (autonome)"
        else:
            desc = (
                f"ARIA a appris {new_insights} chose(s) via Tavily (auto-formation).\n\n"
                f"Aperçu :\n{preview}\n\n"
                f"Valider en mémoire ? Réponds : oui / non"
            )
        await request_approval("learn_knowledge", desc)

    return {"outcome": "ok", "insights": new_insights, "picked": picked}
