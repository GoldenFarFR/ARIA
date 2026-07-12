"""LanceDB store — Phase C (embedded local, opt-in aria_vector_memory).

Remplace ``chroma_store.py`` 1:1 — même surface (``store``/``search``/``is_available``/
``vector_store_status``), même sémantique. Le texte reste l'entrée : l'embedding
(``embedding.embed_text``) est calculé ici, l'appelant ne voit jamais de vecteur.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any
from uuid import uuid4

from aria_core.memory.vector._flags import is_vector_enabled
from aria_core.memory.vector.embedding import embed_text
from aria_core.memory.vector.lancedb_client import get_table, lancedb_installed
from aria_core.memory.vector.schema_validator import (
    load_schema,
    normalize_metadata,
    validate_entry,
)
from aria_core.paths import vector_dir

logger = logging.getLogger(__name__)

_ENTRY_TYPE_RE = re.compile(r"^[a-z0-9_]+$")


def is_available() -> bool:
    """Vector store utilisable (flag + lancedb/fastembed + table OK)."""
    if not is_vector_enabled() or not lancedb_installed():
        return False
    return get_table() is not None


def vector_store_status() -> dict[str, Any]:
    schema = load_schema()
    installed = lancedb_installed()
    enabled = is_vector_enabled()
    available = is_available()
    count = 0
    if available:
        try:
            tbl = get_table()
            if tbl is not None:
                count = int(tbl.count_rows())
        except Exception as exc:
            logger.debug("lancedb count: %s", exc)
    return {
        "enabled": enabled,
        "available": available,
        "installed": installed,
        "backend": "lancedb",
        "persist_dir": str(vector_dir()),
        "collection_count": count,
        "entry_types": list((schema.get("entry_types") or {}).keys()),
    }


def _distance_metric() -> str:
    schema = load_schema()
    return str((schema.get("collection") or {}).get("distance") or "cosine")


async def store(
    entry_type: str,
    content: str,
    *,
    metadata: dict[str, Any] | None = None,
) -> str | None:
    """Persiste un document — no-op si flag off ou lancedb absent."""
    if not is_available():
        return None
    text = (content or "").strip()
    if not text:
        return None
    ok, err = validate_entry(entry_type, metadata)
    if not ok:
        logger.warning("lancedb store rejected: %s", err)
        return None
    tbl = get_table()
    if tbl is None:
        return None
    meta = normalize_metadata(entry_type, metadata)
    doc_id = str((metadata or {}).get("source_id") or uuid4())[:36]
    try:
        text = text[:8000]
        row = {
            "id": doc_id,
            "vector": embed_text(text),
            "text": text,
            "entry_type": entry_type,
            "metadata_json": json.dumps(meta),
        }
        tbl.merge_insert("id").when_matched_update_all().when_not_matched_insert_all().execute(
            [row]
        )
        return doc_id
    except Exception as exc:
        logger.warning("lancedb store failed: %s", exc)
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
    tbl = get_table()
    if tbl is None:
        return []
    try:
        search_q = tbl.search(embed_text(q[:2000]), vector_column_name="vector").metric(
            _distance_metric()
        )
        if entry_type:
            if _ENTRY_TYPE_RE.match(entry_type):
                search_q = search_q.where(f"entry_type = '{entry_type}'", prefilter=True)
            else:
                logger.warning("lancedb search: entry_type invalide ignoré: %r", entry_type)
        rows = search_q.limit(max(1, min(limit, 20))).to_list()
        return [
            {
                "id": row.get("id"),
                "content": row.get("text") or "",
                "metadata": json.loads(row.get("metadata_json") or "{}"),
                "distance": row.get("_distance"),
            }
            for row in rows
        ]
    except Exception as exc:
        logger.warning("lancedb search failed: %s", exc)
        return []
