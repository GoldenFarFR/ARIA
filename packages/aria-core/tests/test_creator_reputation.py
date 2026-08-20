"""Per-creator reputation (20/08). Isolated tmp db, no network."""
from __future__ import annotations

import aiosqlite
import pytest

from aria_core import creator_reputation as rep

SEEN = "2026-08-20T20:00:00+00:00"


@pytest.fixture
async def db(tmp_path):
    path = str(tmp_path / "shadow.db")
    rep._ensured_db_paths.clear()
    await rep._ensure_table(path)
    yield path
    rep._ensured_db_paths.clear()


@pytest.mark.asyncio
async def test_repeated_launches_accumulate_on_the_same_creator(db):
    for _ in range(3):
        await rep.record_creator("devA", seen_at=SEEN, db_path=db)

    stats = await rep.get_stats("devA", db_path=db)
    assert stats.tokens_seen == 3
    assert stats.is_factory is False  # below the measured cliff


@pytest.mark.asyncio
async def test_a_creator_at_the_threshold_is_flagged_a_factory(db):
    for _ in range(rep.MIN_TOKENS_FOR_FACTORY):
        await rep.record_creator("devFactory", seen_at=SEEN, db_path=db)

    assert await rep.is_factory("devFactory", db_path=db) is True


@pytest.mark.asyncio
async def test_an_unknown_creator_fails_OPEN(db):
    """Unlike the holder gate, this one must fail OPEN: never having seen a
    creator is the NORMAL state of a genuinely new builder. Rejecting on it
    would block every real fresh launch -- the opposite of the intent."""
    assert await rep.is_factory("neverSeen", db_path=db) is False
    assert await rep.get_stats("neverSeen", db_path=db) is None


@pytest.mark.asyncio
async def test_a_missing_creator_is_never_treated_as_a_verdict(db):
    assert await rep.is_factory(None, db_path=db) is False
    assert await rep.get_stats(None, db_path=db) is None


@pytest.mark.asyncio
async def test_recording_never_raises_into_the_enrichment_task(tmp_path):
    await rep.record_creator("devA", seen_at=SEEN, db_path=str(tmp_path / "nope" / "x.db"))


@pytest.mark.asyncio
async def test_a_read_failure_never_blocks_an_entry(tmp_path):
    """A broken reputation DB must let the trade through, not stall it."""
    assert await rep.is_factory("devA", db_path=str(tmp_path / "nope" / "x.db")) is False


@pytest.mark.asyncio
async def test_backfill_seeds_from_existing_closed_positions(db):
    async with aiosqlite.connect(db) as c:
        await c.execute(
            """CREATE TABLE solana_fresh_launch_fast_discovery_shadow_log
               (id INTEGER PRIMARY KEY, rugcheck_creator TEXT, detected_at TEXT)"""
        )
        await c.executemany(
            "INSERT INTO solana_fresh_launch_fast_discovery_shadow_log (rugcheck_creator, detected_at) VALUES (?, ?)",
            [("devFactory", SEEN)] * 5 + [("devSolo", SEEN)],
        )
        await c.commit()

    assert await rep.backfill_from_closed_positions(db_path=db) == 2
    assert await rep.is_factory("devFactory", db_path=db) is True
    assert await rep.is_factory("devSolo", db_path=db) is False


@pytest.mark.asyncio
async def test_backfill_is_idempotent(db):
    """Run twice, counts must not double -- otherwise every restart would
    inflate every creator toward the factory threshold."""
    async with aiosqlite.connect(db) as c:
        await c.execute(
            """CREATE TABLE solana_fresh_launch_fast_discovery_shadow_log
               (id INTEGER PRIMARY KEY, rugcheck_creator TEXT, detected_at TEXT)"""
        )
        await c.executemany(
            "INSERT INTO solana_fresh_launch_fast_discovery_shadow_log (rugcheck_creator, detected_at) VALUES (?, ?)",
            [("devSolo", SEEN)] * 2,
        )
        await c.commit()

    await rep.backfill_from_closed_positions(db_path=db)
    await rep.backfill_from_closed_positions(db_path=db)

    stats = await rep.get_stats("devSolo", db_path=db)
    assert stats.tokens_seen == 2  # not 4


@pytest.mark.asyncio
async def test_backfill_survives_a_pocket_table_that_does_not_exist(db):
    assert await rep.backfill_from_closed_positions(db_path=db) == 0
