"""Continuous integrity checks on the shadow pockets' own data (20/08).

Three measurement bugs were found by hand on 20/08, each only because someone
looked at a number that felt wrong. These tests lock the checks that make the
same class of defect surface on its own."""
from __future__ import annotations

import aiosqlite
import pytest

from aria_core import shadow_data_integrity as integrity

TABLE = integrity.POCKET_TABLES["late_bonding"]
RECENT = "2026-08-20T23:30:00+00:00"


@pytest.fixture
async def db(tmp_path):
    path = str(tmp_path / "shadow.db")
    async with aiosqlite.connect(path) as c:
        await c.execute(
            f"""CREATE TABLE {TABLE} (
                id INTEGER PRIMARY KEY AUTOINCREMENT, detected_at TEXT,
                entry_price REAL, reserve_usd REAL, last_price REAL, last_reserve_usd REAL,
                last_checked_at TEXT, exit_reason TEXT, final_multiplier REAL,
                realistic_final_multiplier REAL, dex_id TEXT, exit_price_source TEXT)"""
        )
        await c.commit()
    return path


async def _insert(path, **kw):
    base = dict(detected_at=RECENT, entry_price=1.0, reserve_usd=5000.0,
                last_price=0.5, last_reserve_usd=2500.0, last_checked_at=RECENT,
                exit_reason="trailing_stop", final_multiplier=0.5, realistic_final_multiplier=None,
                dex_id="pumpfun", exit_price_source="pumpfun")
    base.update(kw)
    cols = ", ".join(base)
    async with aiosqlite.connect(path) as c:
        await c.execute(f"INSERT INTO {TABLE} ({cols}) VALUES ({', '.join('?' * len(base))})",
                        tuple(base.values()))
        await c.commit()


@pytest.mark.asyncio
async def test_the_tolerance_actually_catches_the_real_bug_it_exists_for():
    """Guards the calibration itself: an ABSOLUTE tolerance was tried first
    and could not catch the real case (gap of only 0.251). Relative terms make
    the same case a 54% divergence."""
    price_ratio = 3.40e-06 / 1.57e-05
    reserve_ratio = 2604.0 / 5565.0
    assert abs(price_ratio - reserve_ratio) / reserve_ratio > integrity.PRICE_RESERVE_TOLERANCE


@pytest.mark.asyncio
async def test_consistent_price_and_reserve_produce_no_finding(db):
    """Price halving while reserve halves is exactly what a constant-product
    curve does -- this must NOT be flagged."""
    await _insert(db)
    assert await integrity.check_pocket("late_bonding", since="2026-08-20T00:00:00+00:00", db_path=db) == []


@pytest.mark.asyncio
async def test_price_and_reserve_moving_apart_is_flagged(db):
    """The exact signature of the real 20/08 bug: reserve fell 53% while the
    reported price fell 79%, because entry and exit came from different
    sources. A constant-product curve cannot produce that."""
    await _insert(db, entry_price=1.57e-05, reserve_usd=5565.0,
                  last_price=3.40e-06, last_reserve_usd=2604.0)

    findings = await integrity.check_pocket("late_bonding", since="2026-08-20T00:00:00+00:00", db_path=db)

    assert [f.check for f in findings] == ["price_reserve_divergence"]
    assert "two different price sources" in findings[0].detail


@pytest.mark.asyncio
async def test_an_implausible_multiplier_is_flagged_as_a_corrupted_price(db):
    await _insert(db, final_multiplier=integrity.IMPLAUSIBLE_MULTIPLIER + 1)
    findings = await integrity.check_pocket("late_bonding", since="2026-08-20T00:00:00+00:00", db_path=db)
    assert "implausible_multiplier" in [f.check for f in findings]


@pytest.mark.asyncio
async def test_open_positions_the_exit_loop_never_reaches_are_flagged(db):
    """The failure that made liquidity_collapse catches land 32-116s late
    while the nominal cadence was 10s."""
    await _insert(db, exit_reason=None, last_checked_at="2026-08-19T00:00:00+00:00",
                  final_multiplier=None)
    findings = await integrity.check_pocket("late_bonding", since="2026-08-20T00:00:00+00:00", db_path=db)
    assert "stale_open_positions" in [f.check for f in findings]


@pytest.mark.asyncio
async def test_a_row_without_a_usable_entry_price_is_flagged(db):
    """Its PnL is meaningless and silently poisons every average. Only OPEN
    rows are flagged: a closed one is already out of the queue."""
    await _insert(db, entry_price=0.0, exit_reason=None, final_multiplier=None)
    findings = await integrity.check_pocket("late_bonding", since="2026-08-20T00:00:00+00:00", db_path=db)
    assert "unusable_entry_price" in [f.check for f in findings]


@pytest.mark.asyncio
async def test_the_dedup_key_is_stable_so_one_problem_opens_one_issue(db):
    """A watchdog re-detecting the same ongoing anomaly must never spam."""
    await _insert(db, entry_price=1.57e-05, reserve_usd=5565.0,
                  last_price=3.40e-06, last_reserve_usd=2604.0)
    first = await integrity.check_pocket("late_bonding", since="2026-08-20T00:00:00+00:00", db_path=db)
    second = await integrity.check_pocket("late_bonding", since="2026-08-20T00:00:00+00:00", db_path=db)
    assert first[0].dedup_key == second[0].dedup_key


@pytest.mark.asyncio
async def test_a_pocket_whose_table_does_not_exist_is_not_a_finding(tmp_path):
    """A pocket that never ran is not an anomaly."""
    assert await integrity.check_pocket("ws_exit", db_path=str(tmp_path / "empty.db")) == []


@pytest.mark.asyncio
async def test_run_all_reports_clean_without_opening_issues(db):
    await _insert(db)
    out = await integrity.run_all(db_path=db, open_issues=False)
    assert out["clean"] is True and out["findings"] == []


@pytest.mark.asyncio
async def test_an_unknown_pocket_is_rejected_without_echoing_the_input(db):
    with pytest.raises(ValueError) as exc:
        await integrity.check_pocket("../../etc/passwd", db_path=db)
    assert "../../etc/passwd" not in str(exc.value)


@pytest.mark.asyncio
async def test_migrated_amm_pools_are_not_checked_against_the_curve_invariant(db):
    """price == quote/token holds on a BONDING CURVE, not on a migrated AMM
    pool where price depends on both sides independently of the USD reserve.
    First live run flagged 51/107 and 19/48 on the older pockets purely because
    they also trade migrated tokens -- a checker that cries wolf gets ignored."""
    # A divergence that WOULD trip the invariant, but on a migrated pool.
    await _insert(db, entry_price=1.57e-05, reserve_usd=5565.0,
                  last_price=3.40e-06, last_reserve_usd=2604.0)
    async with aiosqlite.connect(db) as c:
        await c.execute(f"UPDATE {TABLE} SET dex_id='raydium', exit_price_source='raydium'")
        await c.commit()

    findings = await integrity.check_pocket("late_bonding", since="2026-08-20T00:00:00+00:00", db_path=db)

    assert "price_reserve_divergence" not in [f.check for f in findings]


@pytest.mark.asyncio
async def test_a_stuck_zero_price_row_is_found_however_old_it_is(db):
    """Real case, 20/08: two rows with entry_price=0 sat OPEN for 8 hours
    holding the head of the exit queue (never-checked rows are served first
    since that day's ordering fix). A windowed check could not see them --
    a row like this is old BY DEFINITION, because being stuck is the symptom."""
    await _insert(db, entry_price=0.0, exit_reason=None,
                  detected_at="2026-08-01T00:00:00+00:00", final_multiplier=None)

    findings = await integrity.check_pocket("late_bonding", since="2026-08-20T23:00:00+00:00", db_path=db)

    assert "unusable_entry_price" in [f.check for f in findings]
