import pytest

from aria_core.truth_ledger.store import (
    init_truth_ledger,
    record_exchange,
    search_verified,
    verify_entry,
)


@pytest.fixture(autouse=True)
def isolated_truth_ledger(tmp_path, monkeypatch):
    """DB_PATH figé à l'import — aligner store + sync (import figé) sur un tmp par test."""
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
        monkeypatch.setattr(target, db_path if "db" in target else ledger_dir)
    monkeypatch.setattr("aria_core.truth_ledger.store.DB_PATH", str(db))
    monkeypatch.setattr("aria_core.truth_ledger.store.LEDGER_DIR", ledger)


@pytest.mark.asyncio
async def test_record_and_search_verified():
    await init_truth_ledger()
    meta = await record_exchange(
        "What is DEXPulse?",
        "DEXPulse is the flagship DEX analyzer subsidiary.",
        skill_used="faq_content",
        sources=["faq.yaml"],
    )
    assert meta["status"] == "verified"

    hits = await search_verified("DEXPulse flagship")
    assert any("DEXPulse" in h["agent_reply"] for h in hits)


@pytest.mark.asyncio
async def test_pending_llm_not_in_verified_search():
    await init_truth_ledger()
    meta = await record_exchange(
        "random question xyz",
        "some llm guess answer",
        skill_used=None,
        sources=["llm"],
    )
    assert meta["status"] == "pending"
    hits = await search_verified("random question xyz")
    assert not hits


@pytest.mark.asyncio
async def test_manual_verify():
    await init_truth_ledger()
    meta = await record_exchange(
        "pending topic abc",
        "operator will verify this",
        skill_used=None,
    )
    assert meta["status"] == "pending"
    ok = await verify_entry(meta["id"])
    assert ok is True
    hits = await search_verified("pending topic")
    assert hits