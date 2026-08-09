"""Multi-source signal cascade -- Farcaster column (stages 1+2). Never a
trigger, never blocks the caller (see module docstring)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import aiosqlite
import pytest

from aria_core import signal_cascade_convergence as scc
from aria_core import signal_cascade_farcaster as scf

CONTRACT = "0x" + "a" * 40
PROFILE_URL = "https://warpcast.com/testproject"


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "signal_cascade_farcaster.db")
    monkeypatch.setattr(scf, "DB_PATH", db_path)
    monkeypatch.setattr(scf, "_table_ready", False)
    monkeypatch.setattr(scc, "DB_PATH", db_path)
    monkeypatch.setattr(scc, "_table_ready", False)
    yield


def _links(url: str | None = PROFILE_URL) -> list[dict]:
    return [{"label": "Farcaster", "url": url}] if url else []


@pytest.mark.asyncio
async def test_enqueue_with_farcaster_link_creates_watchlist_row():
    await scf.enqueue_candidate(CONTRACT, "base", _links(), symbol="TP")
    async with aiosqlite.connect(scf.DB_PATH) as db:
        cursor = await db.execute("SELECT profile_url, contract FROM farcaster_signal_cascade_watchlist")
        rows = await cursor.fetchall()
    assert rows == [(PROFILE_URL, CONTRACT)]


@pytest.mark.asyncio
async def test_enqueue_ignores_non_farcaster_links():
    await scf.enqueue_candidate(CONTRACT, "base", [{"label": "X (Twitter)", "url": "https://x.com/test"}])
    await scf._ensure_table()
    async with aiosqlite.connect(scf.DB_PATH) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM farcaster_signal_cascade_watchlist")
        (count,) = await cursor.fetchone()
    assert count == 0


@pytest.mark.asyncio
async def test_enqueue_never_raises_even_on_broken_db(monkeypatch):
    monkeypatch.setattr(scf, "DB_PATH", "/nonexistent/dir/shadow.db")
    monkeypatch.setattr(scf, "_table_ready", False)
    await scf.enqueue_candidate(CONTRACT, "base", _links())  # does not raise


@pytest.mark.asyncio
async def test_refresh_cycle_on_empty_watchlist_returns_none():
    result = await scf.run_refresh_cycle()
    assert result == {"evaluated": None}


def _mock_verification(monkeypatch, *, exists=True, followers=0, spam=None, available=True):
    import aria_core.services.farcaster as fc

    async def _fake_verify(url):
        return fc.FarcasterProfileVerification(
            available=available, exists=exists, follower_count=followers, spam_label=spam,
        )

    monkeypatch.setattr(fc, "verify_profile", _fake_verify)


@pytest.mark.asyncio
async def test_refresh_cycle_judges_positive_above_follower_floor(monkeypatch):
    await scf.enqueue_candidate(CONTRACT, "base", _links())
    _mock_verification(monkeypatch, exists=True, followers=scf.MIN_FOLLOWERS + 50, spam=None)
    result = await scf.run_refresh_cycle()
    assert result["signal"] == "positive"
    stage2 = await scf.list_stage2_positive()
    assert len(stage2) == 1


@pytest.mark.asyncio
async def test_refresh_cycle_judges_weak_on_spam_label(monkeypatch):
    await scf.enqueue_candidate(CONTRACT, "base", _links())
    _mock_verification(monkeypatch, exists=True, followers=10_000, spam="spam")
    result = await scf.run_refresh_cycle()
    assert result["signal"] == "weak"


@pytest.mark.asyncio
async def test_refresh_cycle_judges_neutral_below_follower_floor(monkeypatch):
    await scf.enqueue_candidate(CONTRACT, "base", _links())
    _mock_verification(monkeypatch, exists=True, followers=5, spam=None)
    result = await scf.run_refresh_cycle()
    assert result["signal"] == "neutral"


@pytest.mark.asyncio
async def test_refresh_cycle_judges_unknown_on_dead_link(monkeypatch):
    await scf.enqueue_candidate(CONTRACT, "base", _links())
    _mock_verification(monkeypatch, exists=False)
    result = await scf.run_refresh_cycle()
    assert result["signal"] == "unknown"


@pytest.mark.asyncio
async def test_refresh_cycle_skips_recently_evaluated_profile(monkeypatch):
    await scf.enqueue_candidate(CONTRACT, "base", _links())
    _mock_verification(monkeypatch, exists=True, followers=5, spam=None)
    first = await scf.run_refresh_cycle()
    assert first["evaluated"] == PROFILE_URL
    second = await scf.run_refresh_cycle()
    assert second == {"evaluated": None}


@pytest.mark.asyncio
async def test_refresh_cycle_flags_acceleration_from_neutral_to_positive(monkeypatch):
    await scf.enqueue_candidate(CONTRACT, "base", _links())
    _mock_verification(monkeypatch, exists=True, followers=5, spam=None)
    await scf.run_refresh_cycle()

    old = (datetime.now(timezone.utc) - timedelta(days=scf.REEVALUATION_TTL_DAYS + 1)).isoformat()
    async with aiosqlite.connect(scf.DB_PATH) as db:
        await db.execute(
            "UPDATE farcaster_signal_cascade_watchlist SET last_evaluated_at = ? WHERE profile_url = ?",
            (old, PROFILE_URL),
        )
        await db.commit()

    _mock_verification(monkeypatch, exists=True, followers=scf.MIN_FOLLOWERS + 10, spam=None)
    result = await scf.run_refresh_cycle()
    assert result["accelerating"] is False  # neutral -> positive is NOT an acceleration case (only weak/unknown/none is)


@pytest.mark.asyncio
async def test_refresh_cycle_flags_acceleration_from_unknown_to_positive(monkeypatch):
    await scf.enqueue_candidate(CONTRACT, "base", _links())
    _mock_verification(monkeypatch, exists=False)
    await scf.run_refresh_cycle()

    old = (datetime.now(timezone.utc) - timedelta(days=scf.REEVALUATION_TTL_DAYS + 1)).isoformat()
    async with aiosqlite.connect(scf.DB_PATH) as db:
        await db.execute(
            "UPDATE farcaster_signal_cascade_watchlist SET last_evaluated_at = ? WHERE profile_url = ?",
            (old, PROFILE_URL),
        )
        await db.commit()

    _mock_verification(monkeypatch, exists=True, followers=scf.MIN_FOLLOWERS + 10, spam=None)
    result = await scf.run_refresh_cycle()
    assert result["accelerating"] is True


@pytest.mark.asyncio
async def test_positive_signal_reaches_stage3_convergence_and_queue(monkeypatch):
    await scf.enqueue_candidate(CONTRACT, "base", _links(), symbol="TP")
    _mock_verification(monkeypatch, exists=True, followers=scf.MIN_FOLLOWERS + 50, spam=None)
    await scf.run_refresh_cycle()

    pending = await scc.list_pending_triage()
    assert len(pending) == 1
    assert pending[0]["sources"][0]["source"] == "farcaster"
