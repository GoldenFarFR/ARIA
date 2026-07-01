"""Santé mémoire vectorielle — Phase 2 prep (diagnostic opt-in)."""
from __future__ import annotations

from typing import Any

from aria_core.memory.vector import is_vector_enabled, vector_store_status
from aria_core.memory.vector.chroma_client import chromadb_installed, get_collection
from aria_core.paths import chroma_dir


async def vector_health_report() -> dict[str, Any]:
    """Rapport diagnostic — safe si Chroma absent ou flag off."""
    report: dict[str, Any] = {
        "flag_enabled": is_vector_enabled(),
        "chromadb_installed": chromadb_installed(),
        "persist_dir": str(chroma_dir()),
        "status": vector_store_status(),
    }
    if not is_vector_enabled():
        report["ready"] = False
        report["reason"] = "aria_vector_memory=false"
        return report
    if not chromadb_installed():
        report["ready"] = False
        report["reason"] = "chromadb not installed — pip install -e '.[vector]'"
        return report
    coll = get_collection()
    if coll is None:
        report["ready"] = False
        report["reason"] = "collection init failed"
        return report
    try:
        count = int(coll.count())
    except Exception as exc:
        report["ready"] = False
        report["reason"] = f"count failed: {exc}"
        return report
    report["ready"] = True
    report["document_count"] = count
    report["reason"] = "ok"
    return report