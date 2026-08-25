"""Forward-test observer for the ">=6000$ + top_holder<92%" segment (20/08).
Isolated tmp db, never a real network call or a real DB read -- same pattern
as every other shadow test file in this dome."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import aiosqlite
import pytest

from aria_core import fresh_launch_segment_forward_test as fwd

TABLE = fwd.POCKET_TABLES["fast_discovery"]
SINCE = "2026-08-20T20:00:00+00:00"
_SENTINEL_USE_MULTIPLIER = object()


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "shadow.db")
    async with aiosqlite.connect(path) as db:
        await db.execute(
            f"""
            CREATE TABLE {TABLE} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                detected_at TEXT, reserve_usd REAL, rugcheck_top_holder_pct REAL,
                exit_reason TEXT, final_multiplier REAL, realistic_final_multiplier REAL,
                realistic_entry_price REAL, realistic_realized_proceeds REAL
            )
            """
        )
        await db.commit()
    return path


async def _insert(
    path, *, reserve_usd=8000.0, top_holder=50.0, multiplier=1.2,
    detected_at=None, exit_reason="trailing_stop", realistic=_SENTINEL_USE_MULTIPLIER,
    # 25/08 -- fillable at entry by default (matches every other shadow
    # module's own test convention), so pre-existing tests that never asked
    # about realistic_entry_price keep being counted the way they were before
    # this column existed. Pass None explicitly to test the never-fillable
    # (entry itself unreachable) exclusion path instead.
    realistic_entry_price=1.0, realistic_realized_proceeds=None,
):
    if detected_at is None:
        detected_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    if realistic is _SENTINEL_USE_MULTIPLIER:
        # Default: no distinct realistic reading -- mirrors the nominal
        # multiplier, same doctrine as the other shadow test files'
        # realistic_entry_price sentinel. A test exercising the
        # nominal-vs-realistic distinction (or the stranded/never-fillable
        # paths) passes `realistic=` explicitly.
        realistic = multiplier
    async with aiosqlite.connect(path) as db:
        await db.execute(
            f"""INSERT INTO {TABLE}
                (detected_at, reserve_usd, rugcheck_top_holder_pct, exit_reason,
                 final_multiplier, realistic_final_multiplier, realistic_entry_price,
                 realistic_realized_proceeds)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (detected_at, reserve_usd, top_holder, exit_reason, multiplier, realistic,
             realistic_entry_price, realistic_realized_proceeds),
        )
        await db.commit()


@pytest.mark.asyncio
async def test_segment_and_control_are_split_on_the_two_criteria(db_path):
    await _insert(db_path, reserve_usd=8000.0, top_holder=50.0, multiplier=1.5)   # segment
    await _insert(db_path, reserve_usd=4000.0, top_holder=50.0, multiplier=0.5)   # control: reserve
    await _insert(db_path, reserve_usd=8000.0, top_holder=95.0, multiplier=0.5)   # control: holder

    report = await fwd.build_report(since=SINCE, db_path=db_path)

    assert report.segment.n == 1
    assert report.segment.avg_pnl_pct == pytest.approx(50.0)
    assert report.control.n == 2


@pytest.mark.asyncio
async def test_in_sample_closures_are_excluded_from_the_verdict(db_path):
    """The whole methodological point: the 146 closures the hypothesis was
    FOUND in must never be counted as confirming it."""
    await _insert(db_path, detected_at="2026-08-19T10:00:00+00:00", multiplier=3.0)
    await _insert(db_path, detected_at="2026-08-20T19:59:00+00:00", multiplier=3.0)

    report = await fwd.build_report(since=SINCE, db_path=db_path)

    assert report.segment.n == 0
    assert report.control.n == 0


@pytest.mark.asyncio
async def test_an_unenriched_row_counts_as_outside_the_segment(db_path):
    """A NULL top_holder is not evidence the token was clean -- crediting
    unknowns to the segment would let it prove itself."""
    await _insert(db_path, reserve_usd=8000.0, top_holder=None, multiplier=2.0)

    report = await fwd.build_report(since=SINCE, db_path=db_path)

    assert report.segment.n == 0
    assert report.control.n == 1


@pytest.mark.asyncio
async def test_realistic_multiplier_wins_over_the_nominal_one(db_path):
    await _insert(db_path, multiplier=2.0, realistic=1.1)

    report = await fwd.build_report(since=SINCE, db_path=db_path)

    assert report.segment.avg_pnl_pct == pytest.approx(10.0)


@pytest.mark.asyncio
async def test_a_stranded_mid_hold_position_counts_as_a_real_loss_not_the_nominal(db_path):
    """25/08, real bug found live (twin of shadow_notify.py's 23/08 fix and
    solana_late_bonding_shadow.summary()'s 25/08 fix): a position genuinely
    bought (realistic_entry_price NOT NULL) but stranded mid-hold by a
    liquidity collapse (realistic_final_multiplier NULL) used to fall back to
    the nominal final_multiplier via COALESCE -- crediting a candidate filter
    with an edge that was really just diluted rug exposure scored at an
    optimistic price. Must score the real salvaged-vs-entry loss instead."""
    await _insert(db_path, multiplier=5.0, realistic=None, realistic_entry_price=1.0)

    report = await fwd.build_report(since=SINCE, db_path=db_path)

    assert report.segment.n == 1
    assert report.segment.avg_pnl_pct == pytest.approx(-100.0)


@pytest.mark.asyncio
async def test_a_position_never_fillable_at_entry_stays_excluded(db_path):
    """Twin of the test above -- an entry that was never genuinely fillable
    (realistic_entry_price NULL, too thin from the start) must stay excluded:
    this trade never really happened, unlike a stranded mid-hold position."""
    await _insert(db_path, multiplier=0.1, realistic=None, realistic_entry_price=None)

    report = await fwd.build_report(since=SINCE, db_path=db_path)

    assert report.segment.n == 0


@pytest.mark.asyncio
async def test_open_positions_are_never_counted(db_path):
    await _insert(db_path, exit_reason=None, multiplier=5.0)

    report = await fwd.build_report(since=SINCE, db_path=db_path)

    assert report.segment.n == 0


@pytest.mark.asyncio
async def test_verdict_stays_prudent_under_thirty_closures(db_path):
    for _ in range(5):
        await _insert(db_path, multiplier=2.0)

    report = await fwd.build_report(since=SINCE, db_path=db_path)

    assert "insuffisant" in report.verdict
    assert report.segment.n == 5


@pytest.mark.asyncio
async def test_verdict_compares_against_the_control_not_zero(db_path):
    """A segment that only looks good because the whole market lifted is not
    an edge -- the control group is what makes the comparison honest."""
    for _ in range(30):
        await _insert(db_path, reserve_usd=8000.0, top_holder=50.0, multiplier=1.05)
    for _ in range(30):
        await _insert(db_path, reserve_usd=4000.0, top_holder=50.0, multiplier=1.40)

    report = await fwd.build_report(since=SINCE, db_path=db_path)

    assert report.segment.n == 30
    assert "non confirme" in report.verdict


@pytest.mark.asyncio
async def test_report_never_crashes_on_a_pocket_that_never_ran(tmp_path):
    report = await fwd.build_report(since=SINCE, db_path=str(tmp_path / "empty.db"))

    assert report.segment.n == 0
    assert "insuffisant" in report.verdict


@pytest.mark.asyncio
async def test_unknown_pocket_is_rejected_explicitly(db_path):
    with pytest.raises(ValueError):
        await fwd.build_report("nope", db_path=db_path)


@pytest.mark.asyncio
async def test_ws_exit_is_scanned_so_its_5000_ceiling_stays_verified(db_path):
    """WS-EXIT structurally cannot populate the segment (MAX_LIQUIDITY_USD_
    ENTRY=5000). Scanned anyway so the assumption is continuously checked
    rather than assumed -- rows appearing here mean an entry band changed."""
    assert "ws_exit" in fwd.POCKET_TABLES
    report = await fwd.build_report("ws_exit", since=SINCE, db_path=db_path)
    assert report.segment.n == 0


@pytest.mark.asyncio
async def test_format_report_is_operator_safe_plain_text(db_path):
    await _insert(db_path, multiplier=1.5)

    text = fwd.format_report(await fwd.build_report(since=SINCE, db_path=db_path))

    assert "Forward-test segment" in text
    assert "—" not in text  # no em-dash on an operator-facing surface
    assert "10.57" in text  # the in-sample baseline stays visible as the target
