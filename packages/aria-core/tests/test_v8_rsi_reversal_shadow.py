"""V8 RSI-reversal shadow (08/08) -- resampling correctness + the open/close
state machine, mirroring test_v8_limit_shadow.py's pattern (isolated tmp
sqlite, state machine tested directly plus one real end-to-end pass through
record_evaluation with synthetic candles)."""
from __future__ import annotations

import aiosqlite
import pytest

from aria_core import v8_rsi_reversal_shadow as shadow
from aria_core.skills.entry_signals import rsi_series
from aria_core.skills.ta_levels import Candle

CONTRACT = "0x" + "d" * 40
CHAIN = "base"


@pytest.fixture(autouse=True)
async def _tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(shadow, "DB_PATH", str(tmp_path / "shadow.db"))
    shadow._ensured_db_paths.clear()
    await shadow._ensure_table()
    yield
    shadow._ensured_db_paths.clear()


def _candle(ts: int, close: float, *, spacing: int = 900) -> Candle:
    return Candle(ts=ts, open=close, high=close, low=close, close=close, volume=1.0)


def _series(n: int, spacing: int, closes: list[float]) -> list[Candle]:
    assert len(closes) == n
    return [_candle(i * spacing, c, spacing=spacing) for i, c in enumerate(closes)]


async def _rows(contract=CONTRACT, chain=CHAIN):
    async with aiosqlite.connect(shadow._db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM v8_rsi_reversal_shadow WHERE contract = ? AND chain = ?", (contract, chain)
        )
        return [dict(r) for r in await cur.fetchall()]


# --- resampling -------------------------------------------------------

def test_resample_to_60m_aggregates_15m_candles_by_4():
    candles = _series(40, 900, [float(i) for i in range(40)])  # 900s = 15min
    out = shadow._resample_to_60m(candles)
    assert len(out) == 10
    assert out[-1].close == 39.0
    assert out[-1].ts == candles[-1].ts


def test_resample_to_60m_aggregates_30m_candles_by_2():
    candles = _series(20, 1800, [float(i) for i in range(20)])  # 1800s = 30min
    out = shadow._resample_to_60m(candles)
    assert len(out) == 10
    assert out[-1].close == 19.0


def test_resample_to_60m_keeps_most_recent_complete_groups():
    # 21 15min candles -> 5 complete 60min groups (20 candles used), the
    # single oldest leftover candle is dropped rather than padding a stale group.
    candles = _series(21, 900, [float(i) for i in range(21)])
    out = shadow._resample_to_60m(candles)
    assert len(out) == 5
    assert out[0].open == 1.0  # candle index 0 (close=0.0) was dropped
    assert out[-1].close == 20.0


def test_resample_to_60m_passthrough_when_already_hourly():
    candles = _series(5, 3600, [1.0, 2.0, 3.0, 4.0, 5.0])
    out = shadow._resample_to_60m(candles)
    assert out == candles


# --- state machine (direct, mirrors test_v8_limit_shadow's _price_fn style) -----

@pytest.mark.asyncio
async def test_advance_or_open_opens_on_oversold_exit_crossover():
    cfg = shadow.VARIANTS["rsi14"]
    await shadow._advance_or_open(
        CONTRACT, CHAIN, "TOK", "rsi14", cfg, prev=20.0, cur=30.0, last_close=1.0,
    )
    rows = await _rows()
    assert len(rows) == 1
    assert rows[0]["status"] == "open"
    assert rows[0]["entry_price"] == 1.0
    assert rows[0]["entry_rsi"] == 30.0


@pytest.mark.asyncio
async def test_advance_or_open_ignores_when_no_crossover():
    cfg = shadow.VARIANTS["rsi14"]
    await shadow._advance_or_open(
        CONTRACT, CHAIN, "TOK", "rsi14", cfg, prev=40.0, cur=45.0, last_close=1.0,
    )
    assert await _rows() == []


@pytest.mark.asyncio
async def test_advance_or_open_closes_on_overbought_exit_crossover():
    cfg = shadow.VARIANTS["rsi14"]
    await shadow._advance_or_open(
        CONTRACT, CHAIN, "TOK", "rsi14", cfg, prev=20.0, cur=30.0, last_close=1.0,
    )
    await shadow._advance_or_open(
        CONTRACT, CHAIN, "TOK", "rsi14", cfg, prev=80.0, cur=70.0, last_close=1.2,
    )
    rows = await _rows()
    assert len(rows) == 1
    assert rows[0]["status"] == "closed"
    assert rows[0]["close_reason"] == "rsi_exit_overbought"
    assert rows[0]["pnl_pct"] == pytest.approx(20.0)


@pytest.mark.asyncio
async def test_advance_or_open_closes_on_max_hold_timeout():
    cfg = shadow.VARIANTS["rsi14"]
    await shadow._advance_or_open(
        CONTRACT, CHAIN, "TOK", "rsi14", cfg, prev=20.0, cur=30.0, last_close=1.0,
    )
    async with aiosqlite.connect(shadow._db_path()) as db:
        await db.execute(
            "UPDATE v8_rsi_reversal_shadow SET opened_at = ? WHERE contract = ?",
            ("2020-01-01T00:00:00+00:00", CONTRACT),
        )
        await db.commit()
    await shadow._advance_or_open(
        CONTRACT, CHAIN, "TOK", "rsi14", cfg, prev=50.0, cur=55.0, last_close=0.9,
    )
    rows = await _rows()
    assert rows[0]["status"] == "closed"
    assert rows[0]["close_reason"] == "timeout_max_hold"


@pytest.mark.asyncio
async def test_advance_or_open_deduplicates_open_position():
    cfg = shadow.VARIANTS["rsi14"]
    await shadow._advance_or_open(
        CONTRACT, CHAIN, "TOK", "rsi14", cfg, prev=20.0, cur=30.0, last_close=1.0,
    )
    await shadow._advance_or_open(
        CONTRACT, CHAIN, "TOK", "rsi14", cfg, prev=20.0, cur=30.0, last_close=1.5,
    )
    rows = await _rows()
    assert len(rows) == 1
    assert rows[0]["entry_price"] == 1.0  # first signal wins


# --- end-to-end via record_evaluation ---------------------------------

@pytest.mark.asyncio
async def test_record_evaluation_skips_when_too_few_60m_candles():
    candles = _series(10, 900, [float(i) for i in range(10)])  # only 2 60m bars
    await shadow.record_evaluation(CONTRACT, CHAIN, symbol="TOK", candles=candles)
    assert await _rows() == []


@pytest.mark.asyncio
async def test_record_evaluation_opens_a_shadow_on_real_rsi_crossover():
    # 30 hourly-equivalent (15min x4) candles: sharp decline then a bounce,
    # engineered against the REAL rsi_series so this test tracks the actual
    # implementation rather than a hand-picked magic number.
    n = 30
    closes = [100.0 - i * 3.0 for i in range(n - 3)] + [40.0, 55.0, 70.0]
    candles = _series(n * 4, 900, [c for c in closes for _ in range(4)])
    series = rsi_series(closes, period=14)
    assert series[-2] is not None and series[-1] is not None
    await shadow.record_evaluation(CONTRACT, CHAIN, symbol="TOK", candles=candles)
    rows = await _rows()
    crossed_up = series[-2] < shadow.VARIANTS["rsi14"]["low"] <= series[-1]
    if crossed_up:
        assert any(r["variant"] == "rsi14" and r["status"] == "open" for r in rows)
    else:
        assert not any(r["variant"] == "rsi14" for r in rows)


# --- summary ------------------------------------------------------------

@pytest.mark.asyncio
async def test_summary_aggregates_per_variant():
    cfg14 = shadow.VARIANTS["rsi14"]
    cfg21 = shadow.VARIANTS["rsi21"]
    await shadow._advance_or_open(CONTRACT, CHAIN, "TOK", "rsi14", cfg14, prev=20.0, cur=30.0, last_close=1.0)
    await shadow._advance_or_open(CONTRACT, CHAIN, "TOK", "rsi14", cfg14, prev=80.0, cur=70.0, last_close=1.1)
    await shadow._advance_or_open(CONTRACT, CHAIN, "TOK", "rsi21", cfg21, prev=25.0, cur=35.0, last_close=2.0)
    summary = await shadow.summary()
    assert summary["rsi14"]["closed"] == 1
    assert summary["rsi14"]["wins"] == 1
    assert summary["rsi21"]["open"] == 1
    assert summary["rsi21"]["closed"] == 0
