"""Shared candle archive (18/08) -- store_candles/get_candles, dedup on
(module, position_id, phase, candle_ts), never raises into the caller."""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from aria_core import shadow_candle_archive as archive


@dataclass(frozen=True)
class _FakeCandle:
    ts: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@pytest.fixture(autouse=True)
async def _tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(archive, "DB_PATH", str(tmp_path / "shadow.db"))
    archive._ensured_db_paths.clear()
    await archive._ensure_table()
    yield
    archive._ensured_db_paths.clear()


def _candles(n: int, start_ts: int = 1000) -> list[_FakeCandle]:
    return [
        _FakeCandle(ts=start_ts + i * 300, open=1.0 + i, high=1.1 + i, low=0.9 + i, close=1.05 + i, volume=10.0)
        for i in range(n)
    ]


async def test_store_and_read_back_before_phase():
    stored = await archive.store_candles(
        module="solana_support_bounce", position_id=1, pool_address="pool1",
        chain="solana", phase="before", candles=_candles(5),
    )
    assert stored == 5
    rows = await archive.get_candles(module="solana_support_bounce", position_id=1, phase="before")
    assert len(rows) == 5
    assert rows[0]["candle_ts"] == 1000
    assert rows[-1]["candle_ts"] == 1000 + 4 * 300


async def test_before_and_after_phases_are_independent():
    await archive.store_candles(
        module="solana_support_bounce", position_id=2, pool_address="pool2",
        chain="solana", phase="before", candles=_candles(3, start_ts=0),
    )
    await archive.store_candles(
        module="solana_support_bounce", position_id=2, pool_address="pool2",
        chain="solana", phase="after", candles=_candles(2, start_ts=2000),
    )
    before = await archive.get_candles(module="solana_support_bounce", position_id=2, phase="before")
    after = await archive.get_candles(module="solana_support_bounce", position_id=2, phase="after")
    both = await archive.get_candles(module="solana_support_bounce", position_id=2)
    assert len(before) == 3
    assert len(after) == 2
    assert len(both) == 5


async def test_repeated_after_store_never_duplicates_overlapping_candles():
    # Simulates advance_exit_simulation calling store_candles on every check
    # with a growing/overlapping window -- the SAME candle_ts must never be
    # duplicated across repeated calls.
    first_batch = _candles(3, start_ts=5000)
    second_batch = _candles(5, start_ts=5000)  # includes the first 3 again + 2 new
    await archive.store_candles(
        module="solana_support_bounce_v2", position_id=7, pool_address="poolX",
        chain="solana", phase="after", candles=first_batch,
    )
    inserted_second = await archive.store_candles(
        module="solana_support_bounce_v2", position_id=7, pool_address="poolX",
        chain="solana", phase="after", candles=second_batch,
    )
    assert inserted_second == 2  # only the 2 genuinely new ones
    rows = await archive.get_candles(module="solana_support_bounce_v2", position_id=7, phase="after")
    assert len(rows) == 5


async def test_different_modules_never_collide_on_same_position_id():
    await archive.store_candles(
        module="solana_support_bounce", position_id=1, pool_address="poolA",
        chain="solana", phase="before", candles=_candles(2, start_ts=9000),
    )
    await archive.store_candles(
        module="solana_support_bounce_v2", position_id=1, pool_address="poolB",
        chain="solana", phase="before", candles=_candles(4, start_ts=9000),
    )
    v1_rows = await archive.get_candles(module="solana_support_bounce", position_id=1, phase="before")
    v2_rows = await archive.get_candles(module="solana_support_bounce_v2", position_id=1, phase="before")
    assert len(v1_rows) == 2
    assert len(v2_rows) == 4


async def test_empty_candles_list_is_a_noop():
    stored = await archive.store_candles(
        module="solana_support_bounce", position_id=99, pool_address="pool99",
        chain="solana", phase="before", candles=[],
    )
    assert stored == 0
    rows = await archive.get_candles(module="solana_support_bounce", position_id=99)
    assert rows == []


async def test_store_candles_never_raises_on_db_failure(monkeypatch):
    async def _broken_connect(*args, **kwargs):
        raise RuntimeError("simulated db outage")

    import aiosqlite

    monkeypatch.setattr(aiosqlite, "connect", _broken_connect)
    stored = await archive.store_candles(
        module="solana_support_bounce", position_id=1, pool_address="pool1",
        chain="solana", phase="before", candles=_candles(2),
    )
    assert stored == 0
