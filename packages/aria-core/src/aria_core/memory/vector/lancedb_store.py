"""LanceDB store — Phase C (embedded local, opt-in aria_vector_memory).

Replaces ``chroma_store.py`` 1:1 — same surface (``store``/``search``/``is_available``/
``vector_store_status``), same semantics. Text remains the input: the embedding
(``embedding.embed_text``) is computed here, the caller never sees a vector.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from aria_core.memory.vector import audit
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
# Permissive enough for a hex EVM address or a base58 Solana address plus a
# plain chain identifier, strict enough to rule out SQL predicate injection
# via a value embedded in a where() clause (same doctrine as _ENTRY_TYPE_RE).
_EXACT_MATCH_VALUE_RE = re.compile(r"^[a-zA-Z0-9_\-]{1,100}$")

# Structural anti-injection guard (#206, 18/07 -- promoted research documenting the
# real risk of "memory poisoning" on AI agents' vector memory, $45M+ in cumulative
# 2026 incidents). Deliberately placed HERE (the lowest persistence layer), not in
# each caller: protects ALL content written to this memory, including a future
# caller that would forget to do its own triage (e.g. cybercentry_insight.py,
# which was already writing without any filter before this fix). PATTERN detection
# only (fast, no network call) -- doesn't replace a finer semantic judgment (see
# x_insight_relevance.py, which adds a dedicated LLM check for X insights, more
# exposed). Patterns chosen to be unlikely to false-positive on legitimate content
# (classic injection phrasings, FR+EN), not an exhaustive list.
_INJECTION_MARKERS_RE = re.compile(
    r"ignore\s+(all\s+|the\s+)?(previous|prior|above|earlier)\s+instructions"
    r"|disregard\s+(all\s+|your\s+|the\s+|any\s+)?(previous|prior|system)?\s*instructions"
    r"|forget\s+(everything|all)\s+(you|above|prior)"
    r"|new\s+instructions\s*:"
    r"|system\s*prompt\s*:"
    r"|you\s+are\s+now\s+(a|an)\s"
    r"|act\s+as\s+if\s+you\s+(have\s+no|are\s+not)"
    r"|\[?\s*system\s*\]?\s*:\s*(override|ignore|forget)"
    r"|ignor[e|ez]\s+(toutes\s+les\s+|les\s+)?instructions\s+(pr[ée]c[ée]dentes|ci-dessus)"
    r"|oublie\s+(toutes\s+les\s+instructions|tout\s+ce\s+qui\s+pr[ée]c[ée]de)",
    re.IGNORECASE,
)


def contains_injection_marker(text: str) -> bool:
    """Fast detection (regex, no network call) of classic prompt injection
    phrasings. Deliberately narrow (few false positives) -- catches the
    crudest attempts; the more subtle ones remain the responsibility of a
    possible upstream semantic check (e.g. x_insight_relevance.py)."""
    return bool(_INJECTION_MARKERS_RE.search(text or ""))


def is_available() -> bool:
    """Vector store usable (flag + lancedb/fastembed + table OK)."""
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
    """Persists a document — no-op if flag off or lancedb absent.

    Every accepted OR rejected attempt (injection marker, schema validation
    failure, internal error) is recorded in the write-audit trail
    (``audit.log_write_attempt``, #166, 14/08) -- never blocks the write
    itself, a broken audit log must never break a real store(). ``written_by``
    is captured automatically from the real call stack (never a caller
    parameter, see ``_caller_module_name``), ``written_at`` is a real
    timestamp -- both are structural columns, not part of the free-form
    ``metadata_json`` blob, so every future caller gets them for free."""
    if not is_available():
        return None
    # Provenance derived from the REAL call stack, never a parameter callers
    # pass themselves (a caller-supplied value could be forged) -- index 1 is
    # this function's own direct caller. "unknown" on any introspection
    # failure, never raises (#166, 14/08).
    try:
        written_by = Path(inspect.stack()[1].filename).name
    except Exception:
        written_by = "unknown"
    text = (content or "").strip()
    if not text:
        return None
    if contains_injection_marker(text):
        logger.warning("lancedb store rejected: injection marker detected")
        await audit.log_write_attempt(
            entry_type, written_by, accepted=False, reason="injection marker detected"
        )
        return None
    ok, err = validate_entry(entry_type, metadata)
    if not ok:
        logger.warning("lancedb store rejected: %s", err)
        await audit.log_write_attempt(entry_type, written_by, accepted=False, reason=err)
        return None
    tbl = get_table()
    if tbl is None:
        return None
    meta = normalize_metadata(entry_type, metadata)
    # (14/08, #170) -- real bug found investigating typed columns: naive
    # `str(source_id)[:36]` truncation cut INTO a source_id before ever
    # reaching its trailing date suffix (e.g. conviction_research.py's
    # "conviction-research-{chain}-{contract}-{date}", ~79 chars for a
    # typical EVM address) -- two different-dated recomputations of the SAME
    # contract+chain would collide on the SAME truncated id, so
    # merge_insert("id").when_matched_update_all() would silently overwrite
    # history instead of creating a new dated entry, contradicting this
    # module's own append-only intent. Verified empirically against the 81
    # real prod conviction_research rows before fixing: 0 collisions YET
    # (latent, not yet materialized -- no contract had been re-researched on
    # a different day). Hashing the FULL source_id preserves same-day
    # idempotence (same input -> same hash) while correctly distinguishing
    # different-day entries. uuid4()'s str() form is already exactly 36
    # chars, so the fallback path is unaffected.
    source_id = (metadata or {}).get("source_id")
    doc_id = hashlib.sha256(str(source_id).encode("utf-8")).hexdigest()[:36] if source_id else str(uuid4())
    try:
        text = text[:8000]
        row = {
            "id": doc_id,
            "vector": embed_text(text),
            "text": text,
            "entry_type": entry_type,
            "metadata_json": json.dumps(meta),
            "written_at": datetime.now(timezone.utc).isoformat(),
            "written_by": written_by,
            # (14/08, #170) -- promoted from metadata_json to real typed
            # columns so find_exact() can filter with a SQL predicate instead
            # of a semantic search + Python parse-and-prefix-match.
            "contract": str((metadata or {}).get("contract") or ""),
            "chain": str((metadata or {}).get("chain") or ""),
        }
        tbl.merge_insert("id").when_matched_update_all().when_not_matched_insert_all().execute(
            [row]
        )
        await audit.log_write_attempt(entry_type, written_by, accepted=True)
        return doc_id
    except Exception as exc:
        logger.warning("lancedb store failed: %s", exc)
        await audit.log_write_attempt(
            entry_type, written_by, accepted=False, reason=f"internal error: {exc}"[:500]
        )
        return None


async def search(
    query: str,
    *,
    entry_type: str | None = None,
    limit: int = 8,
) -> list[dict[str, Any]]:
    """Semantic search — [] if disabled."""
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
                logger.warning("lancedb search: invalid entry_type ignored: %r", entry_type)
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


async def find_exact(
    entry_type: str,
    *,
    contract: str | None = None,
    chain: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Exact-match lookup on the typed ``contract``/``chain`` columns (#170,
    14/08) -- a pure filtered scan (``table.search(query=None)``, confirmed
    against the real installed lancedb: "If None then the select/where/limit
    clauses are applied to filter the table"), NO vector similarity involved.

    Replaces the fragile pattern ``conviction_research.py`` used to rely on:
    a semantic ``search()`` (which can miss or reorder a match before any
    filter even runs, since it ranks by embedding distance) followed by a
    Python parse-and-prefix-match on ``metadata_json``. Sorted most-recent-
    first (``written_at`` descending). ``[]`` if disabled, nothing matches,
    an argument fails validation, or on any internal failure -- never
    raises."""
    if not is_available():
        return []
    if not _ENTRY_TYPE_RE.match(entry_type):
        logger.warning("lancedb find_exact: invalid entry_type ignored: %r", entry_type)
        return []
    clauses = [f"entry_type = '{entry_type}'"]
    for name, value in (("contract", contract), ("chain", chain)):
        if value is None:
            continue
        if not _EXACT_MATCH_VALUE_RE.match(value):
            logger.warning("lancedb find_exact: invalid %s ignored: %r", name, value)
            return []
        clauses.append(f"{name} = '{value}'")
    tbl = get_table()
    if tbl is None:
        return []
    try:
        rows = (
            tbl.search(query=None)
            .where(" AND ".join(clauses))
            .limit(max(1, min(limit, 200)))
            .to_list()
        )
    except Exception as exc:
        logger.warning("lancedb find_exact failed: %s", exc)
        return []
    rows.sort(key=lambda row: row.get("written_at") or "", reverse=True)
    return [
        {
            "id": row.get("id"),
            "content": row.get("text") or "",
            "metadata": json.loads(row.get("metadata_json") or "{}"),
            "distance": None,
        }
        for row in rows
    ]


async def purge_expired_entries() -> dict[str, int]:
    """Applies each entry_type's ``retention_days`` (``schema.yaml``, declared
    since Phase B but never enforced until now, #166 14/08) -- deletes rows
    past their retention window, bounding how long a poisoned or stale entry
    can stay retrievable (OWASP ASI06 mitigation, docs/HANDOFF_LANCEDB.md).

    Entries with no ``written_at`` (the 85 pre-existing rows written by prior
    Claude Code sessions before this column existed) are NEVER touched --
    their real age is unknown, and deleting by assumption would be the
    opposite of fail-safe. Not yet called by any automatic cycle -- the
    future maintenance watchdog (#167) is expected to call this
    periodically; built and tested standalone first.

    Returns ``{entry_type: deleted_count}`` for types that had a
    ``retention_days`` and were actually purged (0 or missing entry -- no
    purge needed/attempted). ``{}`` if disabled or on failure -- never
    raises."""
    if not is_available():
        return {}
    tbl = get_table()
    if tbl is None:
        return {}
    schema = load_schema()
    types = schema.get("entry_types") or {}
    deleted: dict[str, int] = {}
    for entry_type, spec in types.items():
        if not _ENTRY_TYPE_RE.match(entry_type):
            continue
        retention_days = spec.get("retention_days")
        if not retention_days or retention_days <= 0:
            continue
        cutoff = (datetime.now(timezone.utc) - timedelta(days=int(retention_days))).isoformat()
        try:
            before = tbl.count_rows(f"entry_type = '{entry_type}'")
            tbl.delete(
                f"entry_type = '{entry_type}' AND written_at IS NOT NULL "
                f"AND written_at < '{cutoff}'"
            )
            after = tbl.count_rows(f"entry_type = '{entry_type}'")
            removed = max(0, before - after)
            if removed:
                deleted[entry_type] = removed
        except Exception as exc:
            logger.warning("lancedb purge failed for entry_type=%s: %s", entry_type, exc)
    return deleted


# LanceDB OSS (the version used here) does NO automatic background maintenance
# -- unlike compaction alone, cleanup_older_than actually reclaims disk space
# but is IRREVERSIBLE (forfeits time-travel to pruned versions). 30 days is
# deliberately generous for this cycle's first weeks in prod (still a brand
# new mechanism, #166/#167 14/08) -- resettle to LanceDB's own 7-day default
# once a few clean weekly passes are observed (same "accelerated observation"
# doctrine as CLAUDE.md's other first-deployment cadences, applied to the
# retention WINDOW here rather than the cycle's own frequency).
_MAINTENANCE_RETENTION_WINDOW_DAYS = 30


async def run_vector_maintenance() -> dict[str, Any]:
    """Weekly maintenance: applies the declared TTL (``purge_expired_entries``)
    then compacts small fragments and prunes old internal versions
    (``table.optimize``) -- LanceDB's own recommended pattern, never automatic
    in the open-source build. Not yet wired to any cycle by itself; the
    heartbeat task calling this IS the wiring (#167, 14/08). Fail-safe: a
    failed optimize() never raises, purge results are still returned."""
    if not is_available():
        return {"skipped": "disabled"}
    purged = await purge_expired_entries()
    tbl = get_table()
    optimized = False
    if tbl is not None:
        try:
            tbl.optimize(cleanup_older_than=timedelta(days=_MAINTENANCE_RETENTION_WINDOW_DAYS))
            optimized = True
        except Exception as exc:
            logger.warning("lancedb optimize failed: %s", exc)
    return {"purged": purged, "optimized": optimized}
