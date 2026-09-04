"""early_life_observation -- post-qualification trajectory tracking.

04/09, operator go (Chantier A, strictly observation-only). Today's
pipeline only observes a candidate WHILE it's pending qualification
(onchain_pool_discovery._candidates), then drops it the instant it
qualifies -- confirmed live on $LEGS (03/09): qualified on its very first
check, exactly ONE snapshot ever recorded, zero trajectory data. This
module continues recording RAW activity snapshots for a bounded window
AFTER qualification, so a candidate's early-life trajectory (t=0, t=5s,
t=10s...) becomes reconstructible.

Never derives a feature -- purely schedules repeated calls to the existing,
already-tested onchain_activity_observation.record_observation (append-
only, immutable). No trade decision anywhere in this module.

04/09, second pass (operator go, twice-revised same day): adds a
"Candidate" state -- ON-CHAIN-established gate (real trajectory, not a
4-second photograph) + SECURITY retry/block state machine (GoPlus's real
indexing lag means a single check is not enough) + the ability to
reconstruct a TrendingPool-like snapshot from already-collected data (zero
extra network call). Operator's own correction, same day: ON-CHAIN+SECURITY
alone must NEVER trigger a Telegram send -- reaching "Candidate" is purely
internal bookkeeping, distinct from a future "Investable Alert" (full
multi-signal convergence, not designed/built here)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import aiosqlite
import pytest

from aria_core import early_life_observation
from aria_core import onchain_activity_observation
from aria_core.paths import configure_data_dir


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path):
    configure_data_dir(str(tmp_path))
    yield


def _t(seconds: int) -> datetime:
    return datetime(2026, 9, 4, 0, 0, 0, tzinfo=timezone.utc) + timedelta(seconds=seconds)


async def test_start_tracking_records_a_row():
    await early_life_observation.start_tracking(
        "robinhood", "0xpool", "0xtoken", qualified_at=_t(0),
    )
    active = await early_life_observation.list_active("robinhood", now=_t(1))
    assert len(active) == 1
    assert active[0]["pool_address"] == "0xpool"


async def test_start_tracking_never_duplicates_the_same_pool():
    await early_life_observation.start_tracking("robinhood", "0xpool", "0xtoken", qualified_at=_t(0))
    await early_life_observation.start_tracking("robinhood", "0xpool", "0xtoken", qualified_at=_t(1))
    active = await early_life_observation.list_active("robinhood", now=_t(2))
    assert len(active) == 1


async def test_active_candidate_within_window_is_listed():
    await early_life_observation.start_tracking("robinhood", "0xpool", "0xtoken", qualified_at=_t(0))
    active = await early_life_observation.list_active(
        "robinhood", now=_t(100), window_seconds=300.0,
    )
    assert len(active) == 1


async def test_expired_candidate_is_not_listed():
    await early_life_observation.start_tracking("robinhood", "0xpool", "0xtoken", qualified_at=_t(0))
    active = await early_life_observation.list_active(
        "robinhood", now=_t(301), window_seconds=300.0,
    )
    assert active == []


async def test_different_chains_never_cross_contaminate():
    await early_life_observation.start_tracking("robinhood", "0xpool", "0xtoken", qualified_at=_t(0))
    active = await early_life_observation.list_active("base", now=_t(1))
    assert active == []


async def test_advance_tracking_cycle_records_a_snapshot_for_each_active_candidate():
    await early_life_observation.start_tracking("robinhood", "0xpool", "0xtoken", qualified_at=_t(0))
    ws_feed = MagicMock()
    ws_feed.get_snapshot = MagicMock(return_value=MagicMock(
        available=True, family="v2", swap_count=3, cumulative_volume_quote=1.5,
        distinct_traders_count=2, stale_seconds=1.0, buy_count=2, sell_count=1,
        undetermined_count=0, buy_volume_quote=1.0, sell_volume_quote=0.5,
        undetermined_volume_quote=0.0, liquidity_added_quote=None, liquidity_removed_quote=None,
        liquidity_added_raw=None, liquidity_removed_raw=None, price_quote=1.0, price_usd=1.0,
        reserve_usd=20000.0, raw_liquidity=None, quote_reserve_raw=None, quote_is_weth=False,
    ))
    n = await early_life_observation.advance_tracking_cycle(
        "robinhood", ws_feed=ws_feed, now=_t(5),
    )
    assert n == 1
    async with aiosqlite.connect(early_life_observation._activity_db_path()) as db:
        cur = await db.execute(
            "SELECT COUNT(*) FROM onchain_activity_observation_log WHERE pool_address = '0xpool'"
        )
        (count,) = await cur.fetchone()
    assert count == 1


async def test_advance_tracking_cycle_skips_expired_candidates():
    await early_life_observation.start_tracking("robinhood", "0xpool", "0xtoken", qualified_at=_t(0))
    ws_feed = MagicMock()
    ws_feed.get_snapshot = MagicMock(return_value=MagicMock(available=False))
    n = await early_life_observation.advance_tracking_cycle(
        "robinhood", ws_feed=ws_feed, now=_t(301), window_seconds=300.0,
    )
    assert n == 0


async def test_advance_tracking_cycle_never_raises_on_snapshot_failure():
    await early_life_observation.start_tracking("robinhood", "0xpool", "0xtoken", qualified_at=_t(0))
    ws_feed = MagicMock()
    ws_feed.get_snapshot = MagicMock(side_effect=Exception("boom"))
    n = await early_life_observation.advance_tracking_cycle(
        "robinhood", ws_feed=ws_feed, now=_t(5),
    )
    assert n == 0


# ---------------------------------------------------------------------------
# 04/09, second pass: ON-CHAIN-established gate, SECURITY retry/block state
# machine, snapshot reconstruction, candidate-validated bookkeeping.
# ---------------------------------------------------------------------------

async def _record_available_observation(pool_address: str, *, at: datetime, **overrides) -> None:
    """Test helper -- writes one real ``available=True`` row via the actual
    production function, never a hand-crafted INSERT, so these tests exercise
    the same schema/columns the real pipeline writes. ``at`` is accepted for
    readability at call sites but not enforced as the real ``observed_at``
    (that column always uses wall-clock time) -- insertion ORDER is what
    ``build_snapshot_pool``/the observation count rely on, never the exact
    timestamp, so this is never a source of test flakiness."""
    kwargs = dict(
        chain="robinhood", pool_address=pool_address, token_address="0xtoken",
        available=True, family="v2", swap_count=1, cumulative_volume_quote=0.01,
        distinct_traders_count=1, last_swap_age_seconds=0.5,
        buy_count=1, sell_count=0, undetermined_count=0,
        buy_volume_quote=0.01, sell_volume_quote=0.0, undetermined_volume_quote=0.0,
        price_quote=1e-6, price_usd=0.003, reserve_usd=25000.0,
        eth_usd_rate_at_observation=3000.0,
    )
    kwargs.update(overrides)
    await onchain_activity_observation.record_observation(**kwargs)


async def test_has_minimum_onchain_trajectory_false_before_min_age():
    await early_life_observation.start_tracking("robinhood", "0xpool", "0xtoken", qualified_at=_t(0))
    await _record_available_observation("0xpool", at=_t(1))
    established = await early_life_observation.has_minimum_onchain_trajectory(
        "robinhood", "0xpool", now=_t(5), min_age_seconds=60.0,
    )
    assert established is False


async def test_has_minimum_onchain_trajectory_false_without_any_available_observation():
    await early_life_observation.start_tracking("robinhood", "0xpool", "0xtoken", qualified_at=_t(0))
    established = await early_life_observation.has_minimum_onchain_trajectory(
        "robinhood", "0xpool", now=_t(120), min_age_seconds=60.0,
    )
    assert established is False


async def test_has_minimum_onchain_trajectory_true_once_aged_and_observed():
    await early_life_observation.start_tracking("robinhood", "0xpool", "0xtoken", qualified_at=_t(0))
    await _record_available_observation("0xpool", at=_t(65))
    established = await early_life_observation.has_minimum_onchain_trajectory(
        "robinhood", "0xpool", now=_t(70), min_age_seconds=60.0,
    )
    assert established is True


async def test_has_minimum_onchain_trajectory_unknown_pool_is_false():
    established = await early_life_observation.has_minimum_onchain_trajectory(
        "robinhood", "0xghost", now=_t(1000), min_age_seconds=60.0,
    )
    assert established is False


def test_should_retry_security_check_true_when_never_checked():
    assert early_life_observation.should_retry_security_check(None, now=_t(0)) is True


def test_should_retry_security_check_false_within_interval():
    last = _t(0).isoformat()
    assert early_life_observation.should_retry_security_check(
        last, now=_t(5), min_interval_seconds=20.0,
    ) is False


def test_should_retry_security_check_true_past_interval():
    last = _t(0).isoformat()
    assert early_life_observation.should_retry_security_check(
        last, now=_t(21), min_interval_seconds=20.0,
    ) is True


async def test_update_security_status_persists_and_is_readable():
    await early_life_observation.start_tracking("robinhood", "0xpool", "0xtoken", qualified_at=_t(0))
    await early_life_observation.update_security_status("robinhood", "0xpool", "blocked", at=_t(10))
    active = await early_life_observation.list_active("robinhood", now=_t(11))
    assert active[0]["security_status"] == "blocked"
    assert active[0]["last_security_check_at"] == _t(10).isoformat()


async def test_touch_security_check_at_updates_timestamp_never_status():
    await early_life_observation.start_tracking("robinhood", "0xpool", "0xtoken", qualified_at=_t(0))
    await early_life_observation.touch_security_check_at("robinhood", "0xpool", at=_t(5))
    active = await early_life_observation.list_active("robinhood", now=_t(6))
    assert active[0]["last_security_check_at"] == _t(5).isoformat()
    assert active[0]["security_status"] is None


async def test_mark_candidate_validated_persists():
    await early_life_observation.start_tracking("robinhood", "0xpool", "0xtoken", qualified_at=_t(0))
    await early_life_observation.mark_candidate_validated("robinhood", "0xpool", at=_t(10))
    active = await early_life_observation.list_active("robinhood", now=_t(11))
    assert active[0]["candidate_validated_at"] == _t(10).isoformat()


async def test_list_pending_candidate_evaluation_excludes_already_alerted():
    await early_life_observation.start_tracking("robinhood", "0xpool", "0xtoken", qualified_at=_t(0))
    await early_life_observation.mark_candidate_validated("robinhood", "0xpool", at=_t(5))
    pending = await early_life_observation.list_pending_candidate_evaluation("robinhood", now=_t(10))
    assert pending == []


async def test_list_pending_candidate_evaluation_excludes_security_blocked():
    await early_life_observation.start_tracking("robinhood", "0xpool", "0xtoken", qualified_at=_t(0))
    await early_life_observation.update_security_status("robinhood", "0xpool", "blocked", at=_t(5))
    pending = await early_life_observation.list_pending_candidate_evaluation("robinhood", now=_t(10))
    assert pending == []


async def test_list_pending_candidate_evaluation_includes_unalerted_unblocked():
    await early_life_observation.start_tracking("robinhood", "0xpool", "0xtoken", qualified_at=_t(0))
    pending = await early_life_observation.list_pending_candidate_evaluation("robinhood", now=_t(10))
    assert len(pending) == 1
    assert pending[0]["pool_address"] == "0xpool"


async def test_mark_candidate_suppressed_excludes_from_pending():
    await early_life_observation.start_tracking("robinhood", "0xpool", "0xtoken", qualified_at=_t(0))
    await early_life_observation.mark_candidate_suppressed(
        "robinhood", "0xpool", "matched a recent liquidity signature", at=_t(5),
    )
    pending = await early_life_observation.list_pending_candidate_evaluation("robinhood", now=_t(10))
    assert pending == []
    active = await early_life_observation.list_active("robinhood", now=_t(10))
    assert active[0]["candidate_suppressed_reason"] == "matched a recent liquidity signature"


async def test_list_pending_candidate_evaluation_excludes_expired():
    await early_life_observation.start_tracking("robinhood", "0xpool", "0xtoken", qualified_at=_t(0))
    pending = await early_life_observation.list_pending_candidate_evaluation(
        "robinhood", now=_t(301), window_seconds=300.0,
    )
    assert pending == []


def test_should_retry_x_check_true_when_never_checked():
    assert early_life_observation.should_retry_x_check(None, now=_t(0)) is True


def test_should_retry_x_check_false_within_interval():
    last = _t(0).isoformat()
    assert early_life_observation.should_retry_x_check(
        last, now=_t(5), min_interval_seconds=20.0,
    ) is False


def test_should_retry_x_check_true_past_interval():
    last = _t(0).isoformat()
    assert early_life_observation.should_retry_x_check(
        last, now=_t(21), min_interval_seconds=20.0,
    ) is True


async def test_update_x_status_persists_handle_and_is_readable():
    await early_life_observation.start_tracking("robinhood", "0xpool", "0xtoken", qualified_at=_t(0))
    await early_life_observation.update_x_status("robinhood", "0xpool", "https://x.com/realproject", at=_t(10))
    active = await early_life_observation.list_active("robinhood", now=_t(11))
    assert active[0]["x_status"] == "found"
    assert active[0]["x_handle"] == "https://x.com/realproject"
    assert active[0]["last_x_check_at"] == _t(10).isoformat()


async def test_touch_x_check_at_updates_timestamp_never_status():
    await early_life_observation.start_tracking("robinhood", "0xpool", "0xtoken", qualified_at=_t(0))
    await early_life_observation.touch_x_check_at("robinhood", "0xpool", at=_t(5))
    active = await early_life_observation.list_active("robinhood", now=_t(6))
    assert active[0]["last_x_check_at"] == _t(5).isoformat()
    assert active[0]["x_status"] is None
    assert active[0]["x_handle"] is None


async def test_list_pending_x_evaluation_excludes_not_yet_candidate():
    """Not yet ON-CHAIN+SECURITY validated -- the X gate only evaluates
    candidates that already reached "Candidate", per the operator's own
    distinction between "Candidate" (unchanged, ON-CHAIN+SECURITY) and
    "Candidate Telegram" (ON-CHAIN+SECURITY+X, this new layer)."""
    await early_life_observation.start_tracking("robinhood", "0xpool", "0xtoken", qualified_at=_t(0))
    pending = await early_life_observation.list_pending_x_evaluation("robinhood", now=_t(10))
    assert pending == []


async def test_list_pending_x_evaluation_includes_validated_candidate_without_x():
    await early_life_observation.start_tracking("robinhood", "0xpool", "0xtoken", qualified_at=_t(0))
    await early_life_observation.mark_candidate_validated("robinhood", "0xpool", at=_t(5))
    pending = await early_life_observation.list_pending_x_evaluation("robinhood", now=_t(10))
    assert len(pending) == 1
    assert pending[0]["pool_address"] == "0xpool"


async def test_list_pending_x_evaluation_excludes_x_already_found():
    await early_life_observation.start_tracking("robinhood", "0xpool", "0xtoken", qualified_at=_t(0))
    await early_life_observation.mark_candidate_validated("robinhood", "0xpool", at=_t(5))
    await early_life_observation.update_x_status("robinhood", "0xpool", "https://x.com/realproject", at=_t(6))
    pending = await early_life_observation.list_pending_x_evaluation("robinhood", now=_t(10))
    assert pending == []


async def test_list_pending_x_evaluation_excludes_suppressed():
    await early_life_observation.start_tracking("robinhood", "0xpool", "0xtoken", qualified_at=_t(0))
    await early_life_observation.mark_candidate_validated("robinhood", "0xpool", at=_t(5))
    await early_life_observation.mark_candidate_suppressed("robinhood", "0xpool", "dup", at=_t(6))
    pending = await early_life_observation.list_pending_x_evaluation("robinhood", now=_t(10))
    assert pending == []


async def test_list_pending_x_evaluation_excludes_expired():
    await early_life_observation.start_tracking("robinhood", "0xpool", "0xtoken", qualified_at=_t(0))
    await early_life_observation.mark_candidate_validated("robinhood", "0xpool", at=_t(5))
    pending = await early_life_observation.list_pending_x_evaluation(
        "robinhood", now=_t(301), window_seconds=300.0,
    )
    assert pending == []


async def test_build_snapshot_pool_none_for_unknown_pool():
    pool = await early_life_observation.build_snapshot_pool("robinhood", "0xghost")
    assert pool is None


async def test_build_snapshot_pool_none_without_any_available_observation():
    await early_life_observation.start_tracking(
        "robinhood", "0xpool", "0xtoken", qualified_at=_t(0), symbol="MEME",
    )
    pool = await early_life_observation.build_snapshot_pool("robinhood", "0xpool")
    assert pool is None


async def test_build_snapshot_pool_reflects_latest_available_observation():
    await early_life_observation.start_tracking(
        "robinhood", "0xpool", "0xtoken", qualified_at=_t(0), symbol="MEME",
        total_supply=1_000_000.0, market_cap_usd=3000.0,
    )
    await _record_available_observation(
        "0xpool", at=_t(5), swap_count=1, buy_count=1, sell_count=0,
        reserve_usd=25000.0, price_usd=0.003,
    )
    await _record_available_observation(
        "0xpool", at=_t(65), swap_count=12, buy_count=11, sell_count=1,
        reserve_usd=31000.0, price_usd=0.0041,
    )
    pool = await early_life_observation.build_snapshot_pool("robinhood", "0xpool")
    assert pool is not None
    assert pool.pool_address == "0xpool"
    assert pool.token_address == "0xtoken"
    assert pool.symbol == "MEME"
    assert pool.swap_count == 12
    assert pool.buy_count == 11
    assert pool.sell_count == 1
    assert pool.reserve_usd == 31000.0
    assert pool.price_usd == 0.0041
    assert pool.total_supply == 1_000_000.0
    assert pool.market_cap_usd == 3000.0


async def test_build_snapshot_pool_volume_usd_none_without_a_resolved_rate():
    await early_life_observation.start_tracking("robinhood", "0xpool", "0xtoken", qualified_at=_t(0))
    await _record_available_observation(
        "0xpool", at=_t(5), cumulative_volume_quote=0.5, eth_usd_rate_at_observation=None,
    )
    pool = await early_life_observation.build_snapshot_pool("robinhood", "0xpool")
    assert pool.volume_usd is None


async def test_build_snapshot_pool_volume_usd_converted_when_rate_available():
    await early_life_observation.start_tracking("robinhood", "0xpool", "0xtoken", qualified_at=_t(0))
    await _record_available_observation(
        "0xpool", at=_t(5),
        cumulative_volume_quote=0.5, buy_volume_quote=0.3, sell_volume_quote=0.2,
        eth_usd_rate_at_observation=3000.0,
    )
    pool = await early_life_observation.build_snapshot_pool("robinhood", "0xpool")
    assert pool.volume_usd == pytest.approx(1500.0)
    assert pool.buy_volume_usd == pytest.approx(900.0)
    assert pool.sell_volume_usd == pytest.approx(600.0)
