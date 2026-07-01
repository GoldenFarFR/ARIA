"""Façade mémoire ARIA — Phase B (package unifié, rétrocompatible).

``from aria_core.memory import append_memory`` continue de fonctionner.
Nouveau code : ``append``, ``get_approved``, ``is_vector_enabled``, etc.
"""
from __future__ import annotations

from aria_core.memory import _legacy_journal
from aria_core.memory.cognitive_sql import (
    KnowledgeItem,
    add_knowledge,
    approve_knowledge,
    build_context_summary,
    count_approved_since,
    get_approved,
    get_approved_since,
    get_pending,
    purge_placeholder_insights,
    upsert_knowledge_by_topic,
)
from aria_core.memory.journal import (
    append,
    build_llm_context,
    count_entries,
    get_doctrine_text,
    get_journal_summary,
    get_launchpad_doctrine_text,
    get_persona_text,
    read_recent,
)
from aria_core.memory.llm_context import fetch_vector_recall, sanitize_recall_text
from aria_core.memory.values import get_values_text, values_count
from aria_core.memory.vector import is_vector_enabled, vector_store_status
from aria_core.memory.vector.health import vector_health_report

# Rétrocompat — noms historiques (ex-aria_core/memory.py)
MEMORY_DIR = _legacy_journal.MEMORY_DIR
append_memory = _legacy_journal.append_memory
read_recent_memory = _legacy_journal.read_recent_memory
count_memory_entries = _legacy_journal.count_memory_entries

__all__ = [
    "MEMORY_DIR",
    "KnowledgeItem",
    "add_knowledge",
    "append",
    "append_memory",
    "approve_knowledge",
    "build_context_summary",
    "build_llm_context",
    "count_approved_since",
    "count_entries",
    "count_memory_entries",
    "fetch_vector_recall",
    "get_approved",
    "get_approved_since",
    "get_doctrine_text",
    "get_journal_summary",
    "get_launchpad_doctrine_text",
    "get_pending",
    "get_persona_text",
    "get_values_text",
    "is_vector_enabled",
    "values_count",
    "vector_health_report",
    "purge_placeholder_insights",
    "read_recent",
    "read_recent_memory",
    "sanitize_recall_text",
    "upsert_knowledge_by_topic",
    "vector_store_status",
]