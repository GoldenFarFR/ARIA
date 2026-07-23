"""ARIA's self-improvement ledger — the memory of her possible upgrades.

When ARIA spots a tool, a data source, a product, or an idea that could make
her better, she **logs it here** rather than forgetting it. Each candidate
follows an honest lifecycle:

    proposed  ->  testing  ->  grafted   (grafted: PROVED it improves calibration)
                           ->  rejected  (tested, brings nothing / fails the dome)

Principles (dome):
- **Grafting ALWAYS goes through a human-validated PR** (via the worker task
  queue) — never an auto-merge of code into the core. ARIA discovers,
  proposes, tests; the human validates the merge.
- **Proof before grafting**: a candidate only moves to `grafted` if it
  improves calibration as MEASURED on the track record (`evidence` documents
  the gain). "It looks good" isn't enough.
- An external candidate (third-party tool/product) must pass the dome
  (sanitization, no untrusted code execution) before any real test.

Local SQLite storage `aria.db`, table `improvement_candidate` (append-only).
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from aria_core.paths import aria_db_path

DB_PATH = str(aria_db_path())

_SEEDS_PATH = Path(__file__).resolve().parent / "knowledge" / "improvement_seeds.yaml"

_STATUSES = ("proposed", "testing", "grafted", "rejected")
_CATEGORIES = ("tool", "data_source", "product", "artifact", "idea")

_COLUMNS = [
    "id",
    "title",
    "description",
    "category",
    "source",
    "benefit",
    "seam",
    "status",
    "evidence",
    "worker_task_id",
    "created_at",
    "updated_at",
]


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS improvement_candidate (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT,
                category TEXT NOT NULL DEFAULT 'idea',
                source TEXT,
                benefit TEXT,
                seam TEXT,
                status TEXT NOT NULL DEFAULT 'proposed',
                evidence TEXT,
                worker_task_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        await db.commit()


async def record_candidate(
    *,
    title: str,
    description: str = "",
    category: str = "idea",
    source: str = "",
    benefit: str = "",
    seam: str = "",
) -> int:
    """Logs an improvement candidate (status ``proposed``). Returns its id.

    ``category`` in {tool, data_source, product, artifact, idea} (falls back
    to 'idea'). ``seam`` = the anticipated anchor point (e.g. ``include_<x>``,
    ``services/<name>``) so the future graft is a simple hook-up, not a
    rewrite. Light dedup: a ``title`` that's already non-rejected isn't
    re-inserted.
    """
    await _ensure_table()
    cat = category if category in _CATEGORIES else "idea"
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        existing = await (
            await db.execute(
                "SELECT id FROM improvement_candidate "
                "WHERE LOWER(title) = LOWER(?) AND status != 'rejected' LIMIT 1",
                (title,),
            )
        ).fetchone()
        if existing:
            return int(existing[0])
        cursor = await db.execute(
            """
            INSERT INTO improvement_candidate
            (title, description, category, source, benefit, seam, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'proposed', ?, ?)
            """,
            (title, description, cat, source, benefit, seam, now, now),
        )
        await db.commit()
        return int(cursor.lastrowid)


async def update_candidate(
    candidate_id: int,
    *,
    status: str | None = None,
    evidence: str | None = None,
    worker_task_id: str | None = None,
) -> dict | None:
    """Updates a candidate (advances its lifecycle). Returns the row, or None.

    A transition to ``grafted`` MUST be backed by proof (``evidence``):
    without it, the transition is refused (returns None) — we never graft on
    a mere impression.
    """
    await _ensure_table()
    fields, values = [], []
    if status is not None:
        if status not in _STATUSES:
            return None
        if status == "grafted" and not (evidence or "").strip():
            return None  # no grafting without proof
        fields.append("status = ?")
        values.append(status)
    if evidence is not None:
        fields.append("evidence = ?")
        values.append(evidence)
    if worker_task_id is not None:
        fields.append("worker_task_id = ?")
        values.append(worker_task_id)
    if not fields:
        return await get_candidate(candidate_id)
    fields.append("updated_at = ?")
    values.append(datetime.now(timezone.utc).isoformat())
    values.append(candidate_id)
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            f"UPDATE improvement_candidate SET {', '.join(fields)} WHERE id = ?", values
        )
        await db.commit()
        if cursor.rowcount == 0:
            return None
    return await get_candidate(candidate_id)


async def get_candidate(candidate_id: int) -> dict | None:
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (
            await db.execute(
                "SELECT * FROM improvement_candidate WHERE id = ?", (candidate_id,)
            )
        ).fetchone()
    return dict(zip(_COLUMNS, row)) if row else None


async def list_candidates(status: str | None = None, limit: int = 50) -> list[dict]:
    """Lists candidates, most recent to oldest, filterable by status."""
    await _ensure_table()
    query = "SELECT * FROM improvement_candidate"
    params: tuple = ()
    if status is not None:
        query += " WHERE status = ?"
        params = (status,)
    query += " ORDER BY id DESC LIMIT ?"
    params += (limit,)
    async with aiosqlite.connect(DB_PATH) as db:
        rows = await (await db.execute(query, params)).fetchall()
    return [dict(zip(_COLUMNS, row)) for row in rows]


async def ingest_seeds(path: str | Path | None = None) -> int:
    """Loads improvement seeds (YAML) into ARIA's memory.

    Idempotent: the title-based dedup in ``record_candidate`` means a second
    call adds nothing (seeds already present aren't duplicated). Called at
    startup so ARIA "remembers" the leads she's spotted. Returns the number of
    seeds processed (existing or new). Graceful degradation: missing file or
    unreadable YAML -> 0, never an exception that breaks the boot.
    """
    seeds_path = Path(path) if path else _SEEDS_PATH
    if not seeds_path.exists():
        return 0
    try:
        import yaml  # dependency already present (config); lazy import

        seeds = yaml.safe_load(seeds_path.read_text(encoding="utf-8")) or []
    except Exception:  # noqa: BLE001 — never blocking at startup
        return 0
    if not isinstance(seeds, list):
        return 0
    count = 0
    for seed in seeds:
        if not isinstance(seed, dict) or not seed.get("title"):
            continue
        await record_candidate(
            title=str(seed.get("title")),
            description=str(seed.get("description", "")),
            category=str(seed.get("category", "idea")),
            source=str(seed.get("source", "")),
            benefit=str(seed.get("benefit", "")),
            seam=str(seed.get("seam", "")),
        )
        count += 1
    return count


async def count_by_status() -> dict:
    """Counters by status (for a "where does self-improvement stand" dashboard)."""
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        rows = await (
            await db.execute(
                "SELECT status, COUNT(*) FROM improvement_candidate GROUP BY status"
            )
        ).fetchall()
    counts = {s: 0 for s in _STATUSES}
    for status, n in rows:
        counts[status] = int(n)
    return counts
