"""5-minute candle granularity shadow (17/08, operator question after the
age_limit fix: would 5min candles have caught a stop-breach earlier than
15min). Mirrors test_wick_filter_shadow.py's test pattern exactly."""
from __future__ import annotations

import pytest

from aria_core import candle_granularity_shadow as cgs


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "candle_granularity_shadow_test.db")
    monkeypatch.setattr(cgs, "DB_PATH", db_path)
    cgs._ensured_db_paths.clear()
    return db_path


@pytest.mark.asyncio
async def test_record_and_list_round_trip():
    await cgs.record_comparison(
        "pool1", "solana", symbol="TEST",
        window_low_15m=0.75, window_low_5m=0.70, stop_threshold=0.80,
    )
    rows = await cgs.list_recent()
    assert len(rows) == 1
    assert rows[0]["pool_address"] == "pool1"
    assert rows[0]["would_15m_have_caught"] == 1  # 0.75 <= 0.80
    assert rows[0]["would_5m_have_caught"] == 1    # 0.70 <= 0.80
    assert rows[0]["five_min_available"] == 1


@pytest.mark.asyncio
async def test_5m_catches_a_breach_15m_missed():
    """The exact case this shadow exists to detect: 15min's coarser window
    averages over the dip and misses the stop line; 5min's finer window
    catches it."""
    await cgs.record_comparison(
        "pool1", "solana", symbol="TEST",
        window_low_15m=0.85, window_low_5m=0.78, stop_threshold=0.80,
    )
    rows = await cgs.list_recent()
    assert rows[0]["would_15m_have_caught"] == 0
    assert rows[0]["would_5m_have_caught"] == 1


@pytest.mark.asyncio
async def test_missing_5m_data_is_never_fabricated():
    """A young pool without enough trades to fill a 5min bucket -- must be
    recorded as unavailable, never silently treated as 'did not breach'."""
    await cgs.record_comparison(
        "pool1", "solana", symbol="TEST",
        window_low_15m=0.75, window_low_5m=None, stop_threshold=0.80,
    )
    rows = await cgs.list_recent()
    assert rows[0]["would_5m_have_caught"] is None
    assert rows[0]["five_min_available"] == 0


@pytest.mark.asyncio
async def test_record_comparison_never_raises_on_db_failure(monkeypatch):
    monkeypatch.setattr(cgs, "_db_path", lambda: "/nonexistent/dir/no.db")
    await cgs.record_comparison(
        "pool1", "solana", symbol="TEST",
        window_low_15m=0.75, window_low_5m=0.70, stop_threshold=0.80,
    )  # must not raise


@pytest.mark.asyncio
async def test_record_comparison_ignores_blank_pool_address():
    await cgs.record_comparison(
        "", "solana", symbol="TEST",
        window_low_15m=0.75, window_low_5m=0.70, stop_threshold=0.80,
    )
    assert await cgs.list_recent() == []


@pytest.mark.asyncio
async def test_divergence_summary_on_empty_table():
    summary = await cgs.divergence_summary()
    assert summary == {
        "total_observations": 0,
        "five_min_available": 0,
        "five_min_caught_a_breach_15min_missed": 0,
        "fifteen_min_caught_a_breach_5min_missed": 0,
    }


@pytest.mark.asyncio
async def test_divergence_summary_counts_each_direction_independently():
    # 5min catches what 15min missed.
    await cgs.record_comparison("p1", "solana", symbol="A", window_low_15m=0.85, window_low_5m=0.78, stop_threshold=0.80)
    # 15min catches what 5min missed (a spike between 5min checks smoothed by then).
    await cgs.record_comparison("p2", "solana", symbol="B", window_low_15m=0.78, window_low_5m=0.85, stop_threshold=0.80)
    # Both agree -- neither direction counted.
    await cgs.record_comparison("p3", "solana", symbol="C", window_low_15m=0.75, window_low_5m=0.75, stop_threshold=0.80)
    # No 5min data at all -- excluded from both divergence counts.
    await cgs.record_comparison("p4", "solana", symbol="D", window_low_15m=0.75, window_low_5m=None, stop_threshold=0.80)

    summary = await cgs.divergence_summary()
    assert summary["total_observations"] == 4
    assert summary["five_min_available"] == 3
    assert summary["five_min_caught_a_breach_15min_missed"] == 1
    assert summary["fifteen_min_caught_a_breach_5min_missed"] == 1
