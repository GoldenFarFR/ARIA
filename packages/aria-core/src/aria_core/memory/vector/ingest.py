"""Ingestion vectorielle — miroir cognitive approuvée (flag-gated)."""
from __future__ import annotations

import logging

from aria_core.knowledge.cognitive import KnowledgeItem, get_knowledge_by_id
from aria_core.memory.vector._flags import is_vector_enabled
from aria_core.memory.vector.lancedb_store import store

logger = logging.getLogger(__name__)

_INSIGHT_SOURCES = frozenset({"x_twitter", "curiosity", "zhc_api", "x_setup"})
_LESSON_SOURCES = frozenset({"manual", "doctrine", "operator", "runbook"})


def entry_type_for_item(item: KnowledgeItem) -> str:
    src = (item.source or "").strip().lower()
    if src in _LESSON_SOURCES:
        return "lesson"
    if src in _INSIGHT_SOURCES:
        return "insight"
    return "lesson"


async def ingest_approved_item(item_id: str) -> str | None:
    """Indexe un item cognitive approuvé dans la mémoire vectorielle — no-op si flag off."""
    if not is_vector_enabled():
        return None
    item = await get_knowledge_by_id(item_id)
    if not item or not item.approved:
        return None
    entry_type = entry_type_for_item(item)
    metadata = {
        "source": item.source,
        "topic": item.topic,
        "source_id": f"cognitive:{item.id}",
        "confidence": str(item.confidence),
    }
    if entry_type == "reflection":
        metadata["context"] = item.topic
    doc_id = await store(entry_type, item.content, metadata=metadata)
    if doc_id:
        logger.debug("vector ingest cognitive:%s -> %s", item.id, doc_id)
    return doc_id