"""ARIA -> Claude Code directive channel (pilot, audited 10/07).

ARIA (the head) drops prioritized directives into a queue; a Claude Code
session (VPS side, started by a human) reads and executes them. This module
EXECUTES NOTHING and writes nothing externally (GitHub/X/email): it's a
local SQLite queue plus a tamper-proof audit log.

Deliberate boundaries (lessons from the Cursor/worker-queue incident, 10/07):
  - **Hardcoded scope**: ``_DIRECTIVE_CATEGORIES`` limits directives to the one
    family already delegated (repo hygiene, docs, backlog). Any category outside
    the list is REFUSED at write time. Widening the list = a deliberate code
    change, locked by ``test_coherence`` (never a silent drift).
  - **Gate OFF by default**: ``ARIA_DIRECTIVE_CHANNEL_ENABLED`` closes the door on
    the producer side (no directive gets in) until it's set. Fail-closed.
  - **Dedicated kill-switch**: ``halt_channel()`` sets a marker (distinct from the
    Telegram /stop and from ``outgoing_pause``); the reader stops BEFORE every
    directive.
  - **Append-only log**: the ``aria_directive_log`` table only ever receives
    INSERTs (no UPDATE/DELETE function exists in this file) -> a trail that's
    reviewable even without prior validation.

Two boundaries this channel NEVER crosses (neither now nor once widened):
real capital (ARIA's Telegram validation stays sealed, out of the allowlist
forever) and modifying the channel itself or its guardrails (otherwise ARIA
could self-expand its own powers -- the exact flaw of the Cursor incident).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

import aiosqlite

from aria_core.paths import aria_db_path, data_dir

logger = logging.getLogger(__name__)

DB_PATH = str(aria_db_path())

# ALLOWED scope of the pilot -- exactly the family already delegated to Claude Code
# ("clean, automated and consistent GitHub"). Locked by test_coherence: widening it
# requires a deliberate code change in the same commit.
_DIRECTIVE_CATEGORIES = frozenset({"repo_hygiene", "docs", "backlog"})

_HALT_MARKER = "aria_directive_halt"

_TRUTHY = ("1", "true", "yes", "on")


def channel_enabled() -> bool:
    """Producer gate OFF by default: without this flag, no directive enters the queue."""
    return os.environ.get("ARIA_DIRECTIVE_CHANNEL_ENABLED", "").strip().lower() in _TRUTHY


def _halt_path():
    return data_dir() / _HALT_MARKER


def is_halted() -> bool:
    """Dedicated kill-switch (file marker), independent of the Telegram /stop."""
    return _halt_path().exists()


async def _ensure_tables() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS aria_directive (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL,
                title TEXT NOT NULL,
                detail TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                proposed_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                outcome TEXT NOT NULL DEFAULT ''
            )
            """
        )
        # Append-only log: only INSERTs, never UPDATE/DELETE (no function in
        # this module touches them -- locked by test_coherence).
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS aria_directive_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                directive_id INTEGER,
                actor TEXT NOT NULL,
                event TEXT NOT NULL,
                detail TEXT NOT NULL DEFAULT '',
                at TEXT NOT NULL
            )
            """
        )
        await db.commit()


async def _log(db, *, directive_id: int | None, actor: str, event: str, detail: str = "") -> None:
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO aria_directive_log (directive_id, actor, event, detail, at) "
        "VALUES (?, ?, ?, ?, ?)",
        (directive_id, actor, event, detail[:2000], now),
    )


async def propose_directive(category: str, title: str, detail: str = "") -> dict:
    """PRODUCER side (ARIA): drops a directive. Executes nothing.

    Refuses (without writing to the queue) if the channel is OFF, if the
    kill-switch is active, or if the category is outside the allowed scope.
    A refusal is still logged (trace of the attempt).
    """
    category = (category or "").strip().lower()
    title = (title or "").strip()
    detail = (detail or "").strip()
    await _ensure_tables()

    if not channel_enabled():
        return {"ok": False, "reason": "canal desactive (ARIA_DIRECTIVE_CHANNEL_ENABLED off)"}
    if is_halted():
        return {"ok": False, "reason": "coupe-circuit actif"}
    if category not in _DIRECTIVE_CATEGORIES:
        async with aiosqlite.connect(DB_PATH) as db:
            await _log(
                db, directive_id=None, actor="aria", event="refused",
                detail=f"categorie hors perimetre: {category!r}",
            )
            await db.commit()
        return {"ok": False, "reason": f"categorie '{category}' hors perimetre autorise"}
    if not title:
        return {"ok": False, "reason": "titre vide"}

    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO aria_directive (category, title, detail, status, proposed_at, updated_at) "
            "VALUES (?, ?, ?, 'pending', ?, ?)",
            (category, title, detail, now, now),
        )
        directive_id = cur.lastrowid
        await _log(db, directive_id=directive_id, actor="aria", event="proposed", detail=title)
        await db.commit()
    logger.info("aria_directives: directive #%s proposed (%s) %s", directive_id, category, title)
    return {"ok": True, "id": directive_id, "category": category, "title": title}


async def list_directives(status: str | None = None, limit: int = 100) -> list[dict]:
    """Lists directives (all, or filtered by status). Read-only."""
    await _ensure_tables()
    cols = ["id", "category", "title", "detail", "status", "proposed_at", "updated_at", "outcome"]
    query = f"SELECT {', '.join(cols)} FROM aria_directive"
    params: tuple = ()
    if status:
        query += " WHERE status=?"
        params = (status,)
    query += " ORDER BY id ASC LIMIT ?"
    params = params + (limit,)
    async with aiosqlite.connect(DB_PATH) as db:
        rows = await (await db.execute(query, params)).fetchall()
    return [dict(zip(cols, row)) for row in rows]


async def claim_next_directive() -> dict | None:
    """READER side (Claude Code session on the VPS): claims the oldest 'pending'
    directive and moves it to 'executing'.

    Returns None if the channel is OFF, if the kill-switch is active, or if the
    queue is empty -- the reader stops BEFORE any action. The session's security
    classifier remains the last line of defense on real execution.
    """
    await _ensure_tables()
    if not channel_enabled() or is_halted():
        return None
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (
            await db.execute(
                "SELECT id, category, title, detail FROM aria_directive "
                "WHERE status='pending' ORDER BY id ASC LIMIT 1"
            )
        ).fetchone()
        if row is None:
            return None
        directive_id = row[0]
        await db.execute(
            "UPDATE aria_directive SET status='executing', updated_at=? WHERE id=?",
            (now, directive_id),
        )
        await _log(db, directive_id=directive_id, actor="claude", event="claimed", detail=row[2])
        await db.commit()
    return {"id": row[0], "category": row[1], "title": row[2], "detail": row[3]}


async def complete_directive(directive_id: int, outcome: str = "") -> dict:
    """READER side: marks a directive as executed, with the outcome report."""
    await _ensure_tables()
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE aria_directive SET status='done', updated_at=?, outcome=? WHERE id=?",
            (now, (outcome or "").strip()[:2000], directive_id),
        )
        await _log(db, directive_id=directive_id, actor="claude", event="executed", detail=outcome)
        await db.commit()
    return {"ok": True, "id": directive_id, "status": "done"}


async def refuse_directive(directive_id: int, reason: str = "") -> dict:
    """READER side: refuses a directive (judged out of scope, ambiguous, risky)."""
    await _ensure_tables()
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE aria_directive SET status='refused', updated_at=?, outcome=? WHERE id=?",
            (now, (reason or "").strip()[:2000], directive_id),
        )
        await _log(db, directive_id=directive_id, actor="claude", event="refused", detail=reason)
        await db.commit()
    return {"ok": True, "id": directive_id, "status": "refused"}


async def read_log(limit: int = 200) -> list[dict]:
    """Reads the audit log (append-only), most recent first. Read-only."""
    await _ensure_tables()
    cols = ["id", "directive_id", "actor", "event", "detail", "at"]
    async with aiosqlite.connect(DB_PATH) as db:
        rows = await (
            await db.execute(
                f"SELECT {', '.join(cols)} FROM aria_directive_log ORDER BY id DESC LIMIT ?",
                (limit,),
            )
        ).fetchall()
    return [dict(zip(cols, row)) for row in rows]


async def halt_channel(reason: str = "") -> dict:
    """Kill-switch: freezes the channel (sets the marker). Logs the halt."""
    await _ensure_tables()
    _halt_path().write_text(
        (reason or "halt").strip()[:500], encoding="utf-8"
    )
    async with aiosqlite.connect(DB_PATH) as db:
        await _log(db, directive_id=None, actor="operator", event="halted", detail=reason)
        await db.commit()
    logger.warning("aria_directives: CHANNEL FROZEN (%s)", reason or "no reason given")
    return {"ok": True, "halted": True}


async def resume_channel() -> dict:
    """Lifts the kill-switch (removes the marker). Logs the resumption."""
    await _ensure_tables()
    path = _halt_path()
    if path.exists():
        path.unlink()
    async with aiosqlite.connect(DB_PATH) as db:
        await _log(db, directive_id=None, actor="operator", event="resumed")
        await db.commit()
    return {"ok": True, "halted": False}
