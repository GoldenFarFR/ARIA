"""Macro regime sensor (26/08, specs/008-solana-regime-macro-gate Part 1) --
OBSERVATION ONLY, isolated tmp db, no network."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import aiosqlite
import pytest

from aria_core import solana_macro_regime as sensor


@pytest.fixture(autouse=True)
async def _tmp_db(tmp_path, monkeypatch):
    path = str(tmp_path / "shadow.db")
    monkeypatch.setattr(sensor, "_db_path", lambda: path)
    sensor._ensured_db_paths.clear()
    yield path


@pytest.mark.asyncio
async def test_graduations_per_hour_is_none_with_no_data():
    """An empty window means "no measurement," never a fabricated zero --
    a caller that treats None as 0 would wrongly read a quiet sensor as a
    dead market."""
    assert await sensor.graduations_per_hour() is None


@pytest.mark.asyncio
async def test_recording_a_graduation_never_raises_on_a_broken_db(monkeypatch):
    monkeypatch.setattr(sensor, "_db_path", lambda: "/nonexistent/dir/shadow.db")
    await sensor.record_graduation("mintX")  # must not raise


@pytest.mark.asyncio
async def test_graduations_per_hour_counts_only_rows_inside_the_window(_tmp_db):
    await sensor._ensure_table(_tmp_db)
    now = datetime.now(timezone.utc)
    recent = (now - timedelta(minutes=10)).isoformat()
    stale = (now - timedelta(minutes=120)).isoformat()
    async with aiosqlite.connect(_tmp_db) as db:
        await db.execute(
            f"INSERT INTO {sensor.TABLE} (mint, chain, graduated_at) VALUES (?, ?, ?)",
            ("mintA", "solana", recent),
        )
        await db.execute(
            f"INSERT INTO {sensor.TABLE} (mint, chain, graduated_at) VALUES (?, ?, ?)",
            ("mintB", "solana", stale),
        )
        await db.commit()

    rate = await sensor.graduations_per_hour(window_minutes=60.0, db_path=_tmp_db)
    assert rate == 1.0  # 1 graduation inside a 60-minute window -> 1/hour


@pytest.mark.asyncio
async def test_record_graduation_is_readable_back(_tmp_db):
    await sensor.record_graduation("mintZ", chain="solana", db_path=_tmp_db)
    rate = await sensor.graduations_per_hour(window_minutes=60.0, db_path=_tmp_db)
    assert rate == 1.0


@pytest.mark.asyncio
async def test_the_same_mint_is_never_counted_twice(_tmp_db):
    """The tracker keeps polling a mint above the threshold until it goes
    stale -- without the UNIQUE constraint this sensor would inflate the
    rate by however many times a single graduation got re-polled."""
    await sensor.record_graduation("mintDup", db_path=_tmp_db)
    await sensor.record_graduation("mintDup", db_path=_tmp_db)
    await sensor.record_graduation("mintDup", db_path=_tmp_db)
    rate = await sensor.graduations_per_hour(window_minutes=60.0, db_path=_tmp_db)
    assert rate == 1.0


@pytest.mark.asyncio
async def test_graduation_threshold_matches_the_pockets_own_entry_ceiling():
    """Consistency guard -- this sensor's "graduated" definition must never
    silently drift from the pocket's own entry-window ceiling (specs/008:
    reuse the existing constant, never a second uncoordinated threshold)."""
    from aria_core.solana_late_bonding_shadow import MAX_BONDING_PROGRESS
    assert sensor.GRADUATION_THRESHOLD == MAX_BONDING_PROGRESS
