"""Mémoire vectorielle — stub Phase B (Chroma opt-in, désactivé par défaut)."""
from __future__ import annotations

from aria_core.memory.vector._flags import is_vector_enabled
from aria_core.memory.vector.chroma_store import (
    is_available,
    search,
    store,
    vector_store_status,
)
from aria_core.memory.vector.health import vector_health_report


__all__ = [
    "is_available",
    "is_vector_enabled",
    "search",
    "store",
    "vector_health_report",
    "vector_store_status",
]