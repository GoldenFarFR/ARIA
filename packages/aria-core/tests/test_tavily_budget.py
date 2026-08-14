"""Suivi du budget de crédits Tavily -- 900 crédits/mois (90% de 1000, plan
"Researcher"). Fenêtre MENSUELLE (pas journalière comme Blockscout) -- même
patron que ``test_blockscout_credit_budget.py``, adapté au "use it or lose
it" mensuel réel de ce fournisseur."""
from __future__ import annotations

import pytest

from aria_core.services import resource_budget
from aria_core.services import tavily_budget as budget


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    # 13/08 (#302): tavily_budget.py now delegates to resource_budget.py, the
    # unified ledger -- the DB path is resolved there, not in this module
    # anymore (which no longer imports aria_db_path at all).
    monkeypatch.setattr(resource_budget, "aria_db_path", lambda: tmp_path / "tavily_budget_test.db")
    yield


@pytest.mark.asyncio
async def test_empty_log_starts_with_full_budget():
    status = await budget.monthly_status()
    assert status["cap_credits"] == 900
    assert status["spent_credits"] == 0
    assert status["remaining_credits"] == 900


@pytest.mark.asyncio
async def test_db_path_resolved_dynamically_not_cached_at_import(tmp_path, monkeypatch):
    """27/07 -- regression test for the real cost bug: DB_PATH used to be a
    module-level constant computed ONCE at import time, so a SECOND test-like
    reconfiguration of aria_db_path within the same process never took
    effect -- every test in the suite silently shared the FIRST resolved
    path (in production, this meant every test run accumulated real spend
    in one persistent `.aria-test-data/aria.db`, eventually exhausting the
    900-credit cap and failing unrelated tests non-deterministically).
    Proves the fix: spending recorded under one path must NEVER be visible
    once aria_db_path is reconfigured to a different path."""
    first_path = tmp_path / "first.db"
    second_path = tmp_path / "second.db"

    monkeypatch.setattr(resource_budget, "aria_db_path", lambda: first_path)
    await budget.record_spend(caller="test", query="first db", credits=500)
    assert await budget.spent_this_month() == 500

    monkeypatch.setattr(resource_budget, "aria_db_path", lambda: second_path)
    # A fresh path must start at zero spend -- the 500 credits above must
    # never leak across, proving DB_PATH is resolved fresh on every call.
    assert await budget.spent_this_month() == 0


@pytest.mark.asyncio
async def test_can_spend_within_cap():
    assert await budget.can_spend(1) is True
    assert await budget.can_spend(900) is True
    assert await budget.can_spend(901) is False


@pytest.mark.asyncio
async def test_can_spend_rejects_non_positive_amounts():
    assert await budget.can_spend(0) is False
    assert await budget.can_spend(-1) is False


@pytest.mark.asyncio
async def test_recorded_spend_reduces_remaining_budget():
    await budget.record_spend(caller="test", query="hello world", credits=1)
    status = await budget.monthly_status()
    assert status["spent_credits"] == 1
    assert status["remaining_credits"] == 899


@pytest.mark.asyncio
async def test_hard_cap_never_exceeded_across_multiple_spends():
    for _ in range(899):
        await budget.record_spend(credits=1)
    assert await budget.can_spend(1) is True
    await budget.record_spend(credits=1)
    assert await budget.can_spend(1) is False
    status = await budget.monthly_status()
    assert status["remaining_credits"] == 0


@pytest.mark.asyncio
async def test_month_start_is_first_of_month_utc():
    from datetime import datetime, timezone

    ref = datetime(2026, 7, 22, 15, 30, tzinfo=timezone.utc)
    start = budget.month_start(ref)
    assert start == datetime(2026, 7, 1, 0, 0, tzinfo=timezone.utc)


def test_cost_for_search_basic_is_1_credit():
    assert budget.cost_for_search("basic") == 1
    assert budget.cost_for_search("") == 1
    assert budget.cost_for_search("bogus") == 1


def test_cost_for_search_advanced_is_2_credits():
    assert budget.cost_for_search("advanced") == 2
    assert budget.cost_for_search("ADVANCED") == 2


@pytest.mark.asyncio
async def test_recent_searches_returns_traceability_log():
    await budget.record_spend(caller="tavily_learning", query="Fed rate decision crypto", credits=1)
    await budget.record_spend(caller="web_verify", query="latest Base ecosystem news", credits=1)

    recent = await budget.recent_searches(limit=10)
    assert len(recent) == 2
    # Ordre du plus récent au plus ancien.
    assert recent[0]["caller"] == "web_verify"
    assert recent[0]["query"] == "latest Base ecosystem news"
    assert recent[1]["caller"] == "tavily_learning"


@pytest.mark.asyncio
async def test_recent_searches_truncates_long_query():
    await budget.record_spend(caller="test", query="x" * 500, credits=1)
    recent = await budget.recent_searches(limit=1)
    assert len(recent[0]["query"]) == 300


# ── extract()/crawl() (23/07 -- routage lecture X + Website/Docs Substance) ──


def test_cost_for_extract_batches_of_5_urls():
    assert budget.cost_for_extract("basic", 1) == 1
    assert budget.cost_for_extract("basic", 5) == 1
    assert budget.cost_for_extract("basic", 6) == 2
    assert budget.cost_for_extract("advanced", 1) == 2
    assert budget.cost_for_extract("advanced", 10) == 4


def test_cost_for_extract_zero_urls_never_free():
    assert budget.cost_for_extract("basic", 0) == 1


def test_cost_for_crawl_combines_mapping_and_extraction():
    # 10 pages, basic : 1 (mapping, 10/10) + 2 (extraction, 10/5) = 3
    assert budget.cost_for_crawl("basic", 10) == 3
    # 15 pages, basic : ceil(15/10)=2 (mapping) + ceil(15/5)=3 (extraction) = 5
    assert budget.cost_for_crawl("basic", 15) == 5
    # 10 pages, advanced : 1 (mapping) + 4 (extraction, 2*10/5) = 5
    assert budget.cost_for_crawl("advanced", 10) == 5


def test_estimate_crawl_worst_case_matches_cost_for_crawl_on_limit():
    assert budget.estimate_crawl_worst_case("basic", 15) == budget.cost_for_crawl("basic", 15)
