"""Candle-freshness shadow observer (backlog #261, 10/08) -- append-only
log, never blocks. Mirrors test_wick_filter_shadow.py's structure (same
shadow pattern)."""
from __future__ import annotations

import pytest

from aria_core import candle_staleness_shadow

CONTRACT = "0x" + "c" * 40


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(candle_staleness_shadow, "DB_PATH", str(tmp_path / "shadow.db"))
    candle_staleness_shadow._ensured_db_paths.clear()
    yield
    candle_staleness_shadow._ensured_db_paths.clear()


@pytest.mark.asyncio
async def test_record_stale_age_would_flag():
    await candle_staleness_shadow.record_observation(
        CONTRACT, "base", mode="standard", source="fetch_candles",
        age_seconds=10_000.0, median_interval_seconds=60.0, symbol="TOK",
    )
    rows = await candle_staleness_shadow.list_recent()
    assert len(rows) == 1
    assert rows[0]["age_seconds"] == 10_000.0
    assert rows[0]["would_flag"] == 1
    assert rows[0]["mode"] == "standard"


@pytest.mark.asyncio
async def test_record_fresh_age_would_not_flag():
    await candle_staleness_shadow.record_observation(
        CONTRACT, "base", mode="scalping", source="fetch_candles",
        age_seconds=90.0, median_interval_seconds=60.0,
    )
    rows = await candle_staleness_shadow.list_recent()
    assert rows[0]["would_flag"] == 0


@pytest.mark.asyncio
async def test_record_exactly_at_multiplier_would_not_flag():
    # boundary: age == multiplier * median -- strictly-greater-than in the
    # implementation, so exactly-at-threshold stays unflagged.
    median = 60.0
    await candle_staleness_shadow.record_observation(
        CONTRACT, "base", mode="standard", source="fetch_candles",
        age_seconds=candle_staleness_shadow.STALENESS_SHADOW_MULTIPLIER * median,
        median_interval_seconds=median,
    )
    rows = await candle_staleness_shadow.list_recent()
    assert rows[0]["would_flag"] == 0


@pytest.mark.asyncio
async def test_record_unknown_median_never_fabricates_a_verdict():
    await candle_staleness_shadow.record_observation(
        CONTRACT, "base", mode="standard", source="fetch_candles",
        age_seconds=500.0, median_interval_seconds=None,
    )
    rows = await candle_staleness_shadow.list_recent()
    assert rows[0]["median_interval_seconds"] is None
    assert rows[0]["would_flag"] is None


@pytest.mark.asyncio
async def test_record_empty_contract_is_a_noop():
    await candle_staleness_shadow.record_observation(
        "", "base", mode="standard", source="fetch_candles",
        age_seconds=500.0, median_interval_seconds=60.0,
    )
    assert await candle_staleness_shadow.list_recent() == []


@pytest.mark.asyncio
async def test_record_never_raises_on_db_failure(monkeypatch):
    monkeypatch.setattr(candle_staleness_shadow, "DB_PATH", "/nonexistent/dir/shadow.db")
    candle_staleness_shadow._ensured_db_paths.clear()
    # must not raise into the caller's fetch path
    await candle_staleness_shadow.record_observation(
        CONTRACT, "base", mode="standard", source="fetch_candles",
        age_seconds=500.0, median_interval_seconds=60.0,
    )


@pytest.mark.asyncio
async def test_flagged_rate_computes_fraction_over_judged_rows():
    await candle_staleness_shadow.record_observation(
        CONTRACT, "base", mode="standard", source="fetch_candles",
        age_seconds=10_000.0, median_interval_seconds=60.0,
    )
    await candle_staleness_shadow.record_observation(
        CONTRACT, "base", mode="standard", source="fetch_candles",
        age_seconds=90.0, median_interval_seconds=60.0,
    )
    await candle_staleness_shadow.record_observation(
        CONTRACT, "base", mode="standard", source="fetch_candles",
        age_seconds=90.0, median_interval_seconds=60.0,
    )
    # unjudged row (unknown median) must never dilute the rate's denominator
    await candle_staleness_shadow.record_observation(
        CONTRACT, "base", mode="standard", source="fetch_candles",
        age_seconds=90.0, median_interval_seconds=None,
    )
    rate = await candle_staleness_shadow.flagged_rate()
    assert rate == pytest.approx(1 / 3)


@pytest.mark.asyncio
async def test_flagged_rate_none_when_nothing_judgeable():
    assert await candle_staleness_shadow.flagged_rate() is None
    await candle_staleness_shadow.record_observation(
        CONTRACT, "base", mode="standard", source="fetch_candles",
        age_seconds=90.0, median_interval_seconds=None,
    )
    assert await candle_staleness_shadow.flagged_rate() is None
