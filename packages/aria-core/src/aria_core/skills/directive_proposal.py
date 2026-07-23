"""Heartbeat -> directive channel wiring (`aria_directives.propose_directive`),
autonomous pilot for task #82.

Until now `propose_directive` was only called from an operator Telegram
command (`/canal propose`) -- ARIA herself had no autonomous way to submit a
proposal there. This module adds ONE single signal source, deliberately
narrow for a first pilot: a literal `TODO(aria)` marker in the repo's
code/docs, never LLM-generated ideas. Each candidate is proposed only ONCE
(remembered locally).

This module never modifies or bypasses `aria_directives.py`'s gating -- it
calls `propose_directive()` as-is, with one of the 3 categories already
locked by `_DIRECTIVE_CATEGORIES` (never a dynamically chosen category). Gated
OFF by default via `ARIA_DIRECTIVE_PROPOSAL_ENABLED`, a 3rd switch independent
of `HeartbeatTask.enabled` and of `ARIA_DIRECTIVE_CHANNEL_ENABLED` (already
OFF by default on the producer side in `propose_directive` itself).
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[5]

_MARKER_RE = re.compile(r"TODO\(aria\)\s*:?\s*(.*)")

_EXCLUDE_DIR_NAMES = {
    ".venv", "node_modules", "__pycache__", ".git", "dist", "build", ".next",
}

# Closed, hardcoded mapping: this pilot only recognizes one type of marker,
# mapped to a single already-authorized category. Never a dynamic category
# choice here -- expanding it would require a deliberate code change, same as
# for _DIRECTIVE_CATEGORIES.
_MARKER_CATEGORY = "repo_hygiene"

# An arbitrarily long TODO(aria) comment must never land as-is in the queue --
# title/detail stay short and readable for human review.
_MAX_SNIPPET_LEN = 200

_TRUTHY = ("1", "true", "yes", "on")


def directive_proposal_enabled() -> bool:
    """Dedicated gate (3rd switch, independent of propose_directive's producer
    gate and of HeartbeatTask.enabled) -- OFF by default."""
    return os.environ.get("ARIA_DIRECTIVE_PROPOSAL_ENABLED", "").strip().lower() in _TRUTHY


def _is_exempt_dir(name: str) -> bool:
    return name in _EXCLUDE_DIR_NAMES or name.startswith(".")


def _scan_todo_candidates() -> list[dict]:
    """Literal scan (no LLM, no freshness heuristic) of `TODO(aria)` markers
    under the repo root. Returns an ordered list of candidates
    {key, path, line, text}."""
    candidates: list[dict] = []
    for dirpath, dirnames, filenames in os.walk(REPO_ROOT):
        dirnames[:] = [d for d in dirnames if not _is_exempt_dir(d)]
        for filename in sorted(filenames):
            if not (filename.endswith(".py") or filename.endswith(".md")):
                continue
            file_path = Path(dirpath) / filename
            rel = file_path.relative_to(REPO_ROOT).as_posix()
            try:
                lines = file_path.read_text(encoding="utf-8", errors="ignore").splitlines()
            except OSError:
                continue
            for lineno, line in enumerate(lines, start=1):
                match = _MARKER_RE.search(line)
                if not match:
                    continue
                text = match.group(1).strip() or line.strip()
                candidates.append(
                    {
                        "key": f"{rel}:{lineno}",
                        "path": rel,
                        "line": lineno,
                        "text": text,
                    }
                )
    candidates.sort(key=lambda c: c["key"])
    return candidates


async def _ensure_seen_table(db) -> None:
    await db.execute(
        "CREATE TABLE IF NOT EXISTS directive_proposal_seen ("
        "candidate_key TEXT PRIMARY KEY, proposed_at TEXT NOT NULL)"
    )
    await db.commit()


async def _already_seen(candidate_key: str) -> bool:
    import aiosqlite

    from aria_core.paths import aria_db_path

    async with aiosqlite.connect(str(aria_db_path())) as db:
        await _ensure_seen_table(db)
        cursor = await db.execute(
            "SELECT 1 FROM directive_proposal_seen WHERE candidate_key = ?", (candidate_key,)
        )
        row = await cursor.fetchone()
    return row is not None


async def _mark_seen(candidate_key: str) -> None:
    import aiosqlite

    from aria_core.paths import aria_db_path

    async with aiosqlite.connect(str(aria_db_path())) as db:
        await _ensure_seen_table(db)
        await db.execute(
            "INSERT OR IGNORE INTO directive_proposal_seen (candidate_key, proposed_at) VALUES (?, ?)",
            (candidate_key, datetime.now(timezone.utc).isoformat()),
        )
        await db.commit()


async def _pick_next_candidate(candidates: list[dict]) -> dict | None:
    for candidate in candidates:
        if not await _already_seen(candidate["key"]):
            return candidate
    return None


async def run_directive_proposal_cycle(*, notifier=None, scanner=None) -> dict:
    """One round: finds ONE `TODO(aria)` candidate not yet proposed and calls
    `propose_directive` (fixed `repo_hygiene` category). Fail-closed at every
    stage -- never a batch proposal, never a dynamically chosen category."""
    if not directive_proposal_enabled():
        return {"outcome": "skipped_disabled"}

    if scanner is None:
        scanner = _scan_todo_candidates

    candidates = scanner()
    candidate = await _pick_next_candidate(candidates)
    if candidate is None:
        return {"outcome": "nothing_new"}

    from aria_core.aria_directives import propose_directive

    snippet = candidate["text"][:_MAX_SNIPPET_LEN]
    title = f"TODO(aria) dans {candidate['path']}:{candidate['line']}"[:_MAX_SNIPPET_LEN]
    detail = f"{candidate['path']}:{candidate['line']} -- {snippet}"[:_MAX_SNIPPET_LEN]
    result = await propose_directive(_MARKER_CATEGORY, title, detail)

    if not result.get("ok"):
        return {"outcome": "skipped", "reason": result.get("reason"), "path": candidate["path"]}

    await _mark_seen(candidate["key"])

    if notifier:
        try:
            await notifier(f"📋 Directive auto-proposée par ARIA -- {title}")
        except Exception:  # noqa: BLE001 -- a failed notification never breaks the cycle
            pass

    return {
        "outcome": "ok",
        "category": result["category"],
        "title": result["title"],
        "id": result["id"],
        "path": candidate["path"],
    }
