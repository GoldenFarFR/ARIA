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


class TestObservationPath:
    """22/08 -- the late-bonding pocket never honoured the 18/08 convention, so
    no position had a recorded path. That is what made `liquidity_collapse`
    (40.6% of the pocket's remaining loss, 87 trades at -63.98%) impossible to
    re-calibrate: the row keeps the reserve at entry and the last one seen, and
    nothing in between."""

    @pytest.fixture(autouse=True)
    def _fresh(self, tmp_path, monkeypatch):
        from aria_core import shadow_candle_archive as arch

        monkeypatch.setattr(arch, "DB_PATH", str(tmp_path / "shadow.db"))
        arch._ensured_db_paths.clear()
        arch._last_observation.clear()

    @pytest.mark.asyncio
    async def test_the_reserve_is_archived_alongside_the_price(self):
        from aria_core import shadow_candle_archive as arch

        await arch.store_observation(
            module="solana_late_bonding", position_id=1, pool_address="pool",
            chain="solana", price_usd=0.001, reserve_usd=6000.0, now_ts=1000.0,
        )
        rows = await arch.get_candles(module="solana_late_bonding", position_id=1)

        assert len(rows) == 1
        assert rows[0]["reserve_usd"] == 6000.0
        assert rows[0]["close"] == 0.001

    @pytest.mark.asyncio
    async def test_a_quiet_position_is_throttled(self):
        """235 positions at a 2s refresh is ~10M rows/day unthrottled."""
        from aria_core import shadow_candle_archive as arch

        written = 0
        for tick in range(10):  # 20 seconds of 2s ticks
            written += await arch.store_observation(
                module="solana_late_bonding", position_id=1, pool_address="pool",
                chain="solana", price_usd=0.001, reserve_usd=6000.0,
                now_ts=1000.0 + tick * 2,
            )

        assert written == 2, "the first point, then one per interval"

    @pytest.mark.asyncio
    async def test_a_collapsing_reserve_is_never_throttled_away(self):
        """The whole point: a collapse happens between two intervals, and it is
        the exact event being studied."""
        from aria_core import shadow_candle_archive as arch

        await arch.store_observation(
            module="solana_late_bonding", position_id=1, pool_address="pool",
            chain="solana", price_usd=0.001, reserve_usd=6000.0, now_ts=1000.0,
        )
        written = await arch.store_observation(
            module="solana_late_bonding", position_id=1, pool_address="pool",
            chain="solana", price_usd=0.0004, reserve_usd=2000.0, now_ts=1002.0,
        )

        assert written == 1, "a 67% reserve drop must be recorded immediately"

    @pytest.mark.asyncio
    async def test_the_first_point_of_a_position_is_always_kept(self):
        """Without a baseline a later drop cannot be measured against anything."""
        from aria_core import shadow_candle_archive as arch

        assert await arch.store_observation(
            module="solana_late_bonding", position_id=42, pool_address="pool",
            chain="solana", price_usd=0.001, reserve_usd=6000.0, now_ts=1.0,
        ) == 1

    @pytest.mark.asyncio
    async def test_closing_a_position_releases_its_throttle_state(self):
        from aria_core import shadow_candle_archive as arch

        await arch.store_observation(
            module="solana_late_bonding", position_id=7, pool_address="pool",
            chain="solana", price_usd=0.001, reserve_usd=6000.0, now_ts=1000.0,
        )
        assert ("solana_late_bonding", 7) in arch._last_observation

        arch.forget_position(module="solana_late_bonding", position_id=7)

        assert ("solana_late_bonding", 7) not in arch._last_observation

    @pytest.mark.asyncio
    async def test_archiving_never_breaks_the_caller(self, monkeypatch):
        """It runs inside a real exit path. A logging failure must never cost a
        position its stop."""
        from aria_core import shadow_candle_archive as arch

        monkeypatch.setattr(arch, "DB_PATH", "/nonexistent/dir/shadow.db")
        arch._ensured_db_paths.clear()

        assert await arch.store_observation(
            module="solana_late_bonding", position_id=1, pool_address="pool",
            chain="solana", price_usd=0.001, reserve_usd=6000.0, now_ts=1.0,
        ) == 0
