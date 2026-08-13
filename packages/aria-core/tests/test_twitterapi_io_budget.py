"""Proactive TwitterAPI.io prepaid-credit runway monitor (13/08) -- built
after a real incident where the account's credit silently ran to zero for
~24h with no alert. No real network/Telegram call here: fetch_credit_balance
and send_message are both faked."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import aiosqlite
import pytest

from aria_core import twitterapi_io_budget as budget
from aria_core.services.twitterapi_io import TwitterApiIoBalance


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(budget, "DB_PATH", str(tmp_path / "twitterapi_io_budget_test.db"))


@pytest.fixture
def sent_messages(monkeypatch):
    calls = []

    async def _fake_send_message(text, **kwargs):
        calls.append(text)
        return True

    monkeypatch.setattr("aria_core.gateway.telegram_bot.send_message", _fake_send_message)
    return calls


def _balance(credits: int) -> TwitterApiIoBalance:
    return TwitterApiIoBalance(recharge_credits=credits, bonus_credits=0)


async def _rewind_last_checked_at(hours: float) -> None:
    """Simulates real elapsed time between two readings by moving the
    stored timestamp back -- avoids depending on wall-clock sleeps."""
    past = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    async with aiosqlite.connect(budget.DB_PATH) as db:
        await db.execute(
            "UPDATE twitterapi_io_budget_state SET last_checked_at = ? WHERE id = 1", (past,),
        )
        await db.commit()


@pytest.mark.asyncio
async def test_failed_balance_check_never_alerts(monkeypatch, sent_messages):
    monkeypatch.setattr(budget, "fetch_credit_balance", lambda: _async_none())

    result = await budget.check_and_alert()

    assert result == {"checked": False}
    assert sent_messages == []


async def _async_none():
    return None


@pytest.mark.asyncio
async def test_first_ever_reading_has_no_prior_point_so_no_alert(monkeypatch, sent_messages):
    monkeypatch.setattr(budget, "fetch_credit_balance", lambda: _async_return(_balance(100_000)))

    result = await budget.check_and_alert()

    assert result["checked"] is True
    assert result["runway_hours"] is None
    assert result["alerted"] is False
    assert sent_messages == []


async def _async_return(value):
    return value


@pytest.mark.asyncio
async def test_slow_burn_with_ample_runway_never_alerts(monkeypatch, sent_messages):
    monkeypatch.setattr(budget, "fetch_credit_balance", lambda: _async_return(_balance(100_000)))
    await budget.check_and_alert()
    await _rewind_last_checked_at(hours=6)

    # Lost 1,000 credits over 6h -> ~166.7/h -> runway ~99,000/166.7 ~ 594h, well above 24h.
    monkeypatch.setattr(budget, "fetch_credit_balance", lambda: _async_return(_balance(99_000)))
    result = await budget.check_and_alert()

    assert result["alerted"] is False
    assert result["runway_hours"] > budget._LOW_RUNWAY_HOURS_THRESHOLD
    assert sent_messages == []


@pytest.mark.asyncio
async def test_fast_burn_under_threshold_alerts_exactly_once(monkeypatch, sent_messages):
    monkeypatch.setattr(budget, "fetch_credit_balance", lambda: _async_return(_balance(50_000)))
    await budget.check_and_alert()
    await _rewind_last_checked_at(hours=1)

    # Lost 40,000 credits in 1h -> burn 40,000/h -> runway 10,000/40,000 = 0.25h, well under 24h.
    monkeypatch.setattr(budget, "fetch_credit_balance", lambda: _async_return(_balance(10_000)))
    result = await budget.check_and_alert()

    assert result["alerted"] is True
    assert result["runway_hours"] < budget._LOW_RUNWAY_HOURS_THRESHOLD
    assert len(sent_messages) == 1
    assert "TwitterAPI.io" in sent_messages[0]

    # Hysteresis: still low on the very next check (no time/balance change simulated
    # beyond the DB write from the previous call) -- must NOT alert again.
    await _rewind_last_checked_at(hours=1)
    monkeypatch.setattr(budget, "fetch_credit_balance", lambda: _async_return(_balance(9_000)))
    result2 = await budget.check_and_alert()

    assert result2["alerted"] is False
    assert len(sent_messages) == 1  # no repeat


@pytest.mark.asyncio
async def test_recharge_disarms_and_a_later_exhaustion_can_alert_again(monkeypatch, sent_messages):
    monkeypatch.setattr(budget, "fetch_credit_balance", lambda: _async_return(_balance(50_000)))
    await budget.check_and_alert()
    await _rewind_last_checked_at(hours=1)

    monkeypatch.setattr(budget, "fetch_credit_balance", lambda: _async_return(_balance(10_000)))
    result = await budget.check_and_alert()
    assert result["alerted"] is True
    assert len(sent_messages) == 1

    # Operator recharges -- balance jumps back up, disarms.
    await _rewind_last_checked_at(hours=1)
    monkeypatch.setattr(budget, "fetch_credit_balance", lambda: _async_return(_balance(500_000)))
    result2 = await budget.check_and_alert()
    assert result2["alerted"] is False

    # A fresh fast burn afterwards must be able to alert again (never
    # permanently silenced by the first armament).
    await _rewind_last_checked_at(hours=1)
    monkeypatch.setattr(budget, "fetch_credit_balance", lambda: _async_return(_balance(1_000)))
    result3 = await budget.check_and_alert()
    assert result3["alerted"] is True
    assert len(sent_messages) == 2
