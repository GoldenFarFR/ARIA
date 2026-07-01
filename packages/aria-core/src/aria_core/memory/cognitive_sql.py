"""Mémoire cognitive SQLite — wrapper fin autour de ``aria_core.knowledge.cognitive``."""
from __future__ import annotations

from aria_core.knowledge import cognitive as _legacy

KnowledgeItem = _legacy.KnowledgeItem

add_knowledge = _legacy.add_knowledge
approve_knowledge = _legacy.approve_knowledge
get_approved_since = _legacy.get_approved_since
count_approved_since = _legacy.count_approved_since
get_approved = _legacy.get_approved
get_pending = _legacy.get_pending
get_knowledge_by_id = _legacy.get_knowledge_by_id
upsert_knowledge_by_topic = _legacy.upsert_knowledge_by_topic
purge_placeholder_insights = _legacy.purge_placeholder_insights
build_context_summary = _legacy.build_context_summary