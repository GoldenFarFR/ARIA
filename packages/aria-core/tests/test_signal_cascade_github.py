"""Multi-source signal cascade -- GitHub column (stages 1+2). Never a
trigger, never blocks the caller (see module docstring)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import aiosqlite
import pytest

from aria_core import signal_cascade_github as scg

CONTRACT = "0x" + "a" * 40
REPO_URL = "https://github.com/testorg/testrepo"


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "signal_cascade_github.db")
    monkeypatch.setattr(scg, "DB_PATH", db_path)
    monkeypatch.setattr(scg, "_table_ready", False)
    yield


def _links(url: str | None = REPO_URL) -> list[dict]:
    return [{"label": "GitHub", "url": url}] if url else []


@pytest.mark.asyncio
async def test_enqueue_with_github_link_creates_watchlist_row():
    await scg.enqueue_candidate(CONTRACT, "base", _links(), symbol="TP")
    async with aiosqlite.connect(scg.DB_PATH) as db:
        cursor = await db.execute("SELECT repo_url, contract FROM github_signal_cascade_watchlist")
        rows = await cursor.fetchall()
    assert rows == [(REPO_URL, CONTRACT)]


@pytest.mark.asyncio
async def test_enqueue_without_github_link_does_nothing():
    await scg.enqueue_candidate(CONTRACT, "base", [{"label": "Website", "url": "https://example.com"}])
    await scg._ensure_table()  # not created by enqueue itself when there's nothing to insert
    async with aiosqlite.connect(scg.DB_PATH) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM github_signal_cascade_watchlist")
        (count,) = await cursor.fetchone()
    assert count == 0


@pytest.mark.asyncio
async def test_enqueue_is_idempotent_never_overwrites_first_seen():
    await scg.enqueue_candidate(CONTRACT, "base", _links())
    async with aiosqlite.connect(scg.DB_PATH) as db:
        await db.execute(
            "UPDATE github_signal_cascade_watchlist SET first_seen_at = 'sentinel' WHERE repo_url = ?",
            (REPO_URL,),
        )
        await db.commit()
    await scg.enqueue_candidate(CONTRACT, "base", _links())  # second sighting, same repo
    async with aiosqlite.connect(scg.DB_PATH) as db:
        cursor = await db.execute(
            "SELECT first_seen_at, COUNT(*) FROM github_signal_cascade_watchlist WHERE repo_url = ?", (REPO_URL,)
        )
        first_seen_at, count = await cursor.fetchone()
    assert first_seen_at == "sentinel"
    assert count == 1


@pytest.mark.asyncio
async def test_enqueue_never_raises_even_on_broken_db(monkeypatch):
    monkeypatch.setattr(scg, "DB_PATH", "/nonexistent/dir/shadow.db")
    monkeypatch.setattr(scg, "_table_ready", False)
    await scg.enqueue_candidate(CONTRACT, "base", _links())  # does not raise


@pytest.mark.asyncio
async def test_refresh_cycle_on_empty_watchlist_returns_none():
    result = await scg.run_refresh_cycle()
    assert result == {"evaluated": None}


def _mock_verdict(monkeypatch, *, signal: str, score: float | None = 80.0):
    import aria_core.skills.github_substance as gh

    async def _fake_gather(repo_url, **kwargs):
        return gh.GithubSubstanceFacts(available=True)

    def _fake_judge(facts):
        return gh.GithubSubstanceVerdict(signal=signal, score=score)

    monkeypatch.setattr(gh, "gather_github_substance_facts", _fake_gather)
    monkeypatch.setattr(gh, "judge_github_substance", _fake_judge)


@pytest.mark.asyncio
async def test_refresh_cycle_evaluates_and_records_signal(monkeypatch):
    await scg.enqueue_candidate(CONTRACT, "base", _links())
    _mock_verdict(monkeypatch, signal="positive", score=85.0)
    result = await scg.run_refresh_cycle()
    assert result["evaluated"] == REPO_URL
    assert result["signal"] == "positive"
    stage2 = await scg.list_stage2_positive()
    assert len(stage2) == 1
    assert stage2[0]["score"] == 85.0


@pytest.mark.asyncio
async def test_refresh_cycle_skips_recently_evaluated_repo(monkeypatch):
    await scg.enqueue_candidate(CONTRACT, "base", _links())
    _mock_verdict(monkeypatch, signal="weak", score=20.0)
    first = await scg.run_refresh_cycle()
    assert first["evaluated"] == REPO_URL

    second = await scg.run_refresh_cycle()
    assert second == {"evaluated": None}  # within TTL, nothing else to refresh


@pytest.mark.asyncio
async def test_refresh_cycle_flags_acceleration_from_weak_to_positive(monkeypatch):
    await scg.enqueue_candidate(CONTRACT, "base", _links())
    _mock_verdict(monkeypatch, signal="weak", score=20.0)
    await scg.run_refresh_cycle()

    # Age the row past the TTL so it's due again.
    old = (datetime.now(timezone.utc) - timedelta(days=scg.REEVALUATION_TTL_DAYS + 1)).isoformat()
    async with aiosqlite.connect(scg.DB_PATH) as db:
        await db.execute(
            "UPDATE github_signal_cascade_watchlist SET last_evaluated_at = ? WHERE repo_url = ?", (old, REPO_URL)
        )
        await db.commit()

    _mock_verdict(monkeypatch, signal="positive", score=90.0)
    result = await scg.run_refresh_cycle()
    assert result["accelerating"] is True
    stage2 = await scg.list_stage2_positive()
    assert stage2[0]["accelerating"] is True


@pytest.mark.asyncio
async def test_no_acceleration_flag_when_already_positive(monkeypatch):
    await scg.enqueue_candidate(CONTRACT, "base", _links())
    _mock_verdict(monkeypatch, signal="positive", score=75.0)
    await scg.run_refresh_cycle()

    old = (datetime.now(timezone.utc) - timedelta(days=scg.REEVALUATION_TTL_DAYS + 1)).isoformat()
    async with aiosqlite.connect(scg.DB_PATH) as db:
        await db.execute(
            "UPDATE github_signal_cascade_watchlist SET last_evaluated_at = ? WHERE repo_url = ?", (old, REPO_URL)
        )
        await db.commit()

    _mock_verdict(monkeypatch, signal="positive", score=88.0)
    result = await scg.run_refresh_cycle()
    assert result["accelerating"] is False


@pytest.mark.asyncio
async def test_list_stage2_positive_excludes_weak_and_unknown(monkeypatch):
    await scg.enqueue_candidate(CONTRACT, "base", _links())
    _mock_verdict(monkeypatch, signal="weak", score=10.0)
    await scg.run_refresh_cycle()
    assert await scg.list_stage2_positive() == []
