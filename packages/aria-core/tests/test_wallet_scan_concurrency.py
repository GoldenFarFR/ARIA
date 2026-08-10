"""Adaptive concurrency for wallet_scan_queue.py's MAX_WALLETS_PER_CYCLE
(10/08) -- isolated temp DB, no real network/LLM calls."""
from __future__ import annotations

import pytest

from aria_core import wallet_scan_concurrency as wsc


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(wsc, "DB_PATH", str(tmp_path / "wallet_scan_concurrency_test.db"))


@pytest.mark.asyncio
async def test_starts_at_4():
    assert await wsc.current_max_wallets() == 4


@pytest.mark.asyncio
async def test_overrun_tightens_immediately():
    new_max = await wsc.record_cycle_duration(wsc.HEALTHY_DURATION_CEILING_SECONDS)
    assert new_max == 3
    assert await wsc.current_max_wallets() == 3


@pytest.mark.asyncio
async def test_tightening_never_goes_below_floor():
    for _ in range(10):
        await wsc.record_cycle_duration(wsc.HEALTHY_DURATION_CEILING_SECONDS + 5)
    assert await wsc.current_max_wallets() == wsc._FLOOR


@pytest.mark.asyncio
async def test_healthy_cycles_below_threshold_never_ease_before_streak():
    for _ in range(wsc._EASE_AFTER_CONSECUTIVE_HEALTHY_CYCLES - 1):
        new_max = await wsc.record_cycle_duration(10.0)
        assert new_max == 4
    assert await wsc.current_max_wallets() == 4


@pytest.mark.asyncio
async def test_reaching_healthy_streak_eases_by_one():
    for _ in range(wsc._EASE_AFTER_CONSECUTIVE_HEALTHY_CYCLES - 1):
        await wsc.record_cycle_duration(10.0)
    new_max = await wsc.record_cycle_duration(10.0)  # crosses the streak
    assert new_max == 5
    assert await wsc.current_max_wallets() == 5


@pytest.mark.asyncio
async def test_easing_never_exceeds_ceiling():
    # Drive far enough past the ceiling that easing would overshoot without the cap.
    for _ in range(wsc._EASE_AFTER_CONSECUTIVE_HEALTHY_CYCLES * (wsc._CEILING - 4 + 3)):
        await wsc.record_cycle_duration(10.0)
    assert await wsc.current_max_wallets() == wsc._CEILING


@pytest.mark.asyncio
async def test_an_overrun_resets_the_healthy_streak():
    for _ in range(wsc._EASE_AFTER_CONSECUTIVE_HEALTHY_CYCLES - 1):
        await wsc.record_cycle_duration(10.0)
    await wsc.record_cycle_duration(wsc.HEALTHY_DURATION_CEILING_SECONDS)  # overrun, resets streak
    assert await wsc.current_max_wallets() == 3
    # One more healthy cycle alone isn't enough to ease again -- streak was reset.
    new_max = await wsc.record_cycle_duration(10.0)
    assert new_max == 3
