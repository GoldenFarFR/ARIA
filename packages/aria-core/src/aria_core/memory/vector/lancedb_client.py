"""Client LanceDB embarqué — lazy init, désactivé si flag off ou lancedb/fastembed absent.

Bibliothèque in-process (format Lance en colonnes) — aucun composant serveur/réseau
dans le paquet, contrairement à chromadb (cf. CVE-2026-45829, RCE non authentifiée
sur son serveur FastAPI, jamais corrigée). Remplace ``chroma_client.py`` 1:1.
"""
from __future__ import annotations

import logging
from typing import Any

from aria_core.memory.vector._flags import is_vector_enabled
from aria_core.memory.vector.embedding import EMBEDDING_DIM, embedding_installed
from aria_core.memory.vector.schema_validator import collection_name
from aria_core.paths import vector_dir

logger = logging.getLogger(__name__)

_client: Any = None
_table: Any = None


def lancedb_installed() -> bool:
    try:
        import lancedb  # noqa: F401
    except ImportError:
        return False
    return embedding_installed()


def reset_client_cache() -> None:
    """Tests uniquement — réinitialise le singleton."""
    global _client, _table
    _client = None
    _table = None


def _table_schema() -> Any:
    import pyarrow as pa

    return pa.schema(
        [
            pa.field("id", pa.string()),
            pa.field("vector", pa.list_(pa.float32(), EMBEDDING_DIM)),
            pa.field("text", pa.string()),
            pa.field("entry_type", pa.string()),
            pa.field("metadata_json", pa.string()),
        ]
    )


def get_table():
    """Retourne la table LanceDB ou None (flag off / import manquant / erreur)."""
    global _client, _table
    if not is_vector_enabled() or not lancedb_installed():
        return None
    if _table is not None:
        return _table
    try:
        import lancedb

        _client = lancedb.connect(str(vector_dir()))
        _table = _client.create_table(collection_name(), schema=_table_schema(), exist_ok=True)
        return _table
    except Exception as exc:
        logger.warning("lancedb client init failed: %s", exc)
        _client = None
        _table = None
        return None
