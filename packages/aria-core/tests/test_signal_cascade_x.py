"""Multi-source signal cascade -- X column (stages 1+2). Never a trigger,
never blocks the caller (see module docstring). Dedicated weekly budget,
separate from x_research_budget.py -- must never let this column starve
conviction_research's own existing use of x_substance."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import aiosqlite
import pytest

from aria_core import signal_cascade_convergence as scc
from aria_core import signal_cascade_x as scx

CONTRACT = "0x" + "a" * 40
X_URL = "https://x.com/testproject"


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "signal_cascade_x.db")
    monkeypatch.setattr(scx, "DB_PATH", db_path)
    monkeypatch.setattr(scx, "_table_ready", False)
    monkeypatch.setattr(scc, "DB_PATH", db_path)
    monkeypatch.setattr(scc, "_table_ready", False)
    yield


def _links(url: str | None = X_URL, label: str = "X (Twitter)") -> list[dict]:
    return [{"label": label, "url": url}] if url else []


@pytest.mark.asyncio
async def test_enqueue_extracts_handle_from_x_link():
    await scx.enqueue_candidate(CONTRACT, "base", _links(), symbol="TP")
    async with aiosqlite.connect(scx.DB_PATH) as db:
        cursor = await db.execute("SELECT x_handle, contract FROM x_signal_cascade_watchlist")
        rows = await cursor.fetchall()
    assert rows == [("testproject", CONTRACT)]


@pytest.mark.asyncio
async def test_enqueue_extracts_handle_from_legacy_twitter_domain():
    await scx.enqueue_candidate(CONTRACT, "base", _links(url="https://twitter.com/testproject"))
    async with aiosqlite.connect(scx.DB_PATH) as db:
        cursor = await db.execute("SELECT x_handle FROM x_signal_cascade_watchlist")
        rows = await cursor.fetchall()
    assert rows == [("testproject",)]


@pytest.mark.asyncio
async def test_enqueue_ignores_non_x_links():
    await scx.enqueue_candidate(CONTRACT, "base", [{"label": "GitHub", "url": "https://github.com/test/test"}])
    await scx._ensure_tables()
    async with aiosqlite.connect(scx.DB_PATH) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM x_signal_cascade_watchlist")
        (count,) = await cursor.fetchone()
    assert count == 0


@pytest.mark.asyncio
async def test_enqueue_never_raises_even_on_broken_db(monkeypatch):
    monkeypatch.setattr(scx, "DB_PATH", "/nonexistent/dir/shadow.db")
    monkeypatch.setattr(scx, "_table_ready", False)
    await scx.enqueue_candidate(CONTRACT, "base", _links())  # does not raise


@pytest.mark.asyncio
async def test_refresh_cycle_on_empty_watchlist_returns_none():
    result = await scx.run_refresh_cycle()
    assert result == {"evaluated": None}


def _mock_verdict(monkeypatch, *, signal: str, score: float | None = 80.0, available: bool = True):
    import aria_core.skills.x_substance as xs

    async def _fake_gather(handle, **kwargs):
        return xs.XSubstanceFacts(available=available)

    def _fake_judge(facts):
        return xs.XSubstanceVerdict(signal=signal, score=score)

    monkeypatch.setattr(xs, "gather_x_substance_facts", _fake_gather)
    monkeypatch.setattr(xs, "judge_x_substance", _fake_judge)


@pytest.mark.asyncio
async def test_refresh_cycle_evaluates_and_records_signal(monkeypatch):
    await scx.enqueue_candidate(CONTRACT, "base", _links())
    _mock_verdict(monkeypatch, signal="positive", score=85.0)
    result = await scx.run_refresh_cycle()
    assert result["evaluated"] == "testproject"
    assert result["signal"] == "positive"
    stage2 = await scx.list_stage2_positive()
    assert len(stage2) == 1


@pytest.mark.asyncio
async def test_refresh_cycle_skips_recently_evaluated_handle(monkeypatch):
    await scx.enqueue_candidate(CONTRACT, "base", _links())
    _mock_verdict(monkeypatch, signal="weak", score=20.0)
    first = await scx.run_refresh_cycle()
    assert first["evaluated"] == "testproject"
    second = await scx.run_refresh_cycle()
    assert second == {"evaluated": None}  # within the 30-day TTL


@pytest.mark.asyncio
async def test_refresh_cycle_flags_acceleration_from_weak_to_positive(monkeypatch):
    await scx.enqueue_candidate(CONTRACT, "base", _links())
    _mock_verdict(monkeypatch, signal="weak", score=20.0)
    await scx.run_refresh_cycle()

    old = (datetime.now(timezone.utc) - timedelta(days=scx.REEVALUATION_TTL_DAYS + 1)).isoformat()
    async with aiosqlite.connect(scx.DB_PATH) as db:
        await db.execute(
            "UPDATE x_signal_cascade_watchlist SET last_evaluated_at = ? WHERE x_handle = ?",
            (old, "testproject"),
        )
        await db.commit()

    _mock_verdict(monkeypatch, signal="positive", score=90.0)
    result = await scx.run_refresh_cycle()
    assert result["accelerating"] is True


@pytest.mark.asyncio
async def test_positive_signal_reaches_stage3_convergence_and_queue(monkeypatch):
    await scx.enqueue_candidate(CONTRACT, "base", _links(), symbol="TP")
    _mock_verdict(monkeypatch, signal="positive", score=91.0)
    await scx.run_refresh_cycle()

    pending = await scc.list_pending_triage()
    assert len(pending) == 1
    assert pending[0]["sources"][0]["source"] == "x"


# ---- dedicated weekly budget -----------------------------------------

@pytest.mark.asyncio
async def test_can_spend_true_when_under_cap():
    assert await scx.can_spend() is True


@pytest.mark.asyncio
async def test_refresh_cycle_stops_once_weekly_cap_reached(monkeypatch):
    _mock_verdict(monkeypatch, signal="positive", score=80.0)
    for i in range(scx.WEEKLY_REQUEST_CAP):
        await scx.enqueue_candidate(f"0x{i:040x}", "base", _links(url=f"https://x.com/proj{i}"))
        result = await scx.run_refresh_cycle()
        assert result["evaluated"] is not None  # still under cap

    assert await scx.can_spend() is False

    # One more candidate queued -- the cap must block it, never overspend.
    await scx.enqueue_candidate("0x" + "f" * 40, "base", _links(url="https://x.com/oneMore"))
    blocked = await scx.run_refresh_cycle()
    assert blocked["evaluated"] is None
    assert "budget" in blocked.get("reason", "")


@pytest.mark.asyncio
async def test_only_real_attempts_count_against_the_budget():
    """An empty-watchlist pass or a budget-skipped pass must never itself
    record a spend -- only an actual evaluation attempt does."""
    await scx.run_refresh_cycle()  # empty watchlist, nothing to spend on
    assert await scx._used_this_week() == 0


@pytest.mark.asyncio
async def test_dedicated_budget_never_touches_x_research_budget_table(monkeypatch):
    """This column's spend log must be its own table -- never shares state
    with x_research_budget.py's conviction_research pool."""
    await scx.enqueue_candidate(CONTRACT, "base", _links())
    _mock_verdict(monkeypatch, signal="positive", score=80.0)
    await scx.run_refresh_cycle()

    async with aiosqlite.connect(scx.DB_PATH) as db:
        cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='x_research_request_log'"
        )
        row = await cursor.fetchone()
    assert row is None  # never created/touched by this module's own DB
