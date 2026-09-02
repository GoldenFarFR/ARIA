"""Real GoPlus consumption meter (operator P0 #3, 02/09) -- a meter, never a
gate: it records what was spent and can never block or break the call it
measures."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from aria_core import goplus_call_budget as budget


async def test_counts_each_call_by_outcome():
    for _ in range(3):
        await budget.record_call("ok")
    await budget.record_call("rate_limited")
    await budget.record_call("error")

    usage = await budget.usage(days=1)
    assert len(usage) == 1
    today = usage[0]
    assert today["day"] == datetime.now(timezone.utc).date().isoformat()
    assert today["ok"] == 3
    assert today["rate_limited"] == 1
    assert today["error"] == 1
    assert today["total"] == 5


async def test_today_total_is_the_number_to_compare_against_any_future_ceiling():
    assert await budget.today_total() == 0
    for _ in range(7):
        await budget.record_call("ok")
    assert await budget.today_total() == 7


async def test_unknown_outcome_is_still_counted_never_dropped():
    await budget.record_call("")
    await budget.record_call(None)  # type: ignore[arg-type]
    assert await budget.today_total() == 2


async def test_a_broken_meter_never_breaks_the_call_it_measures(monkeypatch):
    """The whole point of the best-effort posture: if the DB is unavailable,
    the GoPlus call must still go through."""
    def _boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(budget.aiosqlite, "connect", _boom)
    await budget.record_call("ok")          # must not raise
    assert await budget.usage(days=7) == []  # degrades to empty, never raises


async def test_meter_never_gates_anything():
    """Locks the design intent: this module exposes no ceiling, no
    can_spend(), no suspend -- turning a measured number into a real cap is a
    separate operator decision."""
    forbidden = {"can_spend", "is_suspended", "suspend", "block", "limit", "MAX_CALLS", "DAILY_CAP"}
    assert forbidden.isdisjoint(dir(budget))
