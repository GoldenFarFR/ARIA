"""Chroma store — Phase C (embedded local, opt-in aria_vector_memory)."""
from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

from aria_core.memory.vector._flags import is_vector_enabled
from aria_core.memory.vector.chroma_client import chromadb_installed, get_collection
from aria_core.memory.vector.schema_validator import (
    load_schema,
    normalize_metadata,
    validate_entry,
)
from aria_core.paths import chroma_dir

logger = logging.getLogger(__name__)


def is_available() -> bool:
    """Vector store utilisable (flag + chromadb + collection OK)."""
    if not is_vector_enabled() or not chromadb_installed():
        return False
    return get_collection() is not None


def vector_store_status() -> dict[str, Any]:
    schema = load_schema()
    installed = chromadb_installed()
    enabled = is_vector_enabled()
    available = is_available()
    count = 0
    if available:
        try:
            coll = get_collection()
            if coll is not None:
                count = int(coll.count())
        except Exception as exc:
            logger.debug("chroma count: %s", exc)
    return {
        "enabled": enabled,
        "available": available,
        "installed": installed,
        "backend": "chroma",
        "persist_dir": str(chroma_dir()),
        "collection_count": count,
        "entry_types": list((schema.get("entry_types") or {}).keys()),
    }


def _format_hits(results: dict[str, Any]) -> list[dict[str, Any]]:
    ids = (results.get("ids") or [[]])[0]
    docs = (results.get("documents") or [[]])[0]
    metas = (results.get("metadatas") or [[]])[0]
    dists = (results.get("distances") or [[]])[0]
    out: list[dict[str, Any]] = []
    for i, doc_id in enumerate(ids):
        hit: dict[str, Any] = {
            "id": doc_id,
            "content": docs[i] if i < len(docs) else "",
            "metadata": metas[i] if i < len(metas) else {},
        }
        if i < len(dists):
            hit["distance"] = dists[i]
        out.append(hit)
    return out


async def store(
    entry_type: str,
    content: str,
    *,
    metadata: dict[str, Any] | None = None,
) -> str | None:
    """Persiste un document — no-op si flag off ou chromadb absent."""
    if not is_available():
        return None
    text = (content or "").strip()
    if not text:
        return None
    ok, err = validate_entry(entry_type, metadata)
    if not ok:
        logger.warning("chroma store rejected: %s", err)
        return None
    coll = get_collection()
    if coll is None:
        return None
    meta = normalize_metadata(entry_type, metadata)
    doc_id = str((metadata or {}).get("source_id") or uuid4())[:36]
    try:
        coll.upsert(
            ids=[doc_id],
            documents=[text[:8000]],
            metadatas=[meta],
        )
        return doc_id
    except Exception as exc:
        logger.warning("chroma store failed: %s", exc)
        return None


async def search(
    query: str,
    *,
    entry_type: str | None = None,
    limit: int = 8,
) -> list[dict[str, Any]]:
    """Recherche sémantique — [] si désactivé."""
    if not is_available():
        return []
    q = (query or "").strip()
    if not q:
        return []
    coll = get_collection()
    if coll is None:
        return []
    where = {"entry_type": entry_type} if entry_type else None
    try:
        results = coll.query(
            query_texts=[q[:2000]],
            n_results=max(1, min(limit, 20)),
            where=where,
        )
        return _format_hits(results)
    except Exception as exc:
        logger.warning("chroma search failed: %s", exc)
        return []