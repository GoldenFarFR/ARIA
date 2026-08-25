"""Candle-freshness shadow observer (backlog #261, 10/08) -- append-only
log, never blocks. Mirrors test_wick_filter_shadow.py's structure (same
shadow pattern)."""
from __future__ import annotations

import pytest

from aria_core import candle_staleness_shadow

CONTRACT = "0x" + "c" * 40


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(candle_staleness_shadow, "DB_PATH", str(tmp_path / "shadow.db"))
    candle_staleness_shadow._ensured_db_paths.clear()
    yield
    candle_staleness_shadow._ensured_db_paths.clear()


@pytest.mark.asyncio
async def test_record_stale_age_would_flag():
    await candle_staleness_shadow.record_observation(
        CONTRACT, "base", mode="standard", source="fetch_candles",
        age_seconds=10_000.0, median_interval_seconds=60.0, symbol="TOK",
    )
    rows = await candle_staleness_shadow.list_recent()
    assert len(rows) == 1
    assert rows[0]["age_seconds"] == 10_000.0
    assert rows[0]["would_flag"] == 1
    assert rows[0]["mode"] == "standard"


@pytest.mark.asyncio
async def test_record_fresh_age_would_not_flag():
    await candle_staleness_shadow.record_observation(
        CONTRACT, "base", mode="scalping", source="fetch_candles",
        age_seconds=90.0, median_interval_seconds=60.0,
    )
    rows = await candle_staleness_shadow.list_recent()
    assert rows[0]["would_flag"] == 0


@pytest.mark.asyncio
async def test_record_exactly_at_multiplier_would_not_flag():
    # boundary: age == multiplier * median -- strictly-greater-than in the
    # implementation, so exactly-at-threshold stays unflagged.
    median = 60.0
    await candle_staleness_shadow.record_observation(
        CONTRACT, "base", mode="standard", source="fetch_candles",
        age_seconds=candle_staleness_shadow.STALENESS_SHADOW_MULTIPLIER * median,
        median_interval_seconds=median,
    )
    rows = await candle_staleness_shadow.list_recent()
    assert rows[0]["would_flag"] == 0


@pytest.mark.asyncio
async def test_record_unknown_median_never_fabricates_a_verdict():
    await candle_staleness_shadow.record_observation(
        CONTRACT, "base", mode="standard", source="fetch_candles",
        age_seconds=500.0, median_interval_seconds=None,
    )
    rows = await candle_staleness_shadow.list_recent()
    assert rows[0]["median_interval_seconds"] is None
    assert rows[0]["would_flag"] is None


@pytest.mark.asyncio
async def test_record_empty_contract_is_a_noop():
    await candle_staleness_shadow.record_observation(
        "", "base", mode="standard", source="fetch_candles",
        age_seconds=500.0, median_interval_seconds=60.0,
    )
    assert await candle_staleness_shadow.list_recent() == []


@pytest.mark.asyncio
async def test_record_never_raises_on_db_failure(monkeypatch):
    monkeypatch.setattr(candle_staleness_shadow, "DB_PATH", "/nonexistent/dir/shadow.db")
    candle_staleness_shadow._ensured_db_paths.clear()
    # must not raise into the caller's fetch path
    await candle_staleness_shadow.record_observation(
        CONTRACT, "base", mode="standard", source="fetch_candles",
        age_seconds=500.0, median_interval_seconds=60.0,
    )


@pytest.mark.asyncio
async def test_flagged_rate_computes_fraction_over_judged_rows():
    await candle_staleness_shadow.record_observation(
        CONTRACT, "base", mode="standard", source="fetch_candles",
        age_seconds=10_000.0, median_interval_seconds=60.0,
    )
    await candle_staleness_shadow.record_observation(
        CONTRACT, "base", mode="standard", source="fetch_candles",
        age_seconds=90.0, median_interval_seconds=60.0,
    )
    await candle_staleness_shadow.record_observation(
        CONTRACT, "base", mode="standard", source="fetch_candles",
        age_seconds=90.0, median_interval_seconds=60.0,
    )
    # unjudged row (unknown median) must never dilute the rate's denominator
    await candle_staleness_shadow.record_observation(
        CONTRACT, "base", mode="standard", source="fetch_candles",
        age_seconds=90.0, median_interval_seconds=None,
    )
    rate = await candle_staleness_shadow.flagged_rate()
    assert rate == pytest.approx(1 / 3)


@pytest.mark.asyncio
async def test_flagged_rate_none_when_nothing_judgeable():
    assert await candle_staleness_shadow.flagged_rate() is None
    await candle_staleness_shadow.record_observation(
        CONTRACT, "base", mode="standard", source="fetch_candles",
        age_seconds=90.0, median_interval_seconds=None,
    )
    assert await candle_staleness_shadow.flagged_rate() is None


# --- forward_validation_report (25/08, audit 001-audit-code-sans, T007) ----

async def _make_paper_position_table():
    import aiosqlite

    async with aiosqlite.connect(candle_staleness_shadow.DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE paper_position (
                contract TEXT, chain TEXT, opened_at TEXT,
                status TEXT, pnl_pct REAL
            )
            """
        )
        await db.commit()


async def _insert_position(contract, chain, opened_at, pnl_pct, *, status="closed"):
    import aiosqlite

    async with aiosqlite.connect(candle_staleness_shadow.DB_PATH) as db:
        await db.execute(
            "INSERT INTO paper_position (contract, chain, opened_at, status, pnl_pct) "
            "VALUES (?, ?, ?, ?, ?)",
            (contract, chain, opened_at, status, pnl_pct),
        )
        await db.commit()


@pytest.mark.asyncio
async def test_forward_validation_honest_without_paper_position_table():
    """No paper_position table at all (a fresh/isolated DB) must degrade to
    an honest 'not enough data' report, never a crash."""
    await candle_staleness_shadow.record_observation(
        CONTRACT, "base", mode="standard", source="fetch_candles",
        age_seconds=10_000.0, median_interval_seconds=60.0,
    )
    report = await candle_staleness_shadow.forward_validation_report()
    assert report["enough_data"] is False
    assert "not enough" in report["verdict"]


@pytest.mark.asyncio
async def test_forward_validation_links_position_opened_within_window():
    await _make_paper_position_table()
    await candle_staleness_shadow.record_observation(
        CONTRACT, "base", mode="standard", source="fetch_candles",
        age_seconds=10_000.0, median_interval_seconds=60.0,
    )
    await _insert_position(CONTRACT, "base", "2026-08-25T12:10:00+00:00", -5.0)
    # Backdate the observation so the position falls inside the forward window.
    import aiosqlite

    async with aiosqlite.connect(candle_staleness_shadow.DB_PATH) as db:
        await db.execute(
            "UPDATE candle_staleness_shadow_log SET recorded_at = ?",
            ("2026-08-25T12:00:00+00:00",),
        )
        await db.commit()

    report = await candle_staleness_shadow.forward_validation_report()
    assert report["n_flagged_linked"] == 1


@pytest.mark.asyncio
async def test_forward_validation_ignores_position_outside_window():
    await _make_paper_position_table()
    await candle_staleness_shadow.record_observation(
        CONTRACT, "base", mode="standard", source="fetch_candles",
        age_seconds=10_000.0, median_interval_seconds=60.0,
    )
    # 2h later -- well outside FORWARD_LINK_WINDOW_MINUTES, must not link.
    await _insert_position(CONTRACT, "base", "2026-08-25T14:00:00+00:00", -5.0)
    import aiosqlite

    async with aiosqlite.connect(candle_staleness_shadow.DB_PATH) as db:
        await db.execute(
            "UPDATE candle_staleness_shadow_log SET recorded_at = ?",
            ("2026-08-25T12:00:00+00:00",),
        )
        await db.commit()

    report = await candle_staleness_shadow.forward_validation_report()
    assert report["n_flagged_linked"] == 0


@pytest.mark.asyncio
async def test_forward_validation_detects_flagged_worse_outcome():
    """Flagged observations consistently link to worse real outcomes than
    clean ones -- with enough samples on both sides, the verdict must say
    so, using the outlier-resistant figures."""
    await _make_paper_position_table()
    n = candle_staleness_shadow._MIN_SAMPLES_PER_BUCKET
    import aiosqlite

    for i in range(n):
        contract = f"0x{i:040x}"
        await candle_staleness_shadow.record_observation(
            contract, "base", mode="standard", source="fetch_candles",
            age_seconds=10_000.0, median_interval_seconds=60.0,
        )
        await _insert_position(contract, "base", "2026-08-25T12:05:00+00:00", -10.0)

    for i in range(100, 100 + n):
        contract = f"0x{i:040x}"
        await candle_staleness_shadow.record_observation(
            contract, "base", mode="standard", source="fetch_candles",
            age_seconds=30.0, median_interval_seconds=60.0,
        )
        await _insert_position(contract, "base", "2026-08-25T12:05:00+00:00", 5.0)

    async with aiosqlite.connect(candle_staleness_shadow.DB_PATH) as db:
        await db.execute(
            "UPDATE candle_staleness_shadow_log SET recorded_at = ?",
            ("2026-08-25T12:00:00+00:00",),
        )
        await db.commit()

    report = await candle_staleness_shadow.forward_validation_report()
    assert report["enough_data"] is True
    assert report["n_flagged_linked"] == n
    assert report["n_clean_linked"] == n
    assert "worth graduating" in report["verdict"]


@pytest.mark.asyncio
async def test_forward_validation_verdict_resists_a_single_outlier():
    """A single extreme pnl_pct in the CLEAN bucket must not flip the
    verdict on its own -- same guardrail as signal_cascade_convergence's
    falsifiability report."""
    await _make_paper_position_table()
    n = candle_staleness_shadow._MIN_SAMPLES_PER_BUCKET
    import aiosqlite

    for i in range(n):
        contract = f"0x{i:040x}"
        await candle_staleness_shadow.record_observation(
            contract, "base", mode="standard", source="fetch_candles",
            age_seconds=10_000.0, median_interval_seconds=60.0,
        )
        await _insert_position(contract, "base", "2026-08-25T12:05:00+00:00", -8.0)

    for i in range(100, 100 + n):
        contract = f"0x{i:040x}"
        await candle_staleness_shadow.record_observation(
            contract, "base", mode="standard", source="fetch_candles",
            age_seconds=30.0, median_interval_seconds=60.0,
        )
        # All clean outcomes are modest losses EXCEPT one huge outlier win.
        pnl = 50_000.0 if i == 100 else -7.0
        await _insert_position(contract, "base", "2026-08-25T12:05:00+00:00", pnl)

    async with aiosqlite.connect(candle_staleness_shadow.DB_PATH) as db:
        await db.execute(
            "UPDATE candle_staleness_shadow_log SET recorded_at = ?",
            ("2026-08-25T12:00:00+00:00",),
        )
        await db.commit()

    report = await candle_staleness_shadow.forward_validation_report()
    assert report["enough_data"] is True
    # Raw average makes clean look far better purely from the outlier.
    assert report["avg_pnl_pct_clean"] > report["avg_pnl_pct_flagged"]
    # But flagged is still genuinely worse outlier-free -- the verdict must
    # reflect that, not the inflated raw figure.
    assert report["avg_pnl_pct_flagged_no_top2"] < report["avg_pnl_pct_clean_no_top2"]
    assert "worth graduating" in report["verdict"]
