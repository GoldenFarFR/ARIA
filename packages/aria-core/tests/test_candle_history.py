"""Persisted candle history (11/08) -- FIFO series per (chain, pool_address,
timeframe), fed by momentum_entry.py's passive hook + the future dedicated
watchlist cycle. Mirrors test_candle_staleness_shadow.py's structure (same
shadow/history append-only pattern, tmp DB fixture)."""
from __future__ import annotations

import pytest

from aria_core import candle_history
from aria_core.skills.ta_levels import Candle

POOL = "0x" + "p" * 40
CONTRACT = "0x" + "c" * 40


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(candle_history, "DB_PATH", str(tmp_path / "candle_history.db"))
    candle_history._ensured_db_paths.clear()
    yield
    candle_history._ensured_db_paths.clear()


def _candles(n: int, *, interval: int, start_ts: int = 1_700_000_000, price: float = 1.0) -> list[Candle]:
    return [
        Candle(ts=start_ts + i * interval, open=price, high=price, low=price, close=price, volume=1.0)
        for i in range(n)
    ]


# -- infer_timeframe (pure function, no DB) ---------------------------------

def test_infer_timeframe_matches_each_nominal_interval():
    assert candle_history.infer_timeframe(86400) == "1D"
    assert candle_history.infer_timeframe(14400) == "4H"
    assert candle_history.infer_timeframe(3600) == "1H"
    assert candle_history.infer_timeframe(1800) == "30M"
    assert candle_history.infer_timeframe(900) == "15M"
    assert candle_history.infer_timeframe(300) == "5M"


def test_infer_timeframe_tolerates_normal_jitter():
    # within the 25% tolerance band around 3600s (1H)
    assert candle_history.infer_timeframe(3600 * 1.1) == "1H"
    assert candle_history.infer_timeframe(3600 * 0.9) == "1H"


def test_infer_timeframe_none_when_no_match():
    assert candle_history.infer_timeframe(120) is None  # 2 minutes, no known granularity
    assert candle_history.infer_timeframe(None) is None
    assert candle_history.infer_timeframe(0) is None
    assert candle_history.infer_timeframe(-5) is None


# -- record_candles / get_history --------------------------------------------

@pytest.mark.asyncio
async def test_record_and_read_back_1h_series():
    candles = _candles(5, interval=3600)
    await candle_history.record_candles(
        "base", POOL, mode="standard", candles=candles,
        median_interval_seconds=3600, contract=CONTRACT,
    )
    rows = await candle_history.get_history("base", POOL, "1H")
    assert len(rows) == 5
    assert [r["ts"] for r in rows] == [c.ts for c in candles]  # oldest first
    assert rows[0]["contract"] == CONTRACT
    assert rows[0]["mode"] == "standard"


@pytest.mark.asyncio
async def test_record_is_idempotent_no_duplicate_rows():
    candles = _candles(5, interval=3600)
    await candle_history.record_candles(
        "base", POOL, mode="standard", candles=candles, median_interval_seconds=3600,
    )
    await candle_history.record_candles(
        "base", POOL, mode="standard", candles=candles, median_interval_seconds=3600,
    )
    rows = await candle_history.get_history("base", POOL, "1H")
    assert len(rows) == 5  # re-inserting the same candles changes nothing


@pytest.mark.asyncio
async def test_5m_excluded_from_persistence():
    candles = _candles(5, interval=300)
    await candle_history.record_candles(
        "base", POOL, mode="scalping_5m", candles=candles, median_interval_seconds=300,
    )
    rows = await candle_history.get_history("base", POOL, "5M")
    assert rows == []  # v9's default granularity is never persisted (operator decision)


@pytest.mark.asyncio
async def test_unrecognized_interval_never_persisted():
    candles = _candles(5, interval=120)
    await candle_history.record_candles(
        "base", POOL, mode="standard", candles=candles, median_interval_seconds=120,
    )
    # no known timeframe matches 120s -- nothing should have been written anywhere
    for tf in candle_history.FIFO_CAP_BY_TIMEFRAME:
        assert await candle_history.get_history("base", POOL, tf) == []


@pytest.mark.asyncio
async def test_different_timeframes_stay_in_independent_series():
    hourly = _candles(3, interval=3600, start_ts=1_700_000_000)
    daily = _candles(3, interval=86400, start_ts=1_700_000_000)
    await candle_history.record_candles(
        "base", POOL, mode="standard", candles=hourly, median_interval_seconds=3600,
    )
    await candle_history.record_candles(
        "base", POOL, mode="standard", candles=daily, median_interval_seconds=86400,
    )
    assert len(await candle_history.get_history("base", POOL, "1H")) == 3
    assert len(await candle_history.get_history("base", POOL, "1D")) == 3


@pytest.mark.asyncio
async def test_fifo_purge_respects_per_timeframe_cap(monkeypatch):
    monkeypatch.setitem(candle_history.FIFO_CAP_BY_TIMEFRAME, "1H", 10)
    candles = _candles(25, interval=3600)
    await candle_history.record_candles(
        "base", POOL, mode="standard", candles=candles, median_interval_seconds=3600,
    )
    rows = await candle_history.get_history("base", POOL, "1H")
    assert len(rows) == 10
    # the 10 most RECENT candles survive, oldest evicted
    assert [r["ts"] for r in rows] == [c.ts for c in candles[-10:]]


@pytest.mark.asyncio
async def test_fifo_purge_incremental_across_calls(monkeypatch):
    monkeypatch.setitem(candle_history.FIFO_CAP_BY_TIMEFRAME, "1H", 5)
    first = _candles(5, interval=3600, start_ts=1_700_000_000)
    second = _candles(5, interval=3600, start_ts=1_700_000_000 + 5 * 3600)
    await candle_history.record_candles(
        "base", POOL, mode="standard", candles=first, median_interval_seconds=3600,
    )
    await candle_history.record_candles(
        "base", POOL, mode="standard", candles=second, median_interval_seconds=3600,
    )
    rows = await candle_history.get_history("base", POOL, "1H")
    assert len(rows) == 5
    assert [r["ts"] for r in rows] == [c.ts for c in second]  # only the newest batch survives


@pytest.mark.asyncio
async def test_no_cap_configured_for_timeframe_never_purges():
    # 5M has no entry in FIFO_CAP_BY_TIMEFRAME at all -- but it's also
    # excluded from persistence entirely, so this documents the _purge_fifo
    # cap-lookup miss path directly rather than relying on that exclusion.
    await candle_history._purge_fifo("base", POOL, "5M")  # must not raise


@pytest.mark.asyncio
async def test_empty_candles_or_missing_pool_is_a_noop():
    await candle_history.record_candles(
        "base", "", mode="standard", candles=_candles(3, interval=3600), median_interval_seconds=3600,
    )
    await candle_history.record_candles(
        "base", POOL, mode="standard", candles=[], median_interval_seconds=3600,
    )
    assert await candle_history.get_history("base", POOL, "1H") == []


@pytest.mark.asyncio
async def test_record_failure_never_raises_into_caller(monkeypatch):
    async def _broken_connect(*_a, **_kw):
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(candle_history.aiosqlite, "connect", _broken_connect)
    # must not raise -- best-effort contract, same as every other shadow module
    await candle_history.record_candles(
        "base", POOL, mode="standard", candles=_candles(3, interval=3600), median_interval_seconds=3600,
    )


@pytest.mark.asyncio
async def test_get_history_limit_keeps_most_recent_oldest_first():
    candles = _candles(20, interval=3600)
    await candle_history.record_candles(
        "base", POOL, mode="standard", candles=candles, median_interval_seconds=3600,
    )
    rows = await candle_history.get_history("base", POOL, "1H", limit=5)
    assert len(rows) == 5
    assert [r["ts"] for r in rows] == [c.ts for c in candles[-5:]]
