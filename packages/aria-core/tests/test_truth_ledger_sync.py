import asyncio

import pytest

from aria_core.paths import aria_db_path, truth_ledger_dir
from aria_core.testing import AriaRuntimeSettings, configure_test_runtime
from aria_core.truth_ledger import store as ledger_store
from aria_core.truth_ledger.store import init_truth_ledger, record_exchange
from aria_core.truth_ledger import sync as ledger_sync


async def _noop_scheduler() -> None:
    return None


def _bind_store_paths() -> None:
    ledger_store.DB_PATH = str(aria_db_path())
    ledger_store.LEDGER_DIR = truth_ledger_dir()


@pytest.mark.asyncio
async def test_flush_batches_unsynced_entries(monkeypatch, tmp_path):
    configure_test_runtime(data_dir=tmp_path, settings=AriaRuntimeSettings())
    _bind_store_paths()
    monkeypatch.setattr(ledger_sync, "ensure_github_sync_scheduler", _noop_scheduler)
    await init_truth_ledger()

    for i in range(3):
        await record_exchange(f"question {i}", f"answer {i}", skill_used="faq_content")

    assert await ledger_sync.count_unsynced_entries() == 3

    calls: list[dict] = []

    async def fake_batch(owner, repo, files, message, branch="main"):
        calls.append({"owner": owner, "repo": repo, "files": files, "message": message})
        return {"commit_sha": "abc", "files": len(files)}

    monkeypatch.setattr(
        "aria_core.truth_ledger.sync.github_configured",
        lambda: True,
    )
    monkeypatch.setattr(
        "aria_core.truth_ledger.sync.repo_write_allowed",
        lambda owner, repo: True,
    )

    class FakeClient:
        def __init__(self, _token: str) -> None:
            pass

        async def put_files_batch(self, owner, repo, files, message, branch="main"):
            return await fake_batch(owner, repo, files, message, branch)

    monkeypatch.setattr(
        "aria_core.truth_ledger.sync.GitHubClient",
        FakeClient,
    )

    synced = await ledger_sync.flush_pending_github_sync()
    assert synced == 3
    assert await ledger_sync.count_unsynced_entries() == 0
    assert len(calls) == 1
    assert len(calls[0]["files"]) == 3
    assert "batch" in calls[0]["message"]


@pytest.mark.asyncio
async def test_schedule_triggers_flush_at_batch_size(monkeypatch, tmp_path):
    configure_test_runtime(data_dir=tmp_path, settings=AriaRuntimeSettings())
    _bind_store_paths()
    await init_truth_ledger()
    monkeypatch.setattr(ledger_sync, "_batch_size", lambda: 2)
    monkeypatch.setattr(ledger_sync, "ensure_github_sync_scheduler", _noop_scheduler)

    flushed = 0

    async def fake_flush():
        nonlocal flushed
        flushed += 1
        return 2

    monkeypatch.setattr(ledger_sync, "flush_pending_github_sync", fake_flush)

    await record_exchange("q1", "a1")
    await record_exchange("q2", "a2")

    for _ in range(20):
        if flushed:
            break
        await asyncio.sleep(0.05)

    assert flushed >= 1