"""Vector memory — Phase B stub (LanceDB opt-in, disabled by default)."""
from __future__ import annotations

from aria_core.memory.vector._flags import is_vector_enabled
from aria_core.memory.vector.lancedb_store import (
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