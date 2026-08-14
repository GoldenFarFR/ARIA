"""Tests for the vector-memory write-audit trail (#166, 14/08) -- same
fixture pattern as ``test_system_issues.py`` (own tmp-DB, ``DB_PATH``
monkeypatched, never the real prod database)."""
import pytest

from aria_core.memory.vector import audit


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(audit, "DB_PATH", str(tmp_path / "audit.db"))
    yield


@pytest.mark.asyncio
async def test_log_write_attempt_accepted_is_recorded():
    await audit.log_write_attempt("insight", "conviction_research.py", accepted=True)
    rows = await audit.recent_write_attempts()
    assert len(rows) == 1
    assert rows[0]["entry_type"] == "insight"
    assert rows[0]["written_by"] == "conviction_research.py"
    assert rows[0]["accepted"] == 1
    assert rows[0]["reason"] == ""


@pytest.mark.asyncio
async def test_log_write_attempt_rejected_keeps_reason():
    await audit.log_write_attempt(
        "insight", "evil_module.py", accepted=False, reason="injection marker detected"
    )
    rows = await audit.recent_write_attempts()
    assert rows[0]["accepted"] == 0
    assert rows[0]["reason"] == "injection marker detected"


@pytest.mark.asyncio
async def test_recent_write_attempts_most_recent_first():
    await audit.log_write_attempt("lesson", "a.py", accepted=True)
    await audit.log_write_attempt("insight", "b.py", accepted=True)
    rows = await audit.recent_write_attempts()
    assert [r["written_by"] for r in rows] == ["b.py", "a.py"]


@pytest.mark.asyncio
async def test_recent_write_attempts_respects_limit():
    for i in range(5):
        await audit.log_write_attempt("lesson", f"mod{i}.py", accepted=True)
    rows = await audit.recent_write_attempts(limit=2)
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_log_write_attempt_never_raises_on_db_failure(monkeypatch):
    async def _broken_connect(*_a, **_kw):
        raise OSError("disk full")

    monkeypatch.setattr(audit.aiosqlite, "connect", _broken_connect)
    await audit.log_write_attempt("insight", "x.py", accepted=True)  # must not raise


@pytest.mark.asyncio
async def test_recent_write_attempts_never_raises_on_db_failure(monkeypatch):
    async def _broken_connect(*_a, **_kw):
        raise OSError("disk full")

    monkeypatch.setattr(audit.aiosqlite, "connect", _broken_connect)
    assert await audit.recent_write_attempts() == []
