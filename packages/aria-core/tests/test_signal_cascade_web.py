"""Multi-source signal cascade -- web column (stages 1+2). Never a
trigger, never blocks the caller (see module docstring)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import aiosqlite
import pytest

from aria_core import signal_cascade_convergence as scc
from aria_core import signal_cascade_web as scw

CONTRACT = "0x" + "a" * 40
WEBSITE_URL = "https://testproject.io"


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "signal_cascade_web.db")
    monkeypatch.setattr(scw, "DB_PATH", db_path)
    monkeypatch.setattr(scw, "_table_ready", False)
    monkeypatch.setattr(scc, "DB_PATH", db_path)
    monkeypatch.setattr(scc, "_table_ready", False)
    yield


def _links(url: str | None = WEBSITE_URL, label: str = "Website") -> list[dict]:
    return [{"label": label, "url": url}] if url else []


@pytest.mark.asyncio
async def test_enqueue_with_website_link_creates_watchlist_row():
    await scw.enqueue_candidate(CONTRACT, "base", _links(), symbol="TP")
    async with aiosqlite.connect(scw.DB_PATH) as db:
        cursor = await db.execute("SELECT website_url, contract FROM web_signal_cascade_watchlist")
        rows = await cursor.fetchall()
    assert rows == [(WEBSITE_URL, CONTRACT)]


@pytest.mark.asyncio
async def test_enqueue_recognizes_label_case_insensitively():
    await scw.enqueue_candidate(CONTRACT, "base", _links(label="Site officiel (Website)"), symbol="TP")
    async with aiosqlite.connect(scw.DB_PATH) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM web_signal_cascade_watchlist")
        (count,) = await cursor.fetchone()
    assert count == 1


@pytest.mark.asyncio
async def test_enqueue_ignores_non_website_links():
    await scw.enqueue_candidate(CONTRACT, "base", [{"label": "Docs", "url": "https://docs.example.com"}])
    await scw._ensure_table()
    async with aiosqlite.connect(scw.DB_PATH) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM web_signal_cascade_watchlist")
        (count,) = await cursor.fetchone()
    assert count == 0


@pytest.mark.asyncio
async def test_enqueue_never_raises_even_on_broken_db(monkeypatch):
    monkeypatch.setattr(scw, "DB_PATH", "/nonexistent/dir/shadow.db")
    monkeypatch.setattr(scw, "_table_ready", False)
    await scw.enqueue_candidate(CONTRACT, "base", _links())  # does not raise


@pytest.mark.asyncio
async def test_refresh_cycle_on_empty_watchlist_returns_none():
    result = await scw.run_refresh_cycle()
    assert result == {"evaluated": None}


def _mock_verdict(monkeypatch, *, signal: str, score: float | None = 80.0):
    import aria_core.skills.website_substance as ws

    async def _fake_gather(url, **kwargs):
        return ws.WebsiteSubstanceFacts(available=True)

    def _fake_judge(facts):
        return ws.WebsiteSubstanceVerdict(signal=signal, score=score)

    monkeypatch.setattr(ws, "gather_website_substance_facts", _fake_gather)
    monkeypatch.setattr(ws, "judge_website_substance", _fake_judge)


@pytest.mark.asyncio
async def test_refresh_cycle_evaluates_and_records_signal(monkeypatch):
    await scw.enqueue_candidate(CONTRACT, "base", _links())
    _mock_verdict(monkeypatch, signal="positive", score=85.0)
    result = await scw.run_refresh_cycle()
    assert result["evaluated"] == WEBSITE_URL
    assert result["signal"] == "positive"
    stage2 = await scw.list_stage2_positive()
    assert len(stage2) == 1


@pytest.mark.asyncio
async def test_refresh_cycle_skips_recently_evaluated_website(monkeypatch):
    await scw.enqueue_candidate(CONTRACT, "base", _links())
    _mock_verdict(monkeypatch, signal="weak", score=20.0)
    first = await scw.run_refresh_cycle()
    assert first["evaluated"] == WEBSITE_URL
    second = await scw.run_refresh_cycle()
    assert second == {"evaluated": None}  # within the 15-day TTL


@pytest.mark.asyncio
async def test_refresh_cycle_flags_acceleration_from_weak_to_positive(monkeypatch):
    await scw.enqueue_candidate(CONTRACT, "base", _links())
    _mock_verdict(monkeypatch, signal="weak", score=20.0)
    await scw.run_refresh_cycle()

    old = (datetime.now(timezone.utc) - timedelta(days=scw.REEVALUATION_TTL_DAYS + 1)).isoformat()
    async with aiosqlite.connect(scw.DB_PATH) as db:
        await db.execute(
            "UPDATE web_signal_cascade_watchlist SET last_evaluated_at = ? WHERE website_url = ?",
            (old, WEBSITE_URL),
        )
        await db.commit()

    _mock_verdict(monkeypatch, signal="positive", score=90.0)
    result = await scw.run_refresh_cycle()
    assert result["accelerating"] is True


@pytest.mark.asyncio
async def test_budget_exhausted_crawl_degrades_to_unknown_never_raises(monkeypatch):
    """A fail-closed Tavily budget (tavily_budget.can_spend == False)
    surfaces as available=False through gather_website_substance_facts --
    this cycle must treat it exactly like any other unreachable source."""
    await scw.enqueue_candidate(CONTRACT, "base", _links())

    import aria_core.skills.website_substance as ws

    async def _budget_exhausted(url, **kwargs):
        return ws.WebsiteSubstanceFacts(available=False, error="budget mensuel épuisé")

    monkeypatch.setattr(ws, "gather_website_substance_facts", _budget_exhausted)
    result = await scw.run_refresh_cycle()
    assert result["signal"] == "unknown"


@pytest.mark.asyncio
async def test_positive_signal_reaches_stage3_convergence_and_queue(monkeypatch):
    await scw.enqueue_candidate(CONTRACT, "base", _links(), symbol="TP")
    _mock_verdict(monkeypatch, signal="positive", score=91.0)
    await scw.run_refresh_cycle()

    pending = await scc.list_pending_triage()
    assert len(pending) == 1
    assert pending[0]["sources"][0]["source"] == "web"


# ── contract_confirmed -- gate anti-usurpation (09/08) ──────────────────────


def _mock_verdict_with_confirmation(monkeypatch, *, confirmed: bool | None, signal="positive"):
    import aria_core.skills.website_substance as ws

    async def _fake_gather(url, **kwargs):
        return ws.WebsiteSubstanceFacts(available=True, contract_confirmed=confirmed)

    def _fake_judge(facts):
        return ws.WebsiteSubstanceVerdict(signal=signal, score=80.0)

    monkeypatch.setattr(ws, "gather_website_substance_facts", _fake_gather)
    monkeypatch.setattr(ws, "judge_website_substance", _fake_judge)


@pytest.mark.asyncio
async def test_refresh_cycle_stores_contract_confirmed_true(monkeypatch):
    await scw.enqueue_candidate(CONTRACT, "base", _links())
    _mock_verdict_with_confirmation(monkeypatch, confirmed=True)
    await scw.run_refresh_cycle()

    async with aiosqlite.connect(scw.DB_PATH) as db:
        cursor = await db.execute(
            "SELECT contract_confirmed FROM web_signal_cascade_watchlist WHERE website_url = ?",
            (WEBSITE_URL,),
        )
        (stored,) = await cursor.fetchone()
    assert stored == 1

    stage2 = await scw.list_stage2_positive()
    assert stage2[0]["contract_confirmed"] is True


@pytest.mark.asyncio
async def test_refresh_cycle_propagates_confirmed_false_to_convergence(monkeypatch):
    await scw.enqueue_candidate(CONTRACT, "base", _links())
    _mock_verdict_with_confirmation(monkeypatch, confirmed=False)
    await scw.run_refresh_cycle()

    pending = await scc.list_pending_triage()
    assert pending[0]["contract_confirmed_on_site"] is False


@pytest.mark.asyncio
async def test_refresh_cycle_leaves_confirmed_none_when_unchecked(monkeypatch):
    await scw.enqueue_candidate(CONTRACT, "base", _links())
    _mock_verdict_with_confirmation(monkeypatch, confirmed=None)
    await scw.run_refresh_cycle()

    pending = await scc.list_pending_triage()
    assert pending[0]["contract_confirmed_on_site"] is None
