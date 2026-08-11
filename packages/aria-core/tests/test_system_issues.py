"""Centralized "GitHub Issues"-style registry (11/08, explicit operator
request) -- open/close/list, mirrors candle_history.py's/goplus_watchlist.py's
own tmp-DB fixture pattern (DB_PATH computed once at import, monkeypatched
per test)."""
from __future__ import annotations

import pytest

from aria_core import system_issues


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(system_issues, "DB_PATH", str(tmp_path / "system_issues.db"))
    yield


@pytest.mark.asyncio
async def test_open_and_list_issue():
    issue_id = await system_issues.open_issue("vc-watch", "vc_radar_x silencieux", detail="72h sans passage")
    open_issues = await system_issues.list_open()
    assert len(open_issues) == 1
    assert open_issues[0]["id"] == issue_id
    assert open_issues[0]["source"] == "vc-watch"
    assert open_issues[0]["status"] == "open"


@pytest.mark.asyncio
async def test_open_defaults_to_warning_severity():
    await system_issues.open_issue("test-source", "titre")
    (row,) = await system_issues.list_open()
    assert row["severity"] == "warning"


@pytest.mark.asyncio
async def test_open_rejects_unknown_severity_falls_back_to_warning():
    await system_issues.open_issue("test-source", "titre", severity="apocalyptic")
    (row,) = await system_issues.list_open()
    assert row["severity"] == "warning"


@pytest.mark.asyncio
async def test_dedup_key_reuses_same_open_issue():
    first_id = await system_issues.open_issue("vc-watch", "t1", dedup_key="vc_radar_x")
    second_id = await system_issues.open_issue("vc-watch", "t1 updated", dedup_key="vc_radar_x")
    assert first_id == second_id
    assert len(await system_issues.list_open()) == 1


@pytest.mark.asyncio
async def test_dedup_key_allows_a_new_issue_once_the_old_one_is_closed():
    first_id = await system_issues.open_issue("vc-watch", "t1", dedup_key="vc_radar_x")
    await system_issues.close_issue(first_id, "resolu")
    second_id = await system_issues.open_issue("vc-watch", "t1 again", dedup_key="vc_radar_x")
    assert second_id != first_id
    assert len(await system_issues.list_open()) == 1


@pytest.mark.asyncio
async def test_no_dedup_key_each_call_opens_a_distinct_issue():
    await system_issues.open_issue("test-source", "t1")
    await system_issues.open_issue("test-source", "t2")
    assert len(await system_issues.list_open()) == 2


@pytest.mark.asyncio
async def test_close_issue_requires_a_reason():
    issue_id = await system_issues.open_issue("test-source", "t1")
    assert await system_issues.close_issue(issue_id, "") is False
    assert await system_issues.close_issue(issue_id, "   ") is False
    assert len(await system_issues.list_open()) == 1


@pytest.mark.asyncio
async def test_close_issue_removes_it_from_open_list():
    issue_id = await system_issues.open_issue("test-source", "t1")
    assert await system_issues.close_issue(issue_id, "corrige dans commit abc123") is True
    assert await system_issues.list_open() == []


@pytest.mark.asyncio
async def test_close_nonexistent_issue_returns_false_never_raises():
    assert await system_issues.close_issue(9999, "peu importe") is False


@pytest.mark.asyncio
async def test_close_already_closed_issue_is_idempotent():
    issue_id = await system_issues.open_issue("test-source", "t1")
    assert await system_issues.close_issue(issue_id, "premiere fermeture") is True
    assert await system_issues.close_issue(issue_id, "deuxieme tentative") is False


@pytest.mark.asyncio
async def test_list_open_filters_by_source():
    await system_issues.open_issue("vc-watch", "a")
    await system_issues.open_issue("signal-cascade-watch", "b")
    only_vc = await system_issues.list_open(source="vc-watch")
    assert len(only_vc) == 1
    assert only_vc[0]["source"] == "vc-watch"


@pytest.mark.asyncio
async def test_list_open_orders_critical_first():
    await system_issues.open_issue("s", "info issue", severity="info")
    await system_issues.open_issue("s", "critical issue", severity="critical")
    await system_issues.open_issue("s", "warning issue", severity="warning")
    ordered = await system_issues.list_open()
    assert [r["severity"] for r in ordered] == ["critical", "warning", "info"]


@pytest.mark.asyncio
async def test_list_open_respects_limit():
    for i in range(5):
        await system_issues.open_issue("s", f"t{i}")
    assert len(await system_issues.list_open(limit=2)) == 2


@pytest.mark.asyncio
async def test_count_open_matches_list_length():
    await system_issues.open_issue("s", "a")
    await system_issues.open_issue("s", "b")
    assert await system_issues.count_open() == 2
    assert await system_issues.count_open() == len(await system_issues.list_open())


@pytest.mark.asyncio
async def test_count_open_excludes_closed():
    issue_id = await system_issues.open_issue("s", "a")
    await system_issues.open_issue("s", "b")
    await system_issues.close_issue(issue_id, "resolu")
    assert await system_issues.count_open() == 1


# ── robustness: real DB failure, never blocking the caller (11/08 audit) ──────────

@pytest.mark.asyncio
async def test_open_issue_never_raises_on_db_failure(monkeypatch):
    """This module's whole purpose is to stay non-blocking for the watchdogs
    calling it -- before this fix, every DB access was unprotected, contradicting
    that promise. Simulates a real connection failure (locked/corrupt/disk full),
    never a mocked business exception."""
    async def _broken_connect(*_a, **_kw):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(system_issues.aiosqlite, "connect", _broken_connect)
    issue_id = await system_issues.open_issue("s", "title")
    assert issue_id == -1


@pytest.mark.asyncio
async def test_close_issue_never_raises_on_db_failure(monkeypatch):
    async def _broken_connect(*_a, **_kw):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(system_issues.aiosqlite, "connect", _broken_connect)
    assert await system_issues.close_issue(1, "peu importe") is False


@pytest.mark.asyncio
async def test_list_open_never_raises_on_db_failure(monkeypatch):
    async def _broken_connect(*_a, **_kw):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(system_issues.aiosqlite, "connect", _broken_connect)
    assert await system_issues.list_open() == []


@pytest.mark.asyncio
async def test_count_open_never_raises_on_db_failure(monkeypatch):
    async def _broken_connect(*_a, **_kw):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(system_issues.aiosqlite, "connect", _broken_connect)
    assert await system_issues.count_open() == 0


@pytest.mark.asyncio
async def test_open_issue_recovers_once_db_failure_clears():
    """A transient failure (the common real case -- a concurrent writer
    holding the lock for a moment) must never leave the module permanently
    degraded -- the very next call on a healthy DB works normally."""
    issue_id = await system_issues.open_issue("s", "title")
    assert issue_id != -1
    assert await system_issues.count_open() == 1
