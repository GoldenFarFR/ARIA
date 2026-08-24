"""Plafond de request units Chainstack par chaine -- meme patron que
test_x_research_budget.py (plafond dur, remise a zero calendaire quotidienne,
append-only), mais compte des UNITES (pas des requetes) et est cloisonne par
chaine (solana/base/robinhood) pour qu'une chaine qui s'emballe ne puisse
jamais manger le budget des deux autres."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from aria_core.services import chainstack_ru_budget as budget


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(budget, "aria_db_path", lambda: tmp_path / "chainstack_ru_budget_test.db")
    yield


@pytest.mark.asyncio
async def test_empty_log_starts_with_full_budget():
    status = await budget.daily_status("solana")
    assert status["cap_units"] == 200_000
    assert status["used_units"] == 0
    assert status["remaining_units"] == 200_000


@pytest.mark.asyncio
async def test_can_spend_true_below_cap():
    assert await budget.can_spend("solana") is True


@pytest.mark.asyncio
async def test_recorded_usage_reduces_remaining():
    await budget.record_usage("solana", 50_000, purpose="curve_tracker_poll")
    await budget.record_usage("solana", 25_000, purpose="curve_tracker_poll")
    status = await budget.daily_status("solana")
    assert status["used_units"] == 75_000
    assert status["remaining_units"] == 125_000


@pytest.mark.asyncio
async def test_hard_cap_never_exceeded():
    await budget.record_usage("solana", budget.DAILY_UNIT_CAP_PER_CHAIN, purpose="curve_tracker_poll")
    assert await budget.can_spend("solana") is False
    status = await budget.daily_status("solana")
    assert status["remaining_units"] == 0


@pytest.mark.asyncio
async def test_usage_beyond_cap_still_recorded_but_reports_zero_remaining():
    """A single call can legitimately batch more units than the whole
    remaining budget (e.g. a getMultipleAccounts burst) -- remaining_units
    floors at 0 rather than going negative, it never blocks logging what
    actually happened."""
    await budget.record_usage("solana", budget.DAILY_UNIT_CAP_PER_CHAIN + 50_000, purpose="burst")
    status = await budget.daily_status("solana")
    assert status["used_units"] == budget.DAILY_UNIT_CAP_PER_CHAIN + 50_000
    assert status["remaining_units"] == 0


@pytest.mark.asyncio
async def test_chains_are_independently_budgeted():
    """The whole point of keying by chain: a chain at its cap must never
    starve another chain's own budget."""
    await budget.record_usage("solana", budget.DAILY_UNIT_CAP_PER_CHAIN, purpose="curve_tracker_poll")
    assert await budget.can_spend("solana") is False
    assert await budget.can_spend("base") is True
    assert await budget.can_spend("robinhood") is True
    base_status = await budget.daily_status("base")
    assert base_status["used_units"] == 0
    assert base_status["remaining_units"] == 200_000


@pytest.mark.asyncio
async def test_daily_reset_on_new_calendar_day():
    await budget.record_usage("solana", 100_000, purpose="curve_tracker_poll")
    yesterday = datetime.now(timezone.utc) - timedelta(days=1)
    import aiosqlite

    async with aiosqlite.connect(str(budget.aria_db_path())) as db:
        await db.execute(
            "UPDATE chainstack_ru_log SET created_at = ? WHERE chain = 'solana'",
            (yesterday.isoformat(),),
        )
        await db.commit()

    status = await budget.daily_status("solana")
    assert status["used_units"] == 0
    assert status["remaining_units"] == 200_000
