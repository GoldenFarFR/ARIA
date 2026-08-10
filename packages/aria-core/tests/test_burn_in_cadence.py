"""Generic burn-in-cadence auto-restoration (10/08, Item #133) -- isolated
temp DB, no real network/LLM calls."""
from __future__ import annotations

import pytest

from aria_core import burn_in_cadence


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(burn_in_cadence, "DB_PATH", str(tmp_path / "burn_in_test.db"))


@pytest.fixture()
def registered(monkeypatch):
    monkeypatch.setitem(burn_in_cadence._REQUIRED_CLEAN_CYCLES, "test_task", 3)
    yield "test_task"


@pytest.mark.asyncio
async def test_unregistered_task_never_active():
    assert await burn_in_cadence.is_burn_in_active("some_unregistered_task") is False


@pytest.mark.asyncio
async def test_unregistered_task_resolve_always_nominal():
    value = await burn_in_cadence.resolve("some_unregistered_task", burn_in_value=1, nominal_value=3)
    assert value == 3


@pytest.mark.asyncio
async def test_unregistered_task_record_cycle_result_always_false():
    assert await burn_in_cadence.record_cycle_result("some_unregistered_task", True) is False


@pytest.mark.asyncio
async def test_registered_task_active_by_default(registered):
    assert await burn_in_cadence.is_burn_in_active(registered) is True
    value = await burn_in_cadence.resolve(registered, burn_in_value=1, nominal_value=3)
    assert value == 1


@pytest.mark.asyncio
async def test_below_threshold_never_completes(registered):
    for _ in range(2):  # required is 3
        assert await burn_in_cadence.record_cycle_result(registered, True) is False
    assert await burn_in_cadence.is_burn_in_active(registered) is True


@pytest.mark.asyncio
async def test_reaching_threshold_completes_exactly_once(registered):
    assert await burn_in_cadence.record_cycle_result(registered, True) is False
    assert await burn_in_cadence.record_cycle_result(registered, True) is False
    assert await burn_in_cadence.record_cycle_result(registered, True) is True  # crosses threshold
    assert await burn_in_cadence.is_burn_in_active(registered) is False
    # Further clean cycles never re-fire the "just completed" signal.
    assert await burn_in_cadence.record_cycle_result(registered, True) is False
    assert await burn_in_cadence.record_cycle_result(registered, False) is False


@pytest.mark.asyncio
async def test_a_failure_resets_the_streak(registered):
    assert await burn_in_cadence.record_cycle_result(registered, True) is False
    assert await burn_in_cadence.record_cycle_result(registered, True) is False
    assert await burn_in_cadence.record_cycle_result(registered, False) is False  # resets to 0
    # Two more clean cycles (would have crossed 3 without the reset) still isn't enough.
    assert await burn_in_cadence.record_cycle_result(registered, True) is False
    assert await burn_in_cadence.record_cycle_result(registered, True) is False
    assert await burn_in_cadence.is_burn_in_active(registered) is True


@pytest.mark.asyncio
async def test_resolve_flips_to_nominal_once_complete(registered):
    for _ in range(3):
        await burn_in_cadence.record_cycle_result(registered, True)
    value = await burn_in_cadence.resolve(registered, burn_in_value=1, nominal_value=3)
    assert value == 3


@pytest.mark.asyncio
async def test_a_failure_after_completion_is_a_no_op(registered):
    for _ in range(3):
        await burn_in_cadence.record_cycle_result(registered, True)
    assert await burn_in_cadence.record_cycle_result(registered, False) is False
    # Still complete -- a failure long after burn-in never re-arms it.
    assert await burn_in_cadence.is_burn_in_active(registered) is False
