"""brain_correlation -- multi-brain (ON-CHAIN/SOCIAL/CHART) temporal
correlation per candidate, 03/09 operator go (Chantier C, next level after
ARIA RADAR V1).

Explicitly NOT the Fusion Engine: this module only tracks WHICH brains are
currently positive and valid for a candidate, and reports the convergence
LEVEL (0/3..3/3) -- it never derives a trade decision. No score is ever
invented: a brain is either recorded positive (with its own validity
window) or simply never recorded (never a fabricated negative/neutral
default).

Core scenario under test (operator's own worked example): signals for the
same candidate can arrive at different times -- ON-CHAIN at t0, SOCIAL at
t0+9s -> 2/3, CHART at t0+45s -> 3/3. A signal that has expired (past its
own validity window) must stop counting toward convergence without being
deleted (append-only, same provenance doctrine as every other observation
table in this codebase)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import aiosqlite
import pytest

from aria_core import brain_correlation
from aria_core.paths import configure_data_dir


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    configure_data_dir(str(tmp_path))
    yield


def _t(seconds: int) -> datetime:
    return datetime(2026, 9, 3, 12, 0, 0, tzinfo=timezone.utc) + timedelta(seconds=seconds)


async def test_single_brain_is_1_of_3():
    await brain_correlation.record_signal(
        "0xpool", "robinhood", "on_chain", positive=True,
        observed_at=_t(0), valid_for_seconds=600,
    )
    state = await brain_correlation.correlation_state("0xpool", "robinhood", now=_t(1))
    assert state["level"] == "1/3"
    assert state["brains_positive"] == ["on_chain"]


async def test_two_brains_within_window_is_2_of_3():
    await brain_correlation.record_signal(
        "0xpool", "robinhood", "on_chain", positive=True,
        observed_at=_t(2), valid_for_seconds=600,
    )
    await brain_correlation.record_signal(
        "0xpool", "robinhood", "social", positive=True,
        observed_at=_t(11), valid_for_seconds=600,
    )
    state = await brain_correlation.correlation_state("0xpool", "robinhood", now=_t(12))
    assert state["level"] == "2/3"
    assert set(state["brains_positive"]) == {"on_chain", "social"}


async def test_three_brains_is_3_of_3():
    await brain_correlation.record_signal(
        "0xpool", "robinhood", "on_chain", positive=True, observed_at=_t(2), valid_for_seconds=600,
    )
    await brain_correlation.record_signal(
        "0xpool", "robinhood", "social", positive=True, observed_at=_t(11), valid_for_seconds=600,
    )
    await brain_correlation.record_signal(
        "0xpool", "robinhood", "chart", positive=True, observed_at=_t(47), valid_for_seconds=600,
    )
    state = await brain_correlation.correlation_state("0xpool", "robinhood", now=_t(48))
    assert state["level"] == "3/3"
    assert set(state["brains_positive"]) == {"on_chain", "social", "chart"}


async def test_expired_signal_no_longer_counts():
    await brain_correlation.record_signal(
        "0xpool", "robinhood", "social", positive=True,
        observed_at=_t(0), valid_for_seconds=30,
    )
    state = await brain_correlation.correlation_state("0xpool", "robinhood", now=_t(31))
    assert state["level"] == "0/3"
    assert state["brains_positive"] == []


async def test_expired_signal_not_deleted_append_only():
    await brain_correlation.record_signal(
        "0xpool", "robinhood", "social", positive=True,
        observed_at=_t(0), valid_for_seconds=30,
    )
    async with aiosqlite.connect(brain_correlation._db_path()) as db:
        cur = await db.execute(f"SELECT COUNT(*) FROM {brain_correlation.TABLE}")
        (count,) = await cur.fetchone()
    assert count == 1


async def test_negative_signal_never_counts_as_positive():
    await brain_correlation.record_signal(
        "0xpool", "robinhood", "social", positive=False,
        observed_at=_t(0), valid_for_seconds=600,
    )
    state = await brain_correlation.correlation_state("0xpool", "robinhood", now=_t(1))
    assert state["level"] == "0/3"


async def test_never_recorded_brain_is_absent_not_fabricated_negative():
    """No signal ever recorded for a brain means it's simply absent from
    brains_positive -- never a fabricated False."""
    await brain_correlation.record_signal(
        "0xpool", "robinhood", "on_chain", positive=True, observed_at=_t(0), valid_for_seconds=600,
    )
    state = await brain_correlation.correlation_state("0xpool", "robinhood", now=_t(1))
    assert "social" not in state["brains_positive"]
    assert "chart" not in state["brains_positive"]


async def test_different_candidates_never_cross_contaminate():
    await brain_correlation.record_signal(
        "0xpoolA", "robinhood", "on_chain", positive=True, observed_at=_t(0), valid_for_seconds=600,
    )
    await brain_correlation.record_signal(
        "0xpoolB", "robinhood", "social", positive=True, observed_at=_t(0), valid_for_seconds=600,
    )
    state_a = await brain_correlation.correlation_state("0xpoolA", "robinhood", now=_t(1))
    assert state_a["brains_positive"] == ["on_chain"]


async def test_different_chains_same_pool_address_never_cross_contaminate():
    await brain_correlation.record_signal(
        "0xsame", "robinhood", "on_chain", positive=True, observed_at=_t(0), valid_for_seconds=600,
    )
    await brain_correlation.record_signal(
        "0xsame", "base", "social", positive=True, observed_at=_t(0), valid_for_seconds=600,
    )
    state = await brain_correlation.correlation_state("0xsame", "robinhood", now=_t(1))
    assert state["brains_positive"] == ["on_chain"]


async def test_record_and_check_reports_newly_crossed_2_of_3():
    await brain_correlation.record_signal(
        "0xpool", "robinhood", "on_chain", positive=True, observed_at=_t(0), valid_for_seconds=600,
    )
    result = await brain_correlation.record_signal_and_check_convergence(
        "0xpool", "robinhood", "social", positive=True,
        observed_at=_t(9), valid_for_seconds=600,
    )
    assert result["newly_crossed"] == "2/3"


async def test_record_and_check_never_re_fires_same_level():
    await brain_correlation.record_signal_and_check_convergence(
        "0xpool", "robinhood", "on_chain", positive=True, observed_at=_t(0), valid_for_seconds=600,
    )
    await brain_correlation.record_signal_and_check_convergence(
        "0xpool", "robinhood", "social", positive=True, observed_at=_t(9), valid_for_seconds=600,
    )
    # a redundant re-record of social (e.g. a duplicate call) must not
    # re-fire "2/3" a second time
    result = await brain_correlation.record_signal_and_check_convergence(
        "0xpool", "robinhood", "social", positive=True, observed_at=_t(10), valid_for_seconds=600,
    )
    assert result["newly_crossed"] is None


async def test_record_and_check_never_derives_a_trade_decision():
    result = await brain_correlation.record_signal_and_check_convergence(
        "0xpool", "robinhood", "on_chain", positive=True, observed_at=_t(0), valid_for_seconds=600,
    )
    for forbidden in ("entry", "buy", "sell", "trade", "ENTRY", "BUY", "SELL"):
        assert forbidden not in str(result)
