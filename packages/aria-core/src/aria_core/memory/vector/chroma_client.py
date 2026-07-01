"""Client Chroma embedded — lazy init, désactivé si flag off ou chromadb absent."""
from __future__ import annotations

import logging
from typing import Any

from aria_core.memory.vector._flags import is_vector_enabled
from aria_core.memory.vector.schema_validator import collection_name, load_schema
from aria_core.paths import chroma_dir

logger = logging.getLogger(__name__)

_client: Any = None
_collection: Any = None


def chromadb_installed() -> bool:
    try:
        import chromadb  # noqa: F401

        return True
    except ImportError:
        return False


def reset_client_cache() -> None:
    """Tests uniquement — réinitialise le singleton."""
    global _client, _collection
    _client = None
    _collection = None


def get_collection():
    """Retourne la collection Chroma ou None (flag off / import manquant / erreur)."""
    global _client, _collection
    if not is_vector_enabled() or not chromadb_installed():
        return None
    if _collection is not None:
        return _collection
    try:
        import chromadb

        persist = str(chroma_dir())
        _client = chromadb.PersistentClient(path=persist)
        schema = load_schema()
        coll_cfg = schema.get("collection") or {}
        distance = str(coll_cfg.get("distance") or "cosine")
        _collection = _client.get_or_create_collection(
            name=collection_name(),
            metadata={"hnsw:space": distance},
        )
        return _collection
    except Exception as exc:
        logger.warning("chroma client init failed: %s", exc)
        _client = None
        _collection = None
        return None