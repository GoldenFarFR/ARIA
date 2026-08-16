"""Multi-source signal cascade -- X column (stages 1+2). Never a trigger,
never blocks the caller (see module docstring). No spend cap (removed
09/08 on explicit operator instruction) -- the spend LOG stays separate
from x_research_budget.py, purely for traceability now, never to gate."""
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


def test_extract_x_handle_truncates_oversized_url_before_regex():
    """Une URL demesuree (au-dela de _MAX_URL_CHARS) doit etre tronquee AVANT
    la regex -- place le vrai handle apres la coupure et confirme qu'il n'est
    PAS trouve, preuve que la troncature agit (pas juste presente)."""
    oversized_url = ("z" * (scx._MAX_URL_CHARS + 1000)) + "x.com/realhandle"
    assert scx._extract_x_handle([{"label": "X", "url": oversized_url}]) is None


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
    assert result["evaluated"] is None
    assert result["evaluated_count"] == 0
    assert result["results"] == []


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
    assert result["results"][0]["signal"] == "positive"
    stage2 = await scx.list_stage2_positive()
    assert len(stage2) == 1


@pytest.mark.asyncio
async def test_refresh_cycle_skips_recently_evaluated_handle(monkeypatch):
    await scx.enqueue_candidate(CONTRACT, "base", _links())
    _mock_verdict(monkeypatch, signal="weak", score=20.0)
    first = await scx.run_refresh_cycle()
    assert first["evaluated"] == "testproject"
    second = await scx.run_refresh_cycle()
    assert second["evaluated"] is None  # within the TTL (REEVALUATION_TTL_DAYS)
    assert second["results"] == []


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
    assert result["results"][0]["accelerating"] is True


@pytest.mark.asyncio
async def test_positive_signal_reaches_stage3_convergence_and_queue(monkeypatch):
    await scx.enqueue_candidate(CONTRACT, "base", _links(), symbol="TP")
    _mock_verdict(monkeypatch, signal="positive", score=91.0)
    await scx.run_refresh_cycle()

    pending = await scc.list_pending_triage()
    assert len(pending) == 1
    assert pending[0]["sources"][0]["source"] == "x"


# ---- spend log (traceability only, never a gate since 09/08) --------

@pytest.mark.asyncio
async def test_can_spend_always_true_no_cap_anymore():
    """09/08, explicit operator instruction ("enlève cette limite et
    laisse tourner") -- the dedicated 15/week cap is gone, can_spend never
    refuses."""
    assert await scx.can_spend() is True


@pytest.mark.asyncio
async def test_refresh_cycle_never_blocked_by_a_spend_ceiling(monkeypatch):
    """Regression for the removed cap: 20 real evaluations (well past the
    old WEEKLY_REQUEST_CAP=15) must ALL go through, never a budget-reason
    skip."""
    _mock_verdict(monkeypatch, signal="positive", score=80.0)
    for i in range(20):
        await scx.enqueue_candidate(f"0x{i:040x}", "base", _links(url=f"https://x.com/proj{i}"))
        result = await scx.run_refresh_cycle()
        assert result["evaluated"] is not None

    assert await scx.can_spend() is True
    status = await scx.weekly_spend_status()
    assert status["spent_this_week"] == 20


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


# ── batch adaptatif -- 09/08, "reste en alerte et adapte la quantité de
# token pour que ça tienne toujours sous 7 jours" ───────────────────────────

def test_adaptive_batch_size_scales_with_backlog():
    assert scx._adaptive_batch_size(168) == (1, 1)
    assert scx._adaptive_batch_size(500) == (3, 3)
    assert scx._adaptive_batch_size(0) == (0, 0)


def test_adaptive_batch_size_capped_but_reports_real_need():
    capped, needed = scx._adaptive_batch_size(10_000)
    assert capped == scx._MAX_BATCH_PER_CYCLE
    assert needed > scx._MAX_BATCH_PER_CYCLE


@pytest.mark.asyncio
async def test_refresh_cycle_processes_several_candidates_when_backlog_demands_it(monkeypatch):
    """200 candidats / 168 cycles (7j à cadence horaire) -> besoin de 2/cycle."""
    for i in range(200):
        await scx.enqueue_candidate(f"0x{i:040x}", "base", _links(url=f"https://x.com/proj{i}"))
    _mock_verdict(monkeypatch, signal="neutral", score=50.0)

    result = await scx.run_refresh_cycle()

    assert result["pending_before"] == 200
    assert result["evaluated_count"] == 2
    assert len(result["results"]) == 2
