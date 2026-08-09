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
    assert result["evaluated"] is None
    assert result["evaluated_count"] == 0
    assert result["results"] == []


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
    assert result["results"][0]["evaluated"] == WEBSITE_URL
    assert result["results"][0]["signal"] == "positive"
    stage2 = await scw.list_stage2_positive()
    assert len(stage2) == 1


@pytest.mark.asyncio
async def test_refresh_cycle_skips_recently_evaluated_website(monkeypatch):
    await scw.enqueue_candidate(CONTRACT, "base", _links())
    _mock_verdict(monkeypatch, signal="weak", score=20.0)
    first = await scw.run_refresh_cycle()
    assert first["results"][0]["evaluated"] == WEBSITE_URL
    second = await scw.run_refresh_cycle()
    assert second["evaluated"] is None  # within the 7-day TTL
    assert second["results"] == []


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
    assert result["results"][0]["accelerating"] is True


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
    assert result["results"][0]["signal"] == "unknown"


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


# ── batch adaptatif -- 09/08, "reste en alerte et adapte la quantité de
# token pour que ça tienne toujours sous 7 jours" ───────────────────────────

def test_adaptive_batch_size_scales_with_backlog():
    # 168 cycles/semaine à cadence horaire (REEVALUATION_TTL_DAYS=7) --
    # un backlog de 168 tient en 1/cycle, un backlog de 500 en a besoin de 3.
    assert scw._adaptive_batch_size(168) == (1, 1)
    assert scw._adaptive_batch_size(500) == (3, 3)
    assert scw._adaptive_batch_size(0) == (0, 0)


def test_adaptive_batch_size_capped_but_reports_real_need():
    capped, needed = scw._adaptive_batch_size(10_000)
    assert capped == scw._MAX_BATCH_PER_CYCLE
    assert needed > scw._MAX_BATCH_PER_CYCLE  # le vrai besoin dépasse le plafond -- jamais caché


@pytest.mark.asyncio
async def test_refresh_cycle_processes_several_candidates_when_backlog_demands_it(monkeypatch):
    """Preuve directe de la demande opérateur : un backlog suffisamment
    grand fait sortir PLUSIEURS candidats en un seul appel, jamais 1 fixe.
    200 candidats / 168 cycles (7j à cadence horaire) -> besoin de 2/cycle."""
    for i in range(200):
        await scw.enqueue_candidate(f"0x{i:040x}", "base", _links(url=f"https://project{i}.io"))
    _mock_verdict(monkeypatch, signal="neutral", score=50.0)

    result = await scw.run_refresh_cycle()

    assert result["pending_before"] == 200
    assert result["evaluated_count"] == 2
    assert len(result["results"]) == 2


@pytest.mark.asyncio
async def test_refresh_cycle_logs_warning_when_real_need_exceeds_cap(monkeypatch, caplog):
    monkeypatch.setattr(scw, "_MAX_BATCH_PER_CYCLE", 1)
    for i in range(200):
        await scw.enqueue_candidate(f"0x{i:040x}", "base", _links(url=f"https://project{i}.io"))
    _mock_verdict(monkeypatch, signal="neutral", score=50.0)

    with caplog.at_level("WARNING"):
        await scw.run_refresh_cycle()

    assert any("capped at" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_refresh_cycle_never_processes_more_than_one_at_a_time_concurrently(monkeypatch):
    """Le batch est plus grand, mais chaque évaluation reste séquentielle --
    jamais deux appels _evaluate_one concurrents (doctrine "jamais plusieurs
    en même temps")."""
    monkeypatch.setattr(scw, "_MAX_BATCH_PER_CYCLE", 3)
    for i in range(10):
        await scw.enqueue_candidate(f"0x{i:040x}", "base", _links(url=f"https://project{i}.io"))
    _mock_verdict(monkeypatch, signal="neutral", score=50.0)

    concurrent = {"count": 0, "max_seen": 0}
    real_evaluate_one = scw._evaluate_one

    async def _tracking_evaluate_one(*args, **kwargs):
        concurrent["count"] += 1
        concurrent["max_seen"] = max(concurrent["max_seen"], concurrent["count"])
        result = await real_evaluate_one(*args, **kwargs)
        concurrent["count"] -= 1
        return result

    monkeypatch.setattr(scw, "_evaluate_one", _tracking_evaluate_one)
    await scw.run_refresh_cycle()

    assert concurrent["max_seen"] == 1
