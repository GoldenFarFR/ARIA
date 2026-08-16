"""Persisted candle history (11/08) -- FIFO series per (chain, pool_address,
timeframe), fed by momentum_entry.py's passive hook + the future dedicated
watchlist cycle. Mirrors test_candle_staleness_shadow.py's structure (same
shadow/history append-only pattern, tmp DB fixture)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import aiosqlite
import pytest

from aria_core import candle_history
from aria_core.skills.ta_levels import Candle

POOL = "0x" + "p" * 40
CONTRACT = "0x" + "c" * 40


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(candle_history, "DB_PATH", str(tmp_path / "candle_history.db"))
    candle_history._ensured_db_paths.clear()
    yield
    candle_history._ensured_db_paths.clear()


def _candles(n: int, *, interval: int, start_ts: int = 1_700_000_000, price: float = 1.0) -> list[Candle]:
    return [
        Candle(ts=start_ts + i * interval, open=price, high=price, low=price, close=price, volume=1.0)
        for i in range(n)
    ]


# -- infer_timeframe (pure function, no DB) ---------------------------------

def test_infer_timeframe_matches_each_nominal_interval():
    assert candle_history.infer_timeframe(86400) == "1D"
    assert candle_history.infer_timeframe(14400) == "4H"
    assert candle_history.infer_timeframe(3600) == "1H"
    assert candle_history.infer_timeframe(1800) == "30M"
    assert candle_history.infer_timeframe(900) == "15M"
    assert candle_history.infer_timeframe(300) == "5M"


def test_infer_timeframe_tolerates_normal_jitter():
    # within the 25% tolerance band around 3600s (1H)
    assert candle_history.infer_timeframe(3600 * 1.1) == "1H"
    assert candle_history.infer_timeframe(3600 * 0.9) == "1H"


def test_infer_timeframe_none_when_no_match():
    assert candle_history.infer_timeframe(120) is None  # 2 minutes, no known granularity
    assert candle_history.infer_timeframe(None) is None
    assert candle_history.infer_timeframe(0) is None
    assert candle_history.infer_timeframe(-5) is None


# -- record_candles / get_history --------------------------------------------

@pytest.mark.asyncio
async def test_record_and_read_back_1h_series():
    candles = _candles(5, interval=3600)
    await candle_history.record_candles(
        "base", POOL, mode="standard", candles=candles,
        median_interval_seconds=3600, contract=CONTRACT,
    )
    rows = await candle_history.get_history("base", POOL, "1H")
    assert len(rows) == 5
    assert [r["ts"] for r in rows] == [c.ts for c in candles]  # oldest first
    assert rows[0]["contract"] == CONTRACT
    assert rows[0]["mode"] == "standard"


@pytest.mark.asyncio
async def test_record_is_idempotent_no_duplicate_rows():
    candles = _candles(5, interval=3600)
    await candle_history.record_candles(
        "base", POOL, mode="standard", candles=candles, median_interval_seconds=3600,
    )
    await candle_history.record_candles(
        "base", POOL, mode="standard", candles=candles, median_interval_seconds=3600,
    )
    rows = await candle_history.get_history("base", POOL, "1H")
    assert len(rows) == 5  # re-inserting the same candles changes nothing


@pytest.mark.asyncio
async def test_5m_excluded_from_persistence():
    candles = _candles(5, interval=300)
    await candle_history.record_candles(
        "base", POOL, mode="scalping_5m", candles=candles, median_interval_seconds=300,
    )
    rows = await candle_history.get_history("base", POOL, "5M")
    assert rows == []  # v9's default granularity is never persisted (operator decision)


@pytest.mark.asyncio
async def test_unrecognized_interval_never_persisted():
    candles = _candles(5, interval=120)
    await candle_history.record_candles(
        "base", POOL, mode="standard", candles=candles, median_interval_seconds=120,
    )
    # no known timeframe matches 120s -- nothing should have been written anywhere
    for tf in candle_history.FIFO_CAP_BY_TIMEFRAME:
        assert await candle_history.get_history("base", POOL, tf) == []


@pytest.mark.asyncio
async def test_different_timeframes_stay_in_independent_series():
    hourly = _candles(3, interval=3600, start_ts=1_700_000_000)
    daily = _candles(3, interval=86400, start_ts=1_700_000_000)
    await candle_history.record_candles(
        "base", POOL, mode="standard", candles=hourly, median_interval_seconds=3600,
    )
    await candle_history.record_candles(
        "base", POOL, mode="standard", candles=daily, median_interval_seconds=86400,
    )
    assert len(await candle_history.get_history("base", POOL, "1H")) == 3
    assert len(await candle_history.get_history("base", POOL, "1D")) == 3


@pytest.mark.asyncio
async def test_fifo_purge_respects_per_timeframe_cap(monkeypatch):
    monkeypatch.setitem(candle_history.FIFO_CAP_BY_TIMEFRAME, "1H", 10)
    candles = _candles(25, interval=3600)
    await candle_history.record_candles(
        "base", POOL, mode="standard", candles=candles, median_interval_seconds=3600,
    )
    rows = await candle_history.get_history("base", POOL, "1H")
    assert len(rows) == 10
    # the 10 most RECENT candles survive, oldest evicted
    assert [r["ts"] for r in rows] == [c.ts for c in candles[-10:]]


@pytest.mark.asyncio
async def test_fifo_purge_incremental_across_calls(monkeypatch):
    monkeypatch.setitem(candle_history.FIFO_CAP_BY_TIMEFRAME, "1H", 5)
    first = _candles(5, interval=3600, start_ts=1_700_000_000)
    second = _candles(5, interval=3600, start_ts=1_700_000_000 + 5 * 3600)
    await candle_history.record_candles(
        "base", POOL, mode="standard", candles=first, median_interval_seconds=3600,
    )
    await candle_history.record_candles(
        "base", POOL, mode="standard", candles=second, median_interval_seconds=3600,
    )
    rows = await candle_history.get_history("base", POOL, "1H")
    assert len(rows) == 5
    assert [r["ts"] for r in rows] == [c.ts for c in second]  # only the newest batch survives


@pytest.mark.asyncio
async def test_no_cap_configured_for_timeframe_never_purges():
    # 5M has no entry in FIFO_CAP_BY_TIMEFRAME at all -- but it's also
    # excluded from persistence entirely, so this documents the _purge_fifo
    # cap-lookup miss path directly rather than relying on that exclusion.
    await candle_history._purge_fifo("base", POOL, "5M")  # must not raise


@pytest.mark.asyncio
async def test_empty_candles_or_missing_pool_is_a_noop():
    await candle_history.record_candles(
        "base", "", mode="standard", candles=_candles(3, interval=3600), median_interval_seconds=3600,
    )
    await candle_history.record_candles(
        "base", POOL, mode="standard", candles=[], median_interval_seconds=3600,
    )
    assert await candle_history.get_history("base", POOL, "1H") == []


@pytest.mark.asyncio
async def test_record_failure_never_raises_into_caller(monkeypatch):
    async def _broken_connect(*_a, **_kw):
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(candle_history.aiosqlite, "connect", _broken_connect)
    # must not raise -- best-effort contract, same as every other shadow module
    await candle_history.record_candles(
        "base", POOL, mode="standard", candles=_candles(3, interval=3600), median_interval_seconds=3600,
    )


@pytest.mark.asyncio
async def test_get_history_limit_keeps_most_recent_oldest_first():
    candles = _candles(20, interval=3600)
    await candle_history.record_candles(
        "base", POOL, mode="standard", candles=candles, median_interval_seconds=3600,
    )
    rows = await candle_history.get_history("base", POOL, "1H", limit=5)
    assert len(rows) == 5
    assert [r["ts"] for r in rows] == [c.ts for c in candles[-5:]]


# -- due_for_refresh / mark_watchlist_refreshed (11/08, watchlist collector cursor) --

CONTRACT_A = ("0x" + "a" * 40, "base")
CONTRACT_B = ("0x" + "b" * 40, "base")
CONTRACT_C = ("0x" + "c" * 40, "base")


@pytest.mark.asyncio
async def test_due_for_refresh_never_fetched_comes_first():
    due = await candle_history.due_for_refresh([CONTRACT_A, CONTRACT_B], limit=2)
    assert set(due) == {CONTRACT_A, CONTRACT_B}  # both unseen, order among ties unspecified


@pytest.mark.asyncio
async def test_due_for_refresh_prioritizes_never_fetched_over_stale():
    await candle_history.mark_watchlist_refreshed(*CONTRACT_A)
    due = await candle_history.due_for_refresh([CONTRACT_A, CONTRACT_B], limit=1)
    assert due == [CONTRACT_B]  # B never fetched, beats A's stale-but-known cursor


@pytest.mark.asyncio
async def test_due_for_refresh_oldest_fetched_first_among_known():
    await candle_history.mark_watchlist_refreshed(*CONTRACT_A)
    await candle_history.mark_watchlist_refreshed(*CONTRACT_B)
    # re-touch A so it becomes the MORE recently fetched of the two
    await candle_history.mark_watchlist_refreshed(*CONTRACT_A)
    due = await candle_history.due_for_refresh([CONTRACT_A, CONTRACT_B], limit=2)
    assert due == [CONTRACT_B, CONTRACT_A]  # B's cursor is older -- refreshed first


@pytest.mark.asyncio
async def test_due_for_refresh_respects_limit():
    due = await candle_history.due_for_refresh([CONTRACT_A, CONTRACT_B, CONTRACT_C], limit=2)
    assert len(due) == 2


@pytest.mark.asyncio
async def test_due_for_refresh_empty_candidates_or_zero_limit_is_noop():
    assert await candle_history.due_for_refresh([], limit=5) == []
    assert await candle_history.due_for_refresh([CONTRACT_A], limit=0) == []


@pytest.mark.asyncio
async def test_mark_watchlist_refreshed_is_idempotent_upsert():
    await candle_history.mark_watchlist_refreshed(*CONTRACT_A)
    await candle_history.mark_watchlist_refreshed(*CONTRACT_A)  # must not raise / duplicate
    due = await candle_history.due_for_refresh([CONTRACT_A], limit=1)
    assert due == [CONTRACT_A]


# -- due_for_refresh priority (#97, 13/08: open swing/vc positions cut the queue) --


@pytest.mark.asyncio
async def test_due_for_refresh_priority_beats_never_fetched_non_favorite():
    # C is never fetched (would normally win the tie against A/B), but only
    # A is a favorite -- A must still come first.
    due = await candle_history.due_for_refresh(
        [CONTRACT_A, CONTRACT_C], limit=1, priority={CONTRACT_A},
    )
    assert due == [CONTRACT_A]


@pytest.mark.asyncio
async def test_due_for_refresh_priority_still_respects_round_robin_within_group():
    # Both A and B are favorites; B's cursor is older -- must still come
    # first WITHIN the favorite group, exactly like the unweighted case.
    await candle_history.mark_watchlist_refreshed(*CONTRACT_A)
    await candle_history.mark_watchlist_refreshed(*CONTRACT_B)
    await candle_history.mark_watchlist_refreshed(*CONTRACT_A)  # A now more recent than B
    due = await candle_history.due_for_refresh(
        [CONTRACT_A, CONTRACT_B], limit=2, priority={CONTRACT_A, CONTRACT_B},
    )
    assert due == [CONTRACT_B, CONTRACT_A]


@pytest.mark.asyncio
async def test_due_for_refresh_priority_none_or_empty_is_unweighted_default():
    due_none = await candle_history.due_for_refresh([CONTRACT_A, CONTRACT_B], limit=2)
    due_empty = await candle_history.due_for_refresh(
        [CONTRACT_A, CONTRACT_B], limit=2, priority=set(),
    )
    assert set(due_none) == set(due_empty) == {CONTRACT_A, CONTRACT_B}


@pytest.mark.asyncio
async def test_due_for_refresh_priority_never_drops_non_favorites_from_batch():
    # A favorite plus 2 non-favorites, limit covers all 3 -- nobody is
    # dropped, only reordered.
    due = await candle_history.due_for_refresh(
        [CONTRACT_A, CONTRACT_B, CONTRACT_C], limit=3, priority={CONTRACT_C},
    )
    assert set(due) == {CONTRACT_A, CONTRACT_B, CONTRACT_C}
    assert due[0] == CONTRACT_C


# -- due_for_refresh tiering (#301, 16/08: fast/slow split so an old,
#    never-traded token isn't starved forever behind a growing fast tier) --


async def _set_last_fetched_at(contract: str, chain: str, iso_ts: str) -> None:
    await candle_history._ensure_table()
    async with aiosqlite.connect(candle_history._db_path()) as db:
        await db.execute(
            "INSERT INTO candle_watchlist_cursor (contract, chain, last_fetched_at) VALUES (?, ?, ?) "
            "ON CONFLICT (contract, chain) DO UPDATE SET last_fetched_at = excluded.last_fetched_at",
            (contract, chain, iso_ts),
        )
        await db.commit()


def _days_ago(n: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=n)).isoformat()


@pytest.mark.asyncio
async def test_due_for_refresh_stale_candidate_loses_fast_tier_slots_at_larger_limit():
    # A was fetched 30 days ago (stale, default cutoff is 7 days); B and C
    # are never-fetched (fast tier). At limit=2 (slow quota = 1 of 2), the
    # fast tier still claims the majority of slots -- B/C both beat A for
    # the single non-guaranteed slot, A only gets in via its guaranteed slot.
    await _set_last_fetched_at(*CONTRACT_A, _days_ago(30))
    due = await candle_history.due_for_refresh([CONTRACT_A, CONTRACT_B, CONTRACT_C], limit=2)
    assert CONTRACT_A in due
    assert len(due) == 2


@pytest.mark.asyncio
async def test_due_for_refresh_stale_always_gets_at_least_one_guaranteed_slot():
    # Even at the degenerate limit=1, a stale candidate still wins its
    # guaranteed slow-tier slot over a never-fetched fast candidate -- the
    # guarantee is absolute, not just "usually eventually" (real batches are
    # sized 20, where this costs the fast tier one slot out of twenty).
    await _set_last_fetched_at(*CONTRACT_A, _days_ago(30))
    due = await candle_history.due_for_refresh([CONTRACT_A, CONTRACT_B], limit=1)
    assert due == [CONTRACT_A]


@pytest.mark.asyncio
async def test_due_for_refresh_stale_tier_still_gets_a_guaranteed_slot():
    # B (never-fetched) and C (never-fetched) fill the fast tier; A is
    # stale. At limit=3 (20% of 3 rounds up to 1), A must still appear --
    # never permanently starved just because the fast tier keeps growing.
    await _set_last_fetched_at(*CONTRACT_A, _days_ago(30))
    due = await candle_history.due_for_refresh([CONTRACT_A, CONTRACT_B, CONTRACT_C], limit=3)
    assert CONTRACT_A in due
    assert set(due) == {CONTRACT_A, CONTRACT_B, CONTRACT_C}


@pytest.mark.asyncio
async def test_due_for_refresh_favorite_bypasses_staleness():
    # A is stale (30 days) but a live-position favorite -- must stay in the
    # fast tier exactly like the existing priority behavior, unaffected by
    # tiering.
    await _set_last_fetched_at(*CONTRACT_A, _days_ago(30))
    due = await candle_history.due_for_refresh(
        [CONTRACT_A, CONTRACT_B], limit=1, priority={CONTRACT_A},
    )
    assert due == [CONTRACT_A]


@pytest.mark.asyncio
async def test_due_for_refresh_no_stale_candidates_is_unaffected():
    # Nobody is stale -- tiering must be a no-op, identical to the flat
    # round-robin behavior tested above.
    due = await candle_history.due_for_refresh([CONTRACT_A, CONTRACT_B, CONTRACT_C], limit=2)
    assert len(due) == 2


@pytest.mark.asyncio
async def test_due_for_refresh_stale_backfills_when_fast_tier_runs_dry():
    # Only A (stale) and B (stale) exist -- no fast candidates at all. The
    # slow tier must backfill the whole limit rather than leaving it
    # under-used.
    await _set_last_fetched_at(*CONTRACT_A, _days_ago(30))
    await _set_last_fetched_at(*CONTRACT_B, _days_ago(30))
    due = await candle_history.due_for_refresh([CONTRACT_A, CONTRACT_B], limit=2)
    assert set(due) == {CONTRACT_A, CONTRACT_B}


@pytest.mark.asyncio
async def test_due_for_refresh_custom_stale_after_days_respected():
    # A was fetched 3 days ago -- "fresh" under the default 7-day cutoff
    # (stays in the fast tier, loses the limit=1 tie against never-fetched
    # B/C), but demoted to the slow tier under a tightened 1-day cutoff --
    # where its guaranteed slow-tier slot makes it win DESPITE B/C never
    # having been fetched at all, proving the cutoff is actually load-bearing
    # rather than cosmetic.
    await _set_last_fetched_at(*CONTRACT_A, _days_ago(3))
    due_default = await candle_history.due_for_refresh([CONTRACT_A, CONTRACT_B, CONTRACT_C], limit=1)
    assert due_default != [CONTRACT_A]

    due_tight = await candle_history.due_for_refresh(
        [CONTRACT_A, CONTRACT_B, CONTRACT_C], limit=1, stale_after_days=1,
    )
    assert due_tight == [CONTRACT_A]
