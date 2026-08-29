"""Tests for onchain_activity_observation.py (29/08, operator-directed).

Isolated tmp db, no network -- exercises record_observation() directly
rather than through check_candidates (see test_onchain_pool_discovery.py
for the integration-level test confirming the call site never changes the
qualification decision)."""
from __future__ import annotations

import aiosqlite
import pytest

from aria_core import onchain_activity_observation as m


@pytest.fixture
async def _tmp_db(tmp_path):
    path = str(tmp_path / "shadow.db")
    m._ensured_db_paths.clear()
    m._last_observed.clear()
    yield path
    m._ensured_db_paths.clear()
    m._last_observed.clear()


async def _rows(path):
    async with aiosqlite.connect(path) as c:
        c.row_factory = aiosqlite.Row
        cur = await c.execute(f"SELECT * FROM {m.TABLE} ORDER BY id")
        return [dict(r) for r in await cur.fetchall()]


@pytest.mark.asyncio
async def test_a_v3_pool_observation_is_recorded_clean(_tmp_db):
    """Point 1: a V3/V4 pool's observation records real values and the
    correct, non-biased activity_quality label."""
    await m.record_observation(
        chain="base", pool_address="0xpool", token_address="0xtoken",
        available=True, family="v3", swap_count=12, cumulative_volume_quote=340.5,
        distinct_traders_count=4, last_swap_age_seconds=2.1, db_path=_tmp_db,
    )
    row = (await _rows(_tmp_db))[0]
    assert row["family"] == "v3"
    assert row["activity_quality"] == "v3v4_clean"
    assert row["swap_count"] == 12
    assert row["cumulative_volume_quote"] == 340.5
    assert row["distinct_traders_count"] == 4
    assert row["last_swap_age_seconds"] == 2.1
    assert row["baseline_reset"] == 1  # first observation of this pool ever
    assert row["swaps_delta"] is None


@pytest.mark.asyncio
async def test_a_second_observation_records_a_real_delta(_tmp_db):
    """Point 2: the delta against the prior observation for the SAME pool."""
    await m.record_observation(
        chain="base", pool_address="0xpool", token_address="0xtoken",
        available=True, family="v3", swap_count=10, cumulative_volume_quote=100.0,
        distinct_traders_count=2, last_swap_age_seconds=5.0, db_path=_tmp_db,
    )
    await m.record_observation(
        chain="base", pool_address="0xpool", token_address="0xtoken",
        available=True, family="v3", swap_count=17, cumulative_volume_quote=250.0,
        distinct_traders_count=3, last_swap_age_seconds=1.0, db_path=_tmp_db,
    )
    rows = await _rows(_tmp_db)
    assert rows[0]["baseline_reset"] == 1
    second = rows[1]
    assert second["baseline_reset"] == 0
    assert second["swaps_delta"] == 7
    assert second["volume_quote_delta"] == 150.0
    assert second["traders_delta"] == 1


@pytest.mark.asyncio
async def test_process_restart_marks_baseline_reset_never_a_fabricated_delta(_tmp_db):
    """Point 3: simulates a restart by clearing the in-memory cache (exactly
    what happens when the real process restarts) between two observations
    of the same pool. The cumulative counter also resets to a SMALLER value
    in-memory (the feed's own counters start over at 0) -- the module must
    never compute a negative delta against the pre-restart cumulative, it
    must recognise this as a fresh baseline instead."""
    await m.record_observation(
        chain="base", pool_address="0xpool", token_address="0xtoken",
        available=True, family="v3", swap_count=500, cumulative_volume_quote=9000.0,
        distinct_traders_count=40, last_swap_age_seconds=3.0, db_path=_tmp_db,
    )
    m._last_observed.clear()  # the real trigger: process restart, feed state gone
    await m.record_observation(
        chain="base", pool_address="0xpool", token_address="0xtoken",
        available=True, family="v3", swap_count=2, cumulative_volume_quote=30.0,
        distinct_traders_count=1, last_swap_age_seconds=0.5, db_path=_tmp_db,
    )
    rows = await _rows(_tmp_db)
    second = rows[1]
    assert second["baseline_reset"] == 1
    assert second["swaps_delta"] is None
    assert second["volume_quote_delta"] is None
    assert second["traders_delta"] is None
    assert second["swap_count"] == 2  # the real (post-restart) value, still recorded


@pytest.mark.asyncio
async def test_unavailable_snapshot_stores_explicit_null_never_zero(_tmp_db):
    """Point 4: `not snapshot.available` must record NULL, never a fabricated
    0 that would be indistinguishable from a real zero-activity pool -- and
    must never touch the delta cache (the next real snapshot still compares
    against the last REAL observation, not this gap)."""
    await m.record_observation(
        chain="base", pool_address="0xpool", token_address="0xtoken",
        available=True, family="v3", swap_count=10, cumulative_volume_quote=100.0,
        distinct_traders_count=2, last_swap_age_seconds=5.0, db_path=_tmp_db,
    )
    await m.record_observation(
        chain="base", pool_address="0xpool", token_address="0xtoken",
        available=False, family=None, swap_count=None, cumulative_volume_quote=None,
        distinct_traders_count=None, last_swap_age_seconds=None, db_path=_tmp_db,
    )
    # a real snapshot arrives again right after the gap -- must compare
    # against the pool's LAST REAL observation (10 swaps), not the gap row.
    await m.record_observation(
        chain="base", pool_address="0xpool", token_address="0xtoken",
        available=True, family="v3", swap_count=13, cumulative_volume_quote=140.0,
        distinct_traders_count=2, last_swap_age_seconds=1.0, db_path=_tmp_db,
    )
    rows = await _rows(_tmp_db)
    gap_row = rows[1]
    assert gap_row["swap_count"] is None
    assert gap_row["cumulative_volume_quote"] is None
    assert gap_row["distinct_traders_count"] is None
    assert gap_row["family"] is None
    assert gap_row["activity_quality"] is None
    assert gap_row["baseline_reset"] == 0  # a gap is not itself a new baseline
    assert gap_row["swaps_delta"] is None
    third = rows[2]
    assert third["baseline_reset"] == 0
    assert third["swaps_delta"] == 3  # 13 - 10, skipping straight over the gap


@pytest.mark.asyncio
async def test_v2_is_labeled_biased_never_mixed_with_v3v4_clean(_tmp_db):
    """Point 5: V2's activity_quality is explicitly distinct from V3/V4's,
    so a future analysis can filter or split rather than average a biased
    Mint/Burn-inflated count into a clean one."""
    await m.record_observation(
        chain="base", pool_address="0xpool_v2", token_address="0xtoken",
        available=True, family="v2", swap_count=8, cumulative_volume_quote=0.0,
        distinct_traders_count=0, last_swap_age_seconds=4.0, db_path=_tmp_db,
    )
    row = (await _rows(_tmp_db))[0]
    assert row["family"] == "v2"
    assert row["activity_quality"] == "v2_biased"


# --- brique 2/5 (29/08): buy/sell cumulative + delta persistence ------------

@pytest.mark.asyncio
async def test_buy_sell_cumulatives_are_recorded(_tmp_db):
    await m.record_observation(
        chain="base", pool_address="0xpool", token_address="0xtoken",
        available=True, family="v3", swap_count=5, cumulative_volume_quote=100.0,
        distinct_traders_count=3, last_swap_age_seconds=2.0, db_path=_tmp_db,
        buy_count=3, sell_count=2, undetermined_count=0,
        buy_volume_quote=60.0, sell_volume_quote=40.0, undetermined_volume_quote=0.0,
    )
    row = (await _rows(_tmp_db))[0]
    assert row["buy_count"] == 3
    assert row["sell_count"] == 2
    assert row["undetermined_count"] == 0
    assert row["buy_volume_quote"] == 60.0
    assert row["sell_volume_quote"] == 40.0
    assert row["buy_count_delta"] is None  # first observation of this pool


@pytest.mark.asyncio
async def test_buy_sell_delta_against_the_prior_observation(_tmp_db):
    await m.record_observation(
        chain="base", pool_address="0xpool", token_address="0xtoken",
        available=True, family="v3", swap_count=5, cumulative_volume_quote=100.0,
        distinct_traders_count=3, last_swap_age_seconds=2.0, db_path=_tmp_db,
        buy_count=3, sell_count=2, buy_volume_quote=60.0, sell_volume_quote=40.0,
    )
    await m.record_observation(
        chain="base", pool_address="0xpool", token_address="0xtoken",
        available=True, family="v3", swap_count=9, cumulative_volume_quote=180.0,
        distinct_traders_count=4, last_swap_age_seconds=1.0, db_path=_tmp_db,
        buy_count=5, sell_count=4, buy_volume_quote=100.0, sell_volume_quote=80.0,
    )
    second = (await _rows(_tmp_db))[1]
    assert second["buy_count_delta"] == 2
    assert second["sell_count_delta"] == 2
    assert second["buy_volume_quote_delta"] == 40.0
    assert second["sell_volume_quote_delta"] == 40.0


@pytest.mark.asyncio
async def test_buy_sell_baseline_reset_after_restart_never_a_fabricated_delta(_tmp_db):
    await m.record_observation(
        chain="base", pool_address="0xpool", token_address="0xtoken",
        available=True, family="v3", swap_count=500, cumulative_volume_quote=9000.0,
        distinct_traders_count=40, last_swap_age_seconds=3.0, db_path=_tmp_db,
        buy_count=300, sell_count=200, buy_volume_quote=5000.0, sell_volume_quote=4000.0,
    )
    m._last_observed.clear()  # process restart
    await m.record_observation(
        chain="base", pool_address="0xpool", token_address="0xtoken",
        available=True, family="v3", swap_count=2, cumulative_volume_quote=30.0,
        distinct_traders_count=1, last_swap_age_seconds=0.5, db_path=_tmp_db,
        buy_count=1, sell_count=1, buy_volume_quote=15.0, sell_volume_quote=15.0,
    )
    second = (await _rows(_tmp_db))[1]
    assert second["baseline_reset"] == 1
    assert second["buy_count_delta"] is None
    assert second["sell_count_delta"] is None
    assert second["buy_volume_quote_delta"] is None
    assert second["sell_volume_quote_delta"] is None
    assert second["buy_count"] == 1  # the real post-restart value, still recorded


@pytest.mark.asyncio
async def test_unavailable_snapshot_stores_null_buy_sell_never_zero(_tmp_db):
    await m.record_observation(
        chain="base", pool_address="0xpool", token_address="0xtoken",
        available=False, family=None, swap_count=None, cumulative_volume_quote=None,
        distinct_traders_count=None, last_swap_age_seconds=None, db_path=_tmp_db,
    )
    row = (await _rows(_tmp_db))[0]
    assert row["buy_count"] is None
    assert row["sell_count"] is None
    assert row["undetermined_count"] is None
    assert row["buy_volume_quote"] is None
    assert row["sell_volume_quote"] is None


# --- brique 3/5 (29/08): liquidity delta, same doctrine, third axis --------

@pytest.mark.asyncio
async def test_liquidity_cumulatives_are_recorded(_tmp_db):
    await m.record_observation(
        chain="base", pool_address="0xpool", token_address="0xtoken",
        available=True, family="v3", swap_count=5, cumulative_volume_quote=100.0,
        distinct_traders_count=3, last_swap_age_seconds=2.0, db_path=_tmp_db,
        liquidity_added_quote=500.0, liquidity_removed_quote=120.0,
        liquidity_added_raw=0.0, liquidity_removed_raw=0.0,
    )
    row = (await _rows(_tmp_db))[0]
    assert row["liquidity_added_quote"] == 500.0
    assert row["liquidity_removed_quote"] == 120.0
    assert row["liquidity_added_quote_delta"] is None  # first observation


@pytest.mark.asyncio
async def test_liquidity_delta_against_the_prior_observation(_tmp_db):
    await m.record_observation(
        chain="base", pool_address="0xpool", token_address="0xtoken",
        available=True, family="v4", swap_count=5, cumulative_volume_quote=100.0,
        distinct_traders_count=3, last_swap_age_seconds=2.0, db_path=_tmp_db,
        liquidity_added_raw=1000.0, liquidity_removed_raw=200.0,
    )
    await m.record_observation(
        chain="base", pool_address="0xpool", token_address="0xtoken",
        available=True, family="v4", swap_count=9, cumulative_volume_quote=180.0,
        distinct_traders_count=4, last_swap_age_seconds=1.0, db_path=_tmp_db,
        liquidity_added_raw=1500.0, liquidity_removed_raw=350.0,
    )
    second = (await _rows(_tmp_db))[1]
    assert second["liquidity_added_raw_delta"] == 500.0
    assert second["liquidity_removed_raw_delta"] == 150.0


@pytest.mark.asyncio
async def test_liquidity_baseline_reset_after_restart_never_a_fabricated_delta(_tmp_db):
    await m.record_observation(
        chain="base", pool_address="0xpool", token_address="0xtoken",
        available=True, family="v3", swap_count=500, cumulative_volume_quote=9000.0,
        distinct_traders_count=40, last_swap_age_seconds=3.0, db_path=_tmp_db,
        liquidity_added_quote=50000.0, liquidity_removed_quote=10000.0,
    )
    m._last_observed.clear()  # process restart
    await m.record_observation(
        chain="base", pool_address="0xpool", token_address="0xtoken",
        available=True, family="v3", swap_count=2, cumulative_volume_quote=30.0,
        distinct_traders_count=1, last_swap_age_seconds=0.5, db_path=_tmp_db,
        liquidity_added_quote=10.0, liquidity_removed_quote=5.0,
    )
    second = (await _rows(_tmp_db))[1]
    assert second["baseline_reset"] == 1
    assert second["liquidity_added_quote_delta"] is None
    assert second["liquidity_removed_quote_delta"] is None
    assert second["liquidity_added_quote"] == 10.0  # real post-restart value, still recorded


@pytest.mark.asyncio
async def test_unavailable_snapshot_stores_null_liquidity_never_zero(_tmp_db):
    await m.record_observation(
        chain="base", pool_address="0xpool", token_address="0xtoken",
        available=False, family=None, swap_count=None, cumulative_volume_quote=None,
        distinct_traders_count=None, last_swap_age_seconds=None, db_path=_tmp_db,
    )
    row = (await _rows(_tmp_db))[0]
    assert row["liquidity_added_quote"] is None
    assert row["liquidity_removed_quote"] is None
    assert row["liquidity_added_raw"] is None
    assert row["liquidity_removed_raw"] is None
