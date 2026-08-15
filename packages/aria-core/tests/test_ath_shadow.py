"""ATH shadow persistence (15/08) -- append-only, never blocks. Mirrors
test_candle_staleness_shadow.py's structure (same shadow pattern)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from aria_core import ath_shadow

POOL = "0x" + "p" * 40


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(ath_shadow, "DB_PATH", str(tmp_path / "shadow.db"))
    ath_shadow._ensured_db_paths.clear()
    yield
    ath_shadow._ensured_db_paths.clear()


@pytest.mark.asyncio
async def test_first_scan_has_no_previous_and_is_not_a_violation():
    await ath_shadow.record_scan(
        POOL, "base", ath_price=1.5, ath_at=datetime.now(timezone.utc),
        scanned_until_ts=1000, pages_scanned=3,
    )
    rows = await ath_shadow.list_recent()
    assert len(rows) == 1
    assert rows[0]["previous_ath_price"] is None
    assert rows[0]["invariant_violated"] == 0
    assert rows[0]["new_ath_price"] == 1.5

    cached = await ath_shadow.get_cached(POOL, "base")
    assert cached["ath_price"] == 1.5
    assert cached["pages_scanned"] == 3


@pytest.mark.asyncio
async def test_higher_rescan_updates_cache_without_violation():
    await ath_shadow.record_scan(
        POOL, "base", ath_price=1.5, ath_at=None, scanned_until_ts=1000, pages_scanned=3,
    )
    await ath_shadow.record_scan(
        POOL, "base", ath_price=2.0, ath_at=None, scanned_until_ts=1000, pages_scanned=1,
    )
    rows = await ath_shadow.list_recent()
    assert rows[0]["previous_ath_price"] == 1.5
    assert rows[0]["new_ath_price"] == 2.0
    assert rows[0]["invariant_violated"] == 0

    cached = await ath_shadow.get_cached(POOL, "base")
    assert cached["ath_price"] == 2.0


@pytest.mark.asyncio
async def test_lower_rescan_flags_invariant_violation():
    await ath_shadow.record_scan(
        POOL, "base", ath_price=2.0, ath_at=None, scanned_until_ts=1000, pages_scanned=3,
    )
    await ath_shadow.record_scan(
        POOL, "base", ath_price=1.5, ath_at=None, scanned_until_ts=1000, pages_scanned=1,
    )
    rows = await ath_shadow.list_recent()
    assert rows[0]["previous_ath_price"] == 2.0
    assert rows[0]["new_ath_price"] == 1.5
    assert rows[0]["invariant_violated"] == 1

    # the cache still tracks the LATEST scan, not the highest ever seen --
    # shadow-only, never used to make a real decision yet.
    cached = await ath_shadow.get_cached(POOL, "base")
    assert cached["ath_price"] == 1.5


@pytest.mark.asyncio
async def test_distinct_pools_are_tracked_independently():
    await ath_shadow.record_scan(
        POOL, "base", ath_price=1.5, ath_at=None, scanned_until_ts=1000, pages_scanned=1,
    )
    await ath_shadow.record_scan(
        POOL, "solana", ath_price=99.0, ath_at=None, scanned_until_ts=1000, pages_scanned=1,
    )
    assert (await ath_shadow.get_cached(POOL, "base"))["ath_price"] == 1.5
    assert (await ath_shadow.get_cached(POOL, "solana"))["ath_price"] == 99.0


@pytest.mark.asyncio
async def test_empty_pool_address_is_a_noop():
    await ath_shadow.record_scan(
        "", "base", ath_price=1.5, ath_at=None, scanned_until_ts=1000, pages_scanned=1,
    )
    assert await ath_shadow.list_recent() == []


@pytest.mark.asyncio
async def test_get_cached_missing_pool_returns_none():
    assert await ath_shadow.get_cached(POOL, "base") is None


@pytest.mark.asyncio
async def test_record_never_raises_on_db_failure(monkeypatch):
    monkeypatch.setattr(ath_shadow, "DB_PATH", "/nonexistent/dir/shadow.db")
    ath_shadow._ensured_db_paths.clear()
    await ath_shadow.record_scan(
        POOL, "base", ath_price=1.5, ath_at=None, scanned_until_ts=1000, pages_scanned=1,
    )


@pytest.mark.asyncio
async def test_violation_rate_computes_fraction_over_judged_rows():
    await ath_shadow.record_scan(
        POOL, "base", ath_price=2.0, ath_at=None, scanned_until_ts=1000, pages_scanned=1,
    )
    # violation
    await ath_shadow.record_scan(
        POOL, "base", ath_price=1.0, ath_at=None, scanned_until_ts=1000, pages_scanned=1,
    )
    # not a violation
    await ath_shadow.record_scan(
        POOL, "base", ath_price=3.0, ath_at=None, scanned_until_ts=1000, pages_scanned=1,
    )
    rate = await ath_shadow.violation_rate()
    # first scan has no previous value (unjudged); of the 2 judged rescans, 1 violated
    assert rate == pytest.approx(1 / 2)


@pytest.mark.asyncio
async def test_violation_rate_none_when_nothing_judgeable():
    assert await ath_shadow.violation_rate() is None
    await ath_shadow.record_scan(
        POOL, "base", ath_price=1.5, ath_at=None, scanned_until_ts=1000, pages_scanned=1,
    )
    # only a first-ever scan recorded -- no previous value to judge against
    assert await ath_shadow.violation_rate() is None
