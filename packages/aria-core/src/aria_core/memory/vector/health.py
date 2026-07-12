"""Santé mémoire vectorielle — Phase 2 prep (diagnostic opt-in)."""
from __future__ import annotations

from typing import Any

from aria_core.memory.vector import is_vector_enabled, vector_store_status
from aria_core.memory.vector.lancedb_client import get_table, lancedb_installed
from aria_core.paths import vector_dir


async def vector_health_report() -> dict[str, Any]:
    """Rapport diagnostic — safe si LanceDB absent ou flag off."""
    report: dict[str, Any] = {
        "flag_enabled": is_vector_enabled(),
        "vector_backend_installed": lancedb_installed(),
        "persist_dir": str(vector_dir()),
        "status": vector_store_status(),
    }
    if not is_vector_enabled():
        report["ready"] = False
        report["reason"] = "aria_vector_memory=false"
        return report
    if not lancedb_installed():
        report["ready"] = False
        report["reason"] = "lancedb not installed — pip install -e '.[vector]'"
        return report
    tbl = get_table()
    if tbl is None:
        report["ready"] = False
        report["reason"] = "table init failed"
        return report
    try:
        count = int(tbl.count_rows())
    except Exception as exc:
        report["ready"] = False
        report["reason"] = f"count failed: {exc}"
        return report
    report["ready"] = True
    report["document_count"] = count
    report["reason"] = "ok"
    return report