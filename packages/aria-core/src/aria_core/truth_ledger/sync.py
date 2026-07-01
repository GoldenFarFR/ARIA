"""Sync truth ledger files to aria-sandbox/truth-ledger/ on GitHub (batched)."""

from __future__ import annotations

import asyncio
import logging

import aiosqlite

from aria_core.github_client import GitHubClient
from aria_core.paths import aria_db_path, truth_ledger_dir
from aria_core.runtime import settings
from aria_core.skills.github_skill import github_configured, repo_write_allowed

logger = logging.getLogger(__name__)

GITHUB_LEDGER_PREFIX = "truth-ledger"
_flush_lock = asyncio.Lock()
_scheduler_task: asyncio.Task | None = None


def _batch_size() -> int:
    raw = getattr(settings, "truth_ledger_github_batch_size", 100)
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 100


def _batch_interval_sec() -> float:
    raw = getattr(settings, "truth_ledger_github_batch_interval_sec", 300)
    try:
        return max(30.0, float(raw))
    except (TypeError, ValueError):
        return 300.0


async def count_unsynced_entries() -> int:
    async with aiosqlite.connect(str(aria_db_path())) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM truth_entries WHERE github_synced = 0"
        )
        row = await cursor.fetchone()
        return int(row[0]) if row else 0


async def _fetch_unsynced_entries(limit: int) -> list[tuple[str, str]]:
    async with aiosqlite.connect(str(aria_db_path())) as db:
        cursor = await db.execute(
            """
            SELECT id, file_path FROM truth_entries
            WHERE github_synced = 0
            ORDER BY created_at ASC
            LIMIT ?
            """,
            (limit,),
        )
        rows = await cursor.fetchall()
    return [(row[0], row[1]) for row in rows]


async def _mark_entries_synced(entry_ids: list[str]) -> None:
    if not entry_ids:
        return
    placeholders = ",".join("?" * len(entry_ids))
    async with aiosqlite.connect(str(aria_db_path())) as db:
        await db.execute(
            f"UPDATE truth_entries SET github_synced = 1 WHERE id IN ({placeholders})",
            entry_ids,
        )
        await db.commit()


async def flush_pending_github_sync() -> int:
    """Push up to batch_size unsynced ledger files in one GitHub commit."""
    async with _flush_lock:
        if not github_configured():
            return 0

        owner = settings.github_owner
        repo = settings.github_sandbox_repo
        if not repo_write_allowed(owner, repo):
            return 0

        pending = await _fetch_unsynced_entries(_batch_size())
        if not pending:
            return 0

        files: list[tuple[str, str]] = []
        entry_ids: list[str] = []
        ledger_root = truth_ledger_dir()
        for entry_id, rel_path in pending:
            local = ledger_root / rel_path
            if not local.exists():
                logger.warning(
                    "Truth ledger file missing for %s (%s)", entry_id[:8], rel_path
                )
                continue
            files.append((f"{GITHUB_LEDGER_PREFIX}/{rel_path}", local.read_text(encoding="utf-8")))
            entry_ids.append(entry_id)

        if not files:
            return 0

        first_id = entry_ids[0][:8]
        last_id = entry_ids[-1][:8]
        message = (
            f"ARIA truth-ledger batch: {len(files)} entries ({first_id}…{last_id})"
        )
        client = GitHubClient(settings.github_token)
        try:
            await client.put_files_batch(owner, repo, files, message)
        except Exception as exc:
            logger.warning("Truth ledger GitHub batch sync failed (%d files): %s", len(files), exc)
            return 0

        await _mark_entries_synced(entry_ids)
        logger.info("Truth ledger GitHub batch synced %d entries", len(entry_ids))
        return len(entry_ids)


async def schedule_github_sync() -> None:
    """Queue ledger files for batched GitHub mirror (local write is immediate)."""
    await ensure_github_sync_scheduler()
    if await count_unsynced_entries() >= _batch_size():
        asyncio.create_task(flush_pending_github_sync())


async def ensure_github_sync_scheduler() -> None:
    global _scheduler_task
    if _scheduler_task is not None and not _scheduler_task.done():
        return
    _scheduler_task = asyncio.create_task(_github_sync_loop())


async def _github_sync_loop() -> None:
    while True:
        await asyncio.sleep(_batch_interval_sec())
        try:
            await flush_pending_github_sync()
        except Exception as exc:
            logger.warning("Truth ledger scheduled flush failed: %s", exc)


async def sync_entry_to_github(rel_path: str, entry_id: str) -> bool:
    """Legacy single-file sync — prefer batched flush_pending_github_sync."""
    if not github_configured():
        return False
    local = truth_ledger_dir() / rel_path
    if not local.exists():
        return False

    owner = settings.github_owner
    repo = settings.github_sandbox_repo
    if not repo_write_allowed(owner, repo):
        return False

    gh_path = f"{GITHUB_LEDGER_PREFIX}/{rel_path}"
    content = local.read_text(encoding="utf-8")
    client = GitHubClient(settings.github_token)
    _, sha = await client.get_file_text(owner, repo, gh_path)
    await client.put_file(
        owner,
        repo,
        gh_path,
        content,
        f"ARIA truth-ledger: {entry_id[:8]}",
        sha=sha,
    )
    return True