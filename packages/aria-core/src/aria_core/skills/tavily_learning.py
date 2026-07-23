"""ARIA's continuous self-learning via Tavily -- 07/22.

Fills a real gap: ``curiosity.py::run_curiosity_cycle`` (the existing
fetch -> Groq triage -> pending -> Telegram approval -> LanceDB ingestion
pipeline) depends on the official X API (``x_bearer_token``/``x_api_key``),
deliberately cut since early July (pay-per-use cost, cf. CLAUDE.md).
This module adds an alternative SOURCE -- Tavily (already paid for, shared
monthly budget) -- for TWO kinds of content that the X API never fully
covered anyway:

1. Already-tracked X accounts (``x_watchlist.py``, EXISTING watchlist, nothing
   duplicated) -- via ``include_domains=["twitter.com","x.com"]`` (verified under
   real conditions, 07/22).
2. General self-learning topics -- macro-economics (priority),
   trading psychology, documentation/tools (``learning_topics.yaml``,
   NEW -- the X API never covered this, this isn't a fallback, it's a
   real extension).

FULLY reuses the rest of the existing pipeline (triage, pending
storage, Telegram approval reusing the same ``learn_knowledge`` tag,
LanceDB ingestion on approval via ``cognitive.approve_knowledge`` ->
``ingest.ingest_approved_item``) -- only the source changes. Deliberately a
DISTINCT cycle from ``x_curiosity`` (not a modification of it): its gate
is tied to the operator's decision on the official X API (a separate
question, never presumed reactivated), this new gate is its own.

DAILY cadence, BOUNDED budget: 1 X account + 1 topic per pass (2
Tavily "basic" searches = 2 credits/day, ~60/month out of a shared budget of
900 -- ample margin for the existing ad-hoc calls, web_verify/
conviction_research). PERSISTED round-robin (SQLite): each account/topic in
the list is covered in turn over several days, never always the
same one first.

ARIA self-curating her own topic list (add proposal, same doctrine
as knowledge_inbox.py): DEFERRED, not yet built --
list manually edited for now (``learning_topics.yaml``).
"""
from __future__ import annotations

import logging
import os
from typing import Any

import aiosqlite

from aria_core.paths import aria_db_path

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())

# Per pass: 1 X account + 1 topic -- 2 "basic" searches (1 credit
# each) maximum. Deliberately small and fixed (v1) -- cf. module
# docstring for the margin calculation on the shared monthly budget.
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
    """PERSISTED round-robin: advances a list's cursor and loops back at the
    end. ``None`` if the list is empty (never an error -- a momentarily empty
    watchlist or topic list never breaks the cycle)."""
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
    """One pass: 1 X account (existing watchlist) + 1 topic
    (macro/psychology/docs), persisted round-robin, reused Groq triage,
    reused pending storage + Telegram approval as-is."""
    if not tavily_learning_enabled():
        return {"outcome": "skipped_disabled"}

    from aria_core.services import tavily_budget
    from aria_core.services.tavily import tavily_client

    # 2 "basic" searches max this pass (1 credit each) -- checked
    # BEFORE starting, never after the fact (same doctrine as blockscout.py).
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
        except Exception as exc:  # noqa: BLE001 -- a network failure never breaks the cycle
            logger.warning("tavily_learning: X search failed for %s: %s", handle, exc)
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
            logger.warning("tavily_learning: topic search failed (%s): %s", topic_entry["query"], exc)
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
