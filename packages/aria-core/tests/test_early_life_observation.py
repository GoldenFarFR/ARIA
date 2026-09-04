"""early_life_observation -- post-qualification trajectory tracking.

04/09, operator go (Chantier A, strictly observation-only). Today's
pipeline only observes a candidate WHILE it's pending qualification
(onchain_pool_discovery._candidates), then drops it the instant it
qualifies -- confirmed live on $LEGS (03/09): qualified on its very first
check, exactly ONE snapshot ever recorded, zero trajectory data. This
module continues recording RAW activity snapshots for a bounded window
AFTER qualification, so a candidate's early-life trajectory (t=0, t=5s,
t=10s...) becomes reconstructible.

Never derives a feature -- purely schedules repeated calls to the existing,
already-tested onchain_activity_observation.record_observation (append-
only, immutable). No trade decision anywhere in this module."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest

from aria_core import early_life_observation
from aria_core.paths import configure_data_dir


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path):
    configure_data_dir(str(tmp_path))
    yield


def _t(seconds: int) -> datetime:
    return datetime(2026, 9, 4, 0, 0, 0, tzinfo=timezone.utc) + timedelta(seconds=seconds)


async def test_start_tracking_records_a_row():
    await early_life_observation.start_tracking(
        "robinhood", "0xpool", "0xtoken", qualified_at=_t(0),
    )
    active = await early_life_observation.list_active("robinhood", now=_t(1))
    assert len(active) == 1
    assert active[0]["pool_address"] == "0xpool"


async def test_start_tracking_never_duplicates_the_same_pool():
    await early_life_observation.start_tracking("robinhood", "0xpool", "0xtoken", qualified_at=_t(0))
    await early_life_observation.start_tracking("robinhood", "0xpool", "0xtoken", qualified_at=_t(1))
    active = await early_life_observation.list_active("robinhood", now=_t(2))
    assert len(active) == 1


async def test_active_candidate_within_window_is_listed():
    await early_life_observation.start_tracking("robinhood", "0xpool", "0xtoken", qualified_at=_t(0))
    active = await early_life_observation.list_active(
        "robinhood", now=_t(100), window_seconds=300.0,
    )
    assert len(active) == 1


async def test_expired_candidate_is_not_listed():
    await early_life_observation.start_tracking("robinhood", "0xpool", "0xtoken", qualified_at=_t(0))
    active = await early_life_observation.list_active(
        "robinhood", now=_t(301), window_seconds=300.0,
    )
    assert active == []


async def test_different_chains_never_cross_contaminate():
    await early_life_observation.start_tracking("robinhood", "0xpool", "0xtoken", qualified_at=_t(0))
    active = await early_life_observation.list_active("base", now=_t(1))
    assert active == []


async def test_advance_tracking_cycle_records_a_snapshot_for_each_active_candidate():
    await early_life_observation.start_tracking("robinhood", "0xpool", "0xtoken", qualified_at=_t(0))
    ws_feed = MagicMock()
    ws_feed.get_snapshot = MagicMock(return_value=MagicMock(
        available=True, family="v2", swap_count=3, cumulative_volume_quote=1.5,
        distinct_traders_count=2, stale_seconds=1.0, buy_count=2, sell_count=1,
        undetermined_count=0, buy_volume_quote=1.0, sell_volume_quote=0.5,
        undetermined_volume_quote=0.0, liquidity_added_quote=None, liquidity_removed_quote=None,
        liquidity_added_raw=None, liquidity_removed_raw=None, price_quote=1.0, price_usd=1.0,
        reserve_usd=20000.0, raw_liquidity=None, quote_reserve_raw=None, quote_is_weth=False,
    ))
    n = await early_life_observation.advance_tracking_cycle(
        "robinhood", ws_feed=ws_feed, now=_t(5),
    )
    assert n == 1
    async with aiosqlite.connect(early_life_observation._activity_db_path()) as db:
        cur = await db.execute(
            "SELECT COUNT(*) FROM onchain_activity_observation_log WHERE pool_address = '0xpool'"
        )
        (count,) = await cur.fetchone()
    assert count == 1


async def test_advance_tracking_cycle_skips_expired_candidates():
    await early_life_observation.start_tracking("robinhood", "0xpool", "0xtoken", qualified_at=_t(0))
    ws_feed = MagicMock()
    ws_feed.get_snapshot = MagicMock(return_value=MagicMock(available=False))
    n = await early_life_observation.advance_tracking_cycle(
        "robinhood", ws_feed=ws_feed, now=_t(301), window_seconds=300.0,
    )
    assert n == 0


async def test_advance_tracking_cycle_never_raises_on_snapshot_failure():
    await early_life_observation.start_tracking("robinhood", "0xpool", "0xtoken", qualified_at=_t(0))
    ws_feed = MagicMock()
    ws_feed.get_snapshot = MagicMock(side_effect=Exception("boom"))
    n = await early_life_observation.advance_tracking_cycle(
        "robinhood", ws_feed=ws_feed, now=_t(5),
    )
    assert n == 0
