"""Embedded LanceDB client — lazy init, disabled if the flag is off or
lancedb/fastembed is missing.

In-process library (columnar Lance format) — no server/network component in
the package, unlike chromadb (cf. CVE-2026-45829, unauthenticated RCE on its
FastAPI server, never patched). Replaces ``chroma_client.py`` 1:1.
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
    """Tests only — resets the singleton."""
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
            pa.field("written_at", pa.string()),
            pa.field("written_by", pa.string()),
            pa.field("contract", pa.string()),
            pa.field("chain", pa.string()),
        ]
    )


# (14/08, #166) -- provenance columns added to an already-populated table
# (85 real rows found in prod, never dropped). LanceDB's create_table(...,
# exist_ok=True) only applies a schema at CREATION time -- it silently
# ignores the passed schema for a table that already exists on disk, so
# existing tables need an explicit, idempotent migration via add_columns().
# Verified empirically before writing this (copy of the real prod data,
# never the original): add_columns() on an already-present column raises
# ValueError, so the schema.names check below is required, not optional.
def _migrate_provenance_columns(table: Any) -> None:
    existing = set(table.schema.names)
    missing = {
        name: default
        for name, default in (
            ("written_at", "cast(NULL as string)"),
            ("written_by", "cast('' as string)"),
        )
        if name not in existing
    }
    if missing:
        table.add_columns(missing)


# (14/08, #170) -- ``contract``/``chain`` promoted from the free-form
# ``metadata_json`` blob to real typed columns, so a caller can filter with
# an exact SQL predicate (``where("contract = '...' AND chain = '...'")``)
# instead of a semantic vector search followed by a Python prefix-match on a
# parsed blob (the pattern ``conviction_research.py`` was using, fragile
# because vector search can miss/reorder results before the filter even
# runs). Same idempotent-migration pattern as ``_migrate_provenance_columns``
# above -- a second ``add_columns()`` call on an already-present column
# raises ``ValueError``, hence the ``schema.names`` check.
def _migrate_typed_columns(table: Any) -> None:
    existing = set(table.schema.names)
    missing = {
        name: "cast('' as string)" for name in ("contract", "chain") if name not in existing
    }
    if missing:
        table.add_columns(missing)


def get_table():
    """Returns the LanceDB table or None (flag off / missing import / error).

    14/08 -- real bug found in prod right after this same day's #166 migration
    landed: `create_table(name, schema=_table_schema(), exist_ok=True)` does
    NOT silently ignore the passed schema for an already-existing table, as a
    prior comment here assumed without ever testing it against the real
    pre-existing table (only `add_columns()` itself was verified empirically
    at the time) -- it raises `Schema Error: Provided schema does not match
    existing table schema` whenever the declared schema gains a field the
    on-disk table doesn't have yet (exactly what happened here: `written_at`/
    `written_by` added to `_table_schema()` while the real 85-row prod table
    still had the pre-#166 5-column schema). The exception was swallowed by
    the broad `except Exception` below, so `get_table()` silently returned
    `None` on every call -- `is_available()` stayed `False` and the whole
    vector store (read AND write) was dead in prod from that deploy onward,
    with zero visible error beyond a warning log line. Fix: `open_table()`
    never compares schemas, so try that FIRST for a table that already
    exists; only fall back to `create_table()` when it genuinely doesn't."""
    global _client, _table
    if not is_vector_enabled() or not lancedb_installed():
        return None
    if _table is not None:
        return _table
    try:
        import lancedb

        _client = lancedb.connect(str(vector_dir()))
        try:
            _table = _client.open_table(collection_name())
        except Exception:
            _table = _client.create_table(collection_name(), schema=_table_schema())
        _migrate_provenance_columns(_table)
        _migrate_typed_columns(_table)
        return _table
    except Exception as exc:
        logger.warning("lancedb client init failed: %s", exc)
        _client = None
        _table = None
        return None
