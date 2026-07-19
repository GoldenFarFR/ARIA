"""Plafond de requêtes X pour la diligence de conviction -- même patron que
test_x402_budget.py (plafond dur, remise à zéro hebdomadaire calendaire), mais compte
des REQUÊTES, pas des dollars (coût réel de lecture X non vérifié, jamais inventé)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from aria_core import x_research_budget as budget


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(budget, "DB_PATH", str(tmp_path / "x_research_budget_test.db"))
    yield


@pytest.mark.asyncio
async def test_empty_log_starts_with_full_budget():
    status = await budget.weekly_status()
    assert status["cap_requests"] == 100
    assert status["used_requests"] == 0
    assert status["remaining_requests"] == 100


@pytest.mark.asyncio
async def test_can_spend_true_below_cap():
    assert await budget.can_spend() is True


@pytest.mark.asyncio
async def test_blocked_attempts_never_consume_budget():
    for _ in range(5):
        await budget.record_request(purpose="buzz_search", status="blocked", reason="plafond atteint")
    status = await budget.weekly_status()
    assert status["used_requests"] == 0
    assert status["remaining_requests"] == 100


@pytest.mark.asyncio
async def test_hard_cap_never_exceeded():
    for _ in range(budget.WEEKLY_REQUEST_CAP):
        await budget.record_request(purpose="buzz_search", status="ok")
    assert await budget.can_spend() is False
    status = await budget.weekly_status()
    assert status["remaining_requests"] == 0
    assert status["used_requests"] == budget.WEEKLY_REQUEST_CAP


@pytest.mark.asyncio
async def test_recorded_requests_reduce_remaining():
    await budget.record_request(purpose="buzz_search", contract="0xabc", status="ok")
    await budget.record_request(purpose="posting_cadence", contract="0xabc", status="ok")
    status = await budget.weekly_status()
    assert status["used_requests"] == 2
    assert status["remaining_requests"] == 98


@pytest.mark.asyncio
async def test_weekly_reset_on_new_calendar_week():
    await budget.record_request(purpose="buzz_search", status="ok")
    last_week = datetime.now(timezone.utc) - timedelta(days=8)
    import aiosqlite

    async with aiosqlite.connect(budget.DB_PATH) as db:
        await db.execute(
            "UPDATE x_research_request_log SET created_at = ? WHERE purpose = 'buzz_search'",
            (last_week.isoformat(),),
        )
        await db.commit()

    status = await budget.weekly_status()
    assert status["used_requests"] == 0
    assert status["remaining_requests"] == 100
