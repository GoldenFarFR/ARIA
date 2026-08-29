"""Tests for discovery_liquidity_observation.py (29/08, operator-directed).

Isolated tmp db, no network -- exercises record_observation() directly
rather than through check_candidates (see test_onchain_pool_discovery.py
for the integration-level tests confirming the call sites and the
unchanged qualification decision)."""
from __future__ import annotations

import aiosqlite
import pytest

from aria_core import discovery_liquidity_observation as m


@pytest.fixture
async def _tmp_db(tmp_path):
    path = str(tmp_path / "shadow.db")
    m._ensured_db_paths.clear()
    yield path
    m._ensured_db_paths.clear()


async def _rows(path):
    async with aiosqlite.connect(path) as c:
        c.row_factory = aiosqlite.Row
        cur = await c.execute(f"SELECT * FROM {m.TABLE}")
        return [dict(r) for r in await cur.fetchall()]


@pytest.mark.asyncio
async def test_a_qualified_observation_is_recorded_with_its_real_values(_tmp_db):
    await m.record_observation(
        chain="robinhood", pool_address="0xpool", token_address="0xtoken",
        reserve_usd=9000.0, price_usd=0.001, min_liquidity_usd=200.0,
        source="event", qualified=True, db_path=_tmp_db,
    )
    row = (await _rows(_tmp_db))[0]
    assert row["chain"] == "robinhood"
    assert row["pool_address"] == "0xpool"
    assert row["token_address"] == "0xtoken"
    assert row["reserve_usd"] == 9000.0
    assert row["price_usd"] == 0.001
    assert row["min_liquidity_usd"] == 200.0
    assert row["meets_liquidity_floor"] == 1
    assert row["source"] == "event"
    assert row["qualified"] == 1


@pytest.mark.asyncio
async def test_a_below_floor_observation_records_meets_floor_false(_tmp_db):
    await m.record_observation(
        chain="robinhood", pool_address="0xpool", token_address="0xtoken",
        reserve_usd=50.0, price_usd=0.001, min_liquidity_usd=200.0,
        source="cold_read", qualified=False, db_path=_tmp_db,
    )
    row = (await _rows(_tmp_db))[0]
    assert row["reserve_usd"] == 50.0
    assert row["meets_liquidity_floor"] == 0
    assert row["qualified"] == 0


@pytest.mark.asyncio
async def test_an_unpriceable_observation_stores_explicit_null_never_zero(_tmp_db):
    """The exact case the operator flagged: reserve_usd/price_usd unknown
    must land as SQL NULL, never a fabricated 0.0 that would be
    indistinguishable from a real zero-liquidity pool."""
    await m.record_observation(
        chain="robinhood", pool_address="0xpool", token_address="0xtoken",
        reserve_usd=None, price_usd=None, min_liquidity_usd=200.0,
        source=None, qualified=False, db_path=_tmp_db,
    )
    row = (await _rows(_tmp_db))[0]
    assert row["reserve_usd"] is None
    assert row["price_usd"] is None
    assert row["meets_liquidity_floor"] is None  # unknown, not "fails the floor"
    assert row["source"] is None
    assert row["qualified"] == 0


@pytest.mark.asyncio
async def test_a_logging_failure_never_raises(_tmp_db, monkeypatch):
    """Observation is best-effort: a DB error here must never propagate into
    check_candidates' own discovery cycle."""
    async def _boom(*_a, **_kw):
        raise RuntimeError("disk full")

    monkeypatch.setattr(m, "_ensure_table", _boom)
    await m.record_observation(
        chain="robinhood", pool_address="0xpool", token_address="0xtoken",
        reserve_usd=1.0, price_usd=1.0, min_liquidity_usd=200.0,
        source="event", qualified=False, db_path=_tmp_db,
    )  # must not raise
