import pytest
import yaml

from aria_core.truth_ledger.canonical import (
    canonical_facts_sync_enabled,
    load_canonical_facts,
    sync_canonical_facts,
)
from aria_core.truth_ledger.store import (
    get_active_canonical_hash,
    init_truth_ledger,
    ledger_stats,
    search_verified,
    supersede_canonical_id,
    upsert_canonical_entry,
)


@pytest.fixture(autouse=True)
def isolated_canonical_truth_db(tmp_path, monkeypatch):
    db = tmp_path / "aria.db"
    ledger = tmp_path / "truth-ledger"
    db_path = lambda: db
    ledger_dir = lambda: ledger
    for target in (
        "aria_core.paths.aria_db_path",
        "aria_core.truth_ledger.sync.aria_db_path",
        "aria_core.paths.truth_ledger_dir",
        "aria_core.truth_ledger.sync.truth_ledger_dir",
    ):
        monkeypatch.setattr(
            target,
            db_path if "db" in target else ledger_dir,
        )
    monkeypatch.setattr("aria_core.truth_ledger.store.DB_PATH", str(db))
    monkeypatch.setattr("aria_core.truth_ledger.store.LEDGER_DIR", ledger)


@pytest.mark.asyncio
async def test_canonical_upsert_and_search():
    await init_truth_ledger()
    await upsert_canonical_entry(
        canonical_id="test-fact",
        topic="test",
        question="What is the test product?",
        answer="TestProduct is a demo subsidiary for unit tests.",
        tags=["test"],
    )
    hits = await search_verified("test product demo")
    assert any("TestProduct" in h["agent_reply"] for h in hits)
    h = await get_active_canonical_hash("test-fact")
    assert h is not None


@pytest.mark.asyncio
async def test_canonical_supersede_excludes_old_from_grounding():
    await init_truth_ledger()
    first = await upsert_canonical_entry(
        canonical_id="supersede-demo",
        topic="demo",
        question="What is DEXPulse URL?",
        answer="Old URL: https://old.example.com",
    )
    await supersede_canonical_id("supersede-demo")
    second = await upsert_canonical_entry(
        canonical_id="supersede-demo",
        topic="demo",
        question="What is DEXPulse URL?",
        answer="New URL: https://dexpulse-m3bp.onrender.com",
        supersedes=[first["id"]],
    )
    hits = await search_verified("DEXPulse URL")
    replies = [h["agent_reply"] for h in hits]
    assert any("dexpulse-m3bp" in r for r in replies)
    assert not any("old.example.com" in r for r in replies)
    assert second["id"] != first["id"]


@pytest.mark.asyncio
async def test_sync_canonical_facts_populates_ledger():
    await init_truth_ledger()
    result = await sync_canonical_facts()
    assert result["total_facts"] >= 10
    assert result["synced"] + result["unchanged"] == result["total_facts"]
    stats = await ledger_stats()
    assert stats["verified"] >= result["total_facts"]
    facts = load_canonical_facts()
    first = facts[0]
    hits = await search_verified(first["question"][:30])
    assert hits


@pytest.mark.asyncio
async def test_sync_exports_faq_yaml():
    await init_truth_ledger()
    await sync_canonical_facts()
    from pathlib import Path
    import aria_core.content.service as faq_service

    faq_path = faq_service._FAQ_PATH
    data = yaml.safe_load(faq_path.read_text(encoding="utf-8"))
    ids = {item["id"] for item in data}
    assert "aria-role" in ids
    assert "dexpulse-retired" in ids
    assert "aria-market-product" in ids


def test_canonical_facts_sync_gate_off_by_default(monkeypatch):
    monkeypatch.delenv("ARIA_CANONICAL_FACTS_SYNC_ENABLED", raising=False)
    assert canonical_facts_sync_enabled() is False


def test_canonical_facts_sync_gate_on_via_env(monkeypatch):
    monkeypatch.setenv("ARIA_CANONICAL_FACTS_SYNC_ENABLED", "1")
    assert canonical_facts_sync_enabled() is True
    monkeypatch.setenv("ARIA_CANONICAL_FACTS_SYNC_ENABLED", "true")
    assert canonical_facts_sync_enabled() is True
    monkeypatch.setenv("ARIA_CANONICAL_FACTS_SYNC_ENABLED", "0")
    assert canonical_facts_sync_enabled() is False