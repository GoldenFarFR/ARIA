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
    # 24/08 -- record_usage_fast's in-memory buffer and used_today's read
    # cache are module-level state (required to stay cheap at the real 45
    # req/s call rate, see module docstring) -- without clearing them here,
    # a value left over from a PREVIOUS test (a different tmp DB) would
    # silently leak into this one.
    budget._pending_units.clear()
    budget._read_cache.clear()
    yield
    budget._pending_units.clear()
    budget._read_cache.clear()


@pytest.mark.asyncio
async def test_empty_log_starts_with_full_budget():
    status = await budget.daily_status("solana")
    assert status["cap_units"] == budget.cap_for("solana")
    assert status["used_units"] == 0
    assert status["remaining_units"] == budget.cap_for("solana")


@pytest.mark.asyncio
async def test_can_spend_true_below_cap():
    assert await budget.can_spend("solana") is True


@pytest.mark.asyncio
async def test_recorded_usage_reduces_remaining():
    await budget.record_usage("solana", 50_000, purpose="curve_tracker_poll")
    await budget.record_usage("solana", 25_000, purpose="curve_tracker_poll")
    status = await budget.daily_status("solana")
    assert status["used_units"] == 75_000
    assert status["remaining_units"] == budget.cap_for("solana") - 75_000


@pytest.mark.asyncio
async def test_hard_cap_never_exceeded():
    await budget.record_usage("solana", budget.cap_for("solana"), purpose="curve_tracker_poll")
    assert await budget.can_spend("solana") is False
    status = await budget.daily_status("solana")
    assert status["remaining_units"] == 0


@pytest.mark.asyncio
async def test_usage_beyond_cap_still_recorded_but_reports_zero_remaining():
    """A single call can legitimately batch more units than the whole
    remaining budget (e.g. a getMultipleAccounts burst) -- remaining_units
    floors at 0 rather than going negative, it never blocks logging what
    actually happened."""
    await budget.record_usage("solana", budget.cap_for("solana") + 50_000, purpose="burst")
    status = await budget.daily_status("solana")
    assert status["used_units"] == budget.cap_for("solana") + 50_000
    assert status["remaining_units"] == 0


@pytest.mark.asyncio
async def test_chains_are_independently_budgeted():
    """The whole point of keying by chain: a chain at its cap must never
    starve another chain's own budget."""
    await budget.record_usage("solana", budget.cap_for("solana"), purpose="curve_tracker_poll")
    assert await budget.can_spend("solana") is False
    assert await budget.can_spend("base") is True
    assert await budget.can_spend("robinhood") is True
    base_status = await budget.daily_status("base")
    assert base_status["used_units"] == 0
    assert base_status["remaining_units"] == budget.cap_for("base")


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
    assert status["remaining_units"] == budget.cap_for("solana")


# --- record_usage_fast / flush_pending (24/08, hot-loop batching) ----------

@pytest.mark.asyncio
async def test_record_usage_fast_counts_toward_used_before_any_flush():
    """The whole point: a caller in a 45 req/s polling loop must see an
    accurate, up-to-date total WITHOUT waiting for the next flush_pending()
    -- record_usage_fast is zero-I/O but still visible immediately."""
    budget.record_usage_fast("solana", 5)
    budget.record_usage_fast("solana", 3)
    status = await budget.daily_status("solana")
    assert status["used_units"] == 8
    assert status["remaining_units"] == budget.cap_for("solana") - 8


@pytest.mark.asyncio
async def test_record_usage_fast_alone_never_writes_to_the_db():
    budget.record_usage_fast("solana", 42)
    import aiosqlite

    async with aiosqlite.connect(str(budget.aria_db_path())) as db:
        cur = await db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='chainstack_ru_log'")
        assert await cur.fetchone() is None  # table never even created


@pytest.mark.asyncio
async def test_flush_pending_persists_and_clears_the_buffer():
    budget.record_usage_fast("solana", 10)
    budget.record_usage_fast("base", 4)
    await budget.flush_pending()
    assert budget._pending_units == {}
    status_solana = await budget.daily_status("solana")
    status_base = await budget.daily_status("base")
    assert status_solana["used_units"] == 10
    assert status_base["used_units"] == 4


@pytest.mark.asyncio
async def test_flush_pending_is_a_noop_with_nothing_pending():
    await budget.flush_pending()  # must not raise
    status = await budget.daily_status("solana")
    assert status["used_units"] == 0


@pytest.mark.asyncio
async def test_can_spend_turns_false_from_fast_usage_alone_before_any_flush():
    budget.record_usage_fast("solana", budget.cap_for("solana"))
    assert await budget.can_spend("solana") is False
    assert await budget.can_spend("base") is True  # unaffected, cloisonnement


@pytest.mark.asyncio
async def test_used_today_read_is_cached_within_the_ttl(monkeypatch):
    """Direct DB writes from OUTSIDE this module (bypassing record_usage's
    own cache invalidation) must not be visible until the cache TTL expires
    -- this is the deliberate cost of not hitting SQLite on every read at
    45 req/s (see module docstring)."""
    import aiosqlite

    await budget.daily_status("solana")  # first read: populates the cache at 0
    async with aiosqlite.connect(str(budget.aria_db_path())) as db:
        await db.execute(
            "INSERT INTO chainstack_ru_log (chain, units, purpose, created_at) VALUES (?, ?, ?, ?)",
            ("solana", 999, "external_write", datetime.now(timezone.utc).isoformat()),
        )
        await db.commit()
    status = await budget.daily_status("solana")  # still within the TTL
    assert status["used_units"] == 0  # stale cache, deliberately not re-read yet


# --- per-chain caps (25/08) -------------------------------------------------

def test_cap_for_returns_the_calibrated_per_chain_value():
    assert budget.cap_for("base") == 25_000
    assert budget.cap_for("solana") == 175_000
    assert budget.cap_for("robinhood") == 400_000


def test_cap_for_unknown_chain_falls_back_to_default():
    assert budget.cap_for("some_future_chain") == budget._DEFAULT_CAP


# --- cap-reached alert queue (25/08, operator request: notify on the moment) -

@pytest.mark.asyncio
async def test_can_spend_false_enqueues_exactly_one_alert():
    await budget.record_usage("base", budget.cap_for("base"), purpose="test")
    assert await budget.can_spend("base") is False
    assert await budget.can_spend("base") is False  # refused again, same day
    alerts = await budget.pop_unsent_cap_alerts()
    assert len(alerts) == 1
    assert alerts[0]["chain"] == "base"
    assert alerts[0]["cap_units"] == budget.cap_for("base")


@pytest.mark.asyncio
async def test_pop_unsent_cap_alerts_never_returns_the_same_alert_twice():
    await budget.record_usage("robinhood", budget.cap_for("robinhood"), purpose="test")
    await budget.can_spend("robinhood")
    first = await budget.pop_unsent_cap_alerts()
    assert len(first) == 1
    second = await budget.pop_unsent_cap_alerts()
    assert second == []


@pytest.mark.asyncio
async def test_no_alert_while_still_under_cap():
    await budget.record_usage("solana", 100, purpose="test")
    await budget.can_spend("solana")
    assert await budget.pop_unsent_cap_alerts() == []
