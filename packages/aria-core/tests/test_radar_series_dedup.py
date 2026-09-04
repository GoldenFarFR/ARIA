"""radar_series_dedup -- factual liquidity-signature duplicate detection for
ARIA RADAR V1, built 04/09 after live observation: two distinct serial-
deploy bots on Robinhood each reuse a near-identical initial reserve
(~$50,554.1x and ~$21,0xx-21,3xx) across dozens of differently-named/
differently-contracted pools within the same hour, dominating the radar
with zero-value noise (documented in HANDOFF_PIPELINE_MOMENTUM.md's
2026.09.04 entry).

Purely a NOISE-DEDUP mechanism, never a security verdict: a reserve amount
matching a recent one is a fact about liquidity scripting, not proof of a
scam. Records EVERY evaluated candidate (not just the ones that end up
notified) so the chain of matches keeps working even after the first
duplicate is suppressed."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import aiosqlite
import pytest

from aria_core import radar_series_dedup
from aria_core.paths import configure_data_dir


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path):
    configure_data_dir(str(tmp_path))
    yield


def _t(seconds: int) -> datetime:
    return datetime(2026, 9, 4, 0, 0, 0, tzinfo=timezone.utc) + timedelta(seconds=seconds)


async def test_first_candidate_never_a_duplicate():
    is_dup = await radar_series_dedup.record_and_check_duplicate(
        "robinhood", 50554.14, now=_t(0),
    )
    assert is_dup is False


async def test_second_candidate_within_tolerance_is_a_duplicate():
    await radar_series_dedup.record_and_check_duplicate("robinhood", 50554.14, now=_t(0))
    is_dup = await radar_series_dedup.record_and_check_duplicate(
        "robinhood", 50554.15, now=_t(60),
    )
    assert is_dup is True


async def test_candidate_outside_tolerance_is_not_a_duplicate():
    await radar_series_dedup.record_and_check_duplicate("robinhood", 50554.14, now=_t(0))
    is_dup = await radar_series_dedup.record_and_check_duplicate(
        "robinhood", 21055.17, now=_t(60),
    )
    assert is_dup is False


async def test_candidate_outside_time_window_is_not_a_duplicate():
    await radar_series_dedup.record_and_check_duplicate(
        "robinhood", 50554.14, now=_t(0),
    )
    is_dup = await radar_series_dedup.record_and_check_duplicate(
        "robinhood", 50554.14, now=_t(0) + timedelta(seconds=3601),
        window_seconds=3600.0,
    )
    assert is_dup is False


async def test_chain_of_matches_keeps_working_after_first_duplicate_suppressed():
    """Every evaluated candidate is recorded, even a detected duplicate --
    otherwise the chain would stop matching after the first suppression."""
    await radar_series_dedup.record_and_check_duplicate("robinhood", 50554.14, now=_t(0))
    await radar_series_dedup.record_and_check_duplicate("robinhood", 50554.16, now=_t(60))
    is_dup = await radar_series_dedup.record_and_check_duplicate(
        "robinhood", 50554.18, now=_t(120),
    )
    assert is_dup is True


async def test_different_chains_never_cross_contaminate():
    await radar_series_dedup.record_and_check_duplicate("robinhood", 50554.14, now=_t(0))
    is_dup = await radar_series_dedup.record_and_check_duplicate(
        "base", 50554.14, now=_t(60),
    )
    assert is_dup is False


async def test_tolerance_is_overridable():
    await radar_series_dedup.record_and_check_duplicate("robinhood", 50554.00, now=_t(0))
    is_dup = await radar_series_dedup.record_and_check_duplicate(
        "robinhood", 50554.50, now=_t(60), tolerance_usd=1.0,
    )
    assert is_dup is True


async def test_none_reserve_never_a_duplicate_never_recorded():
    """Fail-closed on missing data, same doctrine as the rest of this
    pipeline -- never fabricate a comparison against an unknown value."""
    is_dup = await radar_series_dedup.record_and_check_duplicate(
        "robinhood", None, now=_t(0),
    )
    assert is_dup is False
    await radar_series_dedup._ensure_table()
    async with aiosqlite.connect(radar_series_dedup._db_path()) as db:
        cur = await db.execute(f"SELECT COUNT(*) FROM {radar_series_dedup.TABLE}")
        (count,) = await cur.fetchone()
    assert count == 0
