"""Truth ledger — mega history of ARIA exchanges, grounded on verified facts only."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from uuid import uuid4

import aiosqlite

from aria_core.paths import aria_db_path, truth_ledger_dir
from aria_core.truth_ledger.io import atomic_write_text, read_modify_write
from aria_core.truth_ledger.sync import schedule_github_sync

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())
LEDGER_DIR = truth_ledger_dir()
GITHUB_LEDGER_PREFIX = "truth-ledger"
CANONICAL_SOURCE = "canonical_facts.yaml"

# Skills whose answers are auto-marked verified (factual sources)
AUTO_VERIFY_SKILLS = frozenset({
    "faq_content",
    "epistemic_check",
    "analyze_portfolio",
    "memory_recall",
    "launchpad_select",
    "zhc_bridge",
})


async def init_truth_ledger() -> None:
    os.makedirs(LEDGER_DIR, exist_ok=True)
    readme = LEDGER_DIR / "README.md"
    if not readme.exists():
        readme.write_text(
            "# ARIA Truth Ledger\n\n"
            "Mega history of every ARIA exchange. Only `verified` entries ground future answers.\n\n"
            "Canonical facts: edit `aria_core/truth_ledger/canonical_facts.yaml` in aria-sandbox — "
            "startup sync supersedes stale entries automatically.\n\n"
            "Mirrors `aria-sandbox/truth-ledger/` on GitHub when `GITHUB_TOKEN` is set.\n",
            encoding="utf-8",
        )
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS truth_entries (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                user_message TEXT NOT NULL,
                agent_reply TEXT NOT NULL,
                skill_used TEXT,
                sources TEXT DEFAULT '[]',
                visitor_id TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                file_path TEXT NOT NULL,
                github_synced INTEGER DEFAULT 0
            )
            """
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_truth_status ON truth_entries(status)"
        )
        await _migrate_truth_ledger_columns(db)
        await db.commit()


async def _migrate_truth_ledger_columns(db: aiosqlite.Connection) -> None:
    cursor = await db.execute("PRAGMA table_info(truth_entries)")
    cols = {row[1] for row in await cursor.fetchall()}
    migrations = [
        ("canonical_id", "TEXT"),
        ("topic", "TEXT"),
        ("supersedes", "TEXT DEFAULT '[]'"),
        ("answer_hash", "TEXT"),
    ]
    for name, typedef in migrations:
        if name not in cols:
            await db.execute(f"ALTER TABLE truth_entries ADD COLUMN {name} {typedef}")
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_truth_canonical ON truth_entries(canonical_id)"
    )


def _answer_hash(answer: str) -> str:
    return hashlib.sha256(answer.strip().encode()).hexdigest()[:12]


def _auto_verify(skill_used: str | None, sources: list[str]) -> str:
    if skill_used in AUTO_VERIFY_SKILLS:
        return "verified"
    if "faq.yaml" in sources or "faq_direct" in sources:
        return "verified"
    return "pending"


def _write_markdown_file(
    entry_id: str,
    created: datetime,
    user_message: str,
    agent_reply: str,
    skill_used: str | None,
    sources: list[str],
    visitor_id: str,
    status: str,
) -> str:
    day = created.strftime("%Y-%m-%d")
    day_dir = LEDGER_DIR / day
    day_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{created.strftime('%H%M%S')}-{entry_id[:8]}.md"
    rel_path = f"{day}/{fname}"
    abs_path = LEDGER_DIR / rel_path
    sources_line = ", ".join(sources) if sources else "none"
    body = f"""---
id: {entry_id}
created_at: {created.isoformat()}
skill: {skill_used or "general"}
sources: [{sources_line}]
visitor_id: {visitor_id or "anonymous"}
status: {status}
---

## Question
{user_message.strip()}

## Answer
{agent_reply.strip()}

## Meta
- Grounding: `{status}` — only `verified` entries feed future answers
- Sandbox path: `{GITHUB_LEDGER_PREFIX}/{rel_path}`
"""
    atomic_write_text(abs_path, body)
    return rel_path


def _write_canonical_markdown(meta: dict) -> str:
    entry_id = meta["id"]
    created = datetime.fromisoformat(meta["created_at"])
    day = created.strftime("%Y-%m-%d")
    day_dir = LEDGER_DIR / day
    day_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{created.strftime('%H%M%S')}-canonical-{meta['canonical_id']}.md"
    rel_path = f"{day}/{fname}"
    abs_path = LEDGER_DIR / rel_path
    supersedes = meta.get("supersedes") or []
    sup_line = ", ".join(supersedes) if supersedes else "none"
    tags = meta.get("tags") or []
    tags_line = ", ".join(tags) if tags else "none"
    body = f"""---
id: {entry_id}
created_at: {created.isoformat()}
canonical_id: {meta['canonical_id']}
topic: {meta.get('topic') or meta['canonical_id']}
skill: canonical_facts
sources: [{CANONICAL_SOURCE}]
tags: [{tags_line}]
supersedes: [{sup_line}]
answer_hash: {meta.get('answer_hash', '')}
status: verified
---

## Question
{meta['question'].strip()}

## Answer
{meta['answer'].strip()}

## Meta
- Canonical fact — edit `{CANONICAL_SOURCE}` when this truth changes
- Supersedes prior entry ids: `{sup_line}`
- Sandbox path: `{GITHUB_LEDGER_PREFIX}/{rel_path}`
"""
    atomic_write_text(abs_path, body)
    return rel_path


async def supersede_canonical_id(canonical_id: str) -> list[str]:
    """Mark active verified entries for this canonical_id as superseded."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            SELECT id, file_path FROM truth_entries
            WHERE canonical_id = ? AND status = 'verified'
            """,
            (canonical_id,),
        )
        rows = await cursor.fetchall()
        if not rows:
            return []
        old_ids = [row[0] for row in rows]
        placeholders = ",".join("?" * len(old_ids))
        await db.execute(
            f"UPDATE truth_entries SET status = 'superseded' WHERE id IN ({placeholders})",
            old_ids,
        )
        await db.commit()
    for _, file_path in rows:
        _mark_file_superseded(file_path)
    return old_ids


def _mark_file_superseded(file_path: str) -> None:
    full = LEDGER_DIR / file_path

    def _to_superseded(text: str) -> str:
        if "status: verified" in text:
            return text.replace("status: verified", "status: superseded", 1)
        return text

    read_modify_write(
        full,
        _to_superseded,
        ledger_dir=LEDGER_DIR,
        missing_ok=True,
    )


async def get_active_canonical_hash(canonical_id: str) -> str | None:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            SELECT answer_hash FROM truth_entries
            WHERE canonical_id = ? AND status = 'verified'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (canonical_id,),
        )
        row = await cursor.fetchone()
        return row[0] if row else None


async def upsert_canonical_entry(
    *,
    canonical_id: str,
    topic: str,
    question: str,
    answer: str,
    tags: list[str] | None = None,
    supersedes: list[str] | None = None,
) -> dict:
    """Insert a new verified canonical fact (caller must supersede old ids first)."""
    await init_truth_ledger()
    entry_id = str(uuid4())
    created = datetime.now(timezone.utc)
    a_hash = _answer_hash(answer)
    rel_path = _write_canonical_markdown({
        "id": entry_id,
        "created_at": created.isoformat(),
        "canonical_id": canonical_id,
        "topic": topic,
        "question": question,
        "answer": answer,
        "tags": tags or [],
        "supersedes": supersedes or [],
        "answer_hash": a_hash,
    })

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO truth_entries
            (id, created_at, user_message, agent_reply, skill_used, sources,
             visitor_id, status, file_path, github_synced,
             canonical_id, topic, supersedes, answer_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)
            """,
            (
                entry_id,
                created.isoformat(),
                question[:4000],
                answer[:8000],
                "canonical_facts",
                json.dumps([CANONICAL_SOURCE]),
                "",
                "verified",
                rel_path,
                canonical_id,
                topic,
                json.dumps(supersedes or []),
                a_hash,
            ),
        )
        await db.commit()

    await schedule_github_sync()

    return {
        "id": entry_id,
        "canonical_id": canonical_id,
        "topic": topic,
        "question": question,
        "answer": answer,
        "tags": tags or [],
        "supersedes": supersedes or [],
        "answer_hash": a_hash,
        "created_at": created.isoformat(),
        "file_path": rel_path,
        "github_synced": False,
    }


async def record_exchange(
    user_message: str,
    agent_reply: str,
    *,
    skill_used: str | None = None,
    sources: list[str] | None = None,
    visitor_id: str = "",
    force_status: str | None = None,
) -> dict:
    """Log every exchange to local ledger + optional GitHub sandbox."""
    await init_truth_ledger()
    entry_id = str(uuid4())
    created = datetime.now(timezone.utc)
    src = list(sources or [])
    status = force_status or _auto_verify(skill_used, src)
    rel_path = _write_markdown_file(
        entry_id, created, user_message, agent_reply,
        skill_used, src, visitor_id, status,
    )

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO truth_entries
            (id, created_at, user_message, agent_reply, skill_used, sources,
             visitor_id, status, file_path, github_synced)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (
                entry_id,
                created.isoformat(),
                user_message[:4000],
                agent_reply[:8000],
                skill_used,
                json.dumps(src),
                visitor_id,
                status,
                rel_path,
            ),
        )
        await db.commit()

    await schedule_github_sync()

    return {
        "id": entry_id,
        "status": status,
        "file_path": rel_path,
        "github_synced": False,
    }


def _score_entry(query: str, user_message: str, agent_reply: str) -> int:
    q = query.lower()
    score = 0
    blob = f"{user_message} {agent_reply}".lower()
    for token in re.findall(r"[a-z0-9]{3,}", q):
        if token in user_message.lower():
            score += 3
        if token in agent_reply.lower():
            score += 1
    return score


async def search_verified(query: str, limit: int = 5) -> list[dict]:
    """Retrieve verified ledger entries matching the query (excludes superseded)."""
    if not query.strip():
        return []
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """
            SELECT id, user_message, agent_reply, skill_used, sources,
                   created_at, file_path, canonical_id
            FROM truth_entries
            WHERE status = 'verified'
            ORDER BY created_at DESC
            LIMIT 200
            """
        )
        rows = await cursor.fetchall()

    seen_canonical: set[str] = set()
    scored: list[tuple[dict, int]] = []
    for row in rows:
        canonical_id = row[7]
        if canonical_id:
            if canonical_id in seen_canonical:
                continue
            seen_canonical.add(canonical_id)
        entry = {
            "id": row[0],
            "user_message": row[1],
            "agent_reply": row[2],
            "skill_used": row[3],
            "sources": json.loads(row[4] or "[]"),
            "created_at": row[5],
            "file_path": row[6],
            "canonical_id": canonical_id,
        }
        s = _score_entry(query, entry["user_message"], entry["agent_reply"])
        if s > 0:
            scored.append((entry, s))
    scored.sort(key=lambda x: x[1], reverse=True)
    return [e for e, _ in scored[:limit]]


async def verify_entry(entry_id: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT file_path FROM truth_entries WHERE id = ?",
            (entry_id,),
        )
        row = await cursor.fetchone()
        if not row:
            return False
        await db.execute(
            "UPDATE truth_entries SET status = 'verified' WHERE id = ?",
            (entry_id,),
        )
        await db.commit()
        file_path = row[0]
    full = LEDGER_DIR / file_path

    def _to_verified(text: str) -> str:
        if "status: pending" in text:
            return text.replace("status: pending", "status: verified", 1)
        return text

    read_modify_write(full, _to_verified, ledger_dir=LEDGER_DIR, missing_ok=True)
    return True


async def ledger_stats() -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT status, COUNT(*) FROM truth_entries GROUP BY status"
        )
        counts = {row[0]: row[1] for row in await cursor.fetchall()}
        cursor = await db.execute("SELECT COUNT(*) FROM truth_entries WHERE github_synced = 1")
        synced = (await cursor.fetchone())[0]
    return {
        "total": sum(counts.values()),
        "verified": counts.get("verified", 0),
        "pending": counts.get("pending", 0),
        "superseded": counts.get("superseded", 0),
        "github_synced": synced,
        "local_dir": str(LEDGER_DIR),
        "github_path": f"{GITHUB_LEDGER_PREFIX}/",
        "canonical_source": CANONICAL_SOURCE,
    }