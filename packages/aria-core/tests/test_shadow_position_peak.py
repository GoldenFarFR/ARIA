"""Peak concurrent open-position tracker for the Solana/Robinhood pump
shadows (17/08, operator-requested)."""
from __future__ import annotations

import aiosqlite
import pytest

from aria_core import shadow_position_peak as peak


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "shadow_position_peak_test.db")
    monkeypatch.setattr(peak, "DB_PATH", db_path)
    return db_path


async def _make_position(db_path: str, table: str, chain: str, *, exit_reason=None):
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {table} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pool_address TEXT NOT NULL,
                chain TEXT NOT NULL,
                detected_at TEXT NOT NULL,
                entry_price REAL NOT NULL,
                exit_reason TEXT
            )
            """
        )
        await db.execute(
            f"INSERT INTO {table} (pool_address, chain, detected_at, entry_price, exit_reason) "
            "VALUES (?, ?, ?, ?, ?)",
            (f"pool-{chain}-{exit_reason}", chain, "2026-08-17T00:00:00+00:00", 1.0, exit_reason),
        )
        await db.commit()


@pytest.mark.asyncio
async def test_get_peak_defaults_to_zero_when_never_recorded():
    count, at = await peak.get_peak("solana")
    assert count == 0
    assert at is None


@pytest.mark.asyncio
async def test_current_open_count_excludes_closed_positions(_isolated_db):
    for _ in range(3):
        await _make_position(_isolated_db, "solana_pump_shadow_log", "solana")
    await _make_position(_isolated_db, "solana_pump_shadow_log", "solana", exit_reason="trailing_stop")
    assert await peak.current_open_count("solana") == 3


@pytest.mark.asyncio
async def test_check_and_record_peak_widens_on_new_high(_isolated_db):
    for _ in range(2):
        await _make_position(_isolated_db, "solana_pump_shadow_log", "solana")
    open_count, stored_peak = await peak.check_and_record_peak("solana")
    assert open_count == 2
    assert stored_peak == 2

    await _make_position(_isolated_db, "solana_pump_shadow_log", "solana")
    open_count, stored_peak = await peak.check_and_record_peak("solana")
    assert open_count == 3
    assert stored_peak == 3


@pytest.mark.asyncio
async def test_check_and_record_peak_never_shrinks_on_lower_count(_isolated_db):
    for _ in range(5):
        await _make_position(_isolated_db, "solana_pump_shadow_log", "solana")
    await peak.check_and_record_peak("solana")

    # simulate closures: only 1 position stays open
    async with aiosqlite.connect(_isolated_db) as db:
        await db.execute(
            "UPDATE solana_pump_shadow_log SET exit_reason = 'trailing_stop' WHERE id > 1"
        )
        await db.commit()

    open_count, stored_peak = await peak.check_and_record_peak("solana")
    assert open_count == 1
    assert stored_peak == 5  # peak untouched despite the drop


@pytest.mark.asyncio
async def test_chains_are_tracked_independently(_isolated_db):
    for _ in range(4):
        await _make_position(_isolated_db, "solana_pump_shadow_log", "solana")
    await _make_position(_isolated_db, "robinhood_pump_shadow_log", "robinhood")

    await peak.check_and_record_peak("solana")
    await peak.check_and_record_peak("robinhood")

    solana_count, _ = await peak.get_peak("solana")
    robinhood_count, _ = await peak.get_peak("robinhood")
    assert solana_count == 4
    assert robinhood_count == 1


@pytest.mark.asyncio
async def test_seed_peak_if_higher_applies_only_when_it_exceeds_stored():
    await peak.seed_peak_if_higher("solana", 235, "2026-08-17T04:06:51+00:00")
    count, at = await peak.get_peak("solana")
    assert count == 235
    assert at == "2026-08-17T04:06:51+00:00"

    # a lower seed must never overwrite a higher stored peak
    await peak.seed_peak_if_higher("solana", 10, "2026-08-17T12:00:00+00:00")
    count, at = await peak.get_peak("solana")
    assert count == 235
    assert at == "2026-08-17T04:06:51+00:00"

    # a higher seed still widens it
    await peak.seed_peak_if_higher("solana", 300, "2026-08-18T00:00:00+00:00")
    count, at = await peak.get_peak("solana")
    assert count == 300
