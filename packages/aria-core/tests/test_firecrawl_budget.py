"""Suivi du budget de crédits Firecrawl -- 90 000 crédits/mois (90% du plan
Standard supposé, 100 000 crédits/mois, 1 crédit/page markdown). Fenêtre
MENSUELLE, même patron que test_tavily_budget.py."""
from __future__ import annotations

import pytest

from aria_core.services import firecrawl_budget as budget


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    # Même doctrine que test_tavily_budget.py (27/07) : aria_db_path résolu
    # dynamiquement à chaque appel, jamais un chemin figé à l'import -- le
    # patch cible le nom importé dans le module, pas une constante gelée.
    monkeypatch.setattr(budget, "aria_db_path", lambda: tmp_path / "firecrawl_budget_test.db")
    yield


@pytest.mark.asyncio
async def test_empty_log_starts_with_full_budget():
    status = await budget.monthly_status()
    assert status["cap_credits"] == 90_000
    assert status["spent_credits"] == 0
    assert status["remaining_credits"] == 90_000


@pytest.mark.asyncio
async def test_db_path_resolved_dynamically_not_cached_at_import(tmp_path, monkeypatch):
    """Régression pour le même bug réel déjà trouvé côté Tavily (27/07) --
    un DB_PATH figé à l'import ferait fuiter la dépense d'un test à l'autre."""
    first_path = tmp_path / "first.db"
    second_path = tmp_path / "second.db"

    monkeypatch.setattr(budget, "aria_db_path", lambda: first_path)
    await budget.record_spend(caller="test", query="first db", credits=500)
    assert await budget.spent_this_month() == 500

    monkeypatch.setattr(budget, "aria_db_path", lambda: second_path)
    assert await budget.spent_this_month() == 0


@pytest.mark.asyncio
async def test_can_spend_within_cap():
    assert await budget.can_spend(1) is True
    assert await budget.can_spend(90_000) is True
    assert await budget.can_spend(90_001) is False


@pytest.mark.asyncio
async def test_can_spend_rejects_non_positive_amounts():
    assert await budget.can_spend(0) is False
    assert await budget.can_spend(-1) is False


@pytest.mark.asyncio
async def test_recorded_spend_reduces_remaining_budget():
    await budget.record_spend(caller="test", query="crawl:https://example.com", credits=12)
    status = await budget.monthly_status()
    assert status["spent_credits"] == 12
    assert status["remaining_credits"] == 90_000 - 12


@pytest.mark.asyncio
async def test_hard_cap_never_exceeded_across_multiple_spends():
    for _ in range(90):
        await budget.record_spend(credits=1000)
    assert await budget.can_spend(1) is False
    status = await budget.monthly_status()
    assert status["remaining_credits"] == 0


@pytest.mark.asyncio
async def test_month_start_is_first_of_month_utc():
    from datetime import datetime, timezone

    ref = datetime(2026, 8, 9, 15, 30, tzinfo=timezone.utc)
    start = budget.month_start(ref)
    assert start == datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc)


def test_estimate_crawl_worst_case_is_one_credit_per_page():
    assert budget.estimate_crawl_worst_case(15) == 15
    assert budget.estimate_crawl_worst_case(1) == 1
    assert budget.estimate_crawl_worst_case(0) == 0
    assert budget.estimate_crawl_worst_case(-5) == 0


@pytest.mark.asyncio
async def test_recent_crawls_returns_traceability_log():
    await budget.record_spend(caller="website_substance", query="crawl:https://crynux.io", credits=15)
    recent = await budget.recent_crawls()
    assert len(recent) == 1
    assert recent[0]["caller"] == "website_substance"
    assert recent[0]["credits"] == 15


@pytest.mark.asyncio
async def test_recent_crawls_truncates_long_query():
    await budget.record_spend(caller="test", query="x" * 500, credits=1)
    recent = await budget.recent_crawls()
    assert len(recent[0]["query"]) == 300
