"""Circuit-breaker transition log (04/08, operator request for permanent
tracking that survives a container redeploy -- the raw Docker stdout/stderr
logs don't). DB isolated per test, no real network call."""
from __future__ import annotations

import asyncio

import pytest

from aria_core import circuit_breaker_log as cbl


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(cbl, "DB_PATH", str(tmp_path / "circuit_breaker_test.db"))


@pytest.mark.asyncio
async def test_record_transition_nowait_persists_opened_event():
    cbl.record_transition_nowait(
        "dexscreener", "opened", consecutive_failures=3, cooldown_seconds=180.0,
        detail="timeout",
    )
    await asyncio.sleep(0.05)  # background task needs one tick to run
    rows = await cbl.recent_events(service="dexscreener")
    assert len(rows) == 1
    assert rows[0]["event"] == "opened"
    assert rows[0]["consecutive_failures"] == 3
    assert rows[0]["detail"] == "timeout"


@pytest.mark.asyncio
async def test_record_transition_nowait_rejects_unknown_event_silently():
    """Same defensive-assert doctrine as rsi_divergence_log.record_divergence
    -- a typo in the event name must never silently create an unanalyzable
    bucket, and must never raise into the caller's real network call."""
    cbl.record_transition_nowait("dexscreener", "not_a_real_event")
    await asyncio.sleep(0.05)
    rows = await cbl.recent_events(service="dexscreener")
    assert rows == []


@pytest.mark.asyncio
async def test_last_event_per_service_returns_most_recent_per_service():
    cbl.record_transition_nowait("dexscreener", "opened", consecutive_failures=3, cooldown_seconds=180.0)
    await asyncio.sleep(0.05)
    cbl.record_transition_nowait("dexscreener", "closed", consecutive_failures=0, cooldown_seconds=0.0)
    await asyncio.sleep(0.05)
    cbl.record_transition_nowait("goplus", "opened", consecutive_failures=5, cooldown_seconds=300.0)
    await asyncio.sleep(0.05)

    latest = await cbl.last_event_per_service()
    assert latest["dexscreener"]["event"] == "closed"
    assert latest["goplus"]["event"] == "opened"


@pytest.mark.asyncio
async def test_count_opened_since_only_counts_opened_events_in_window():
    cbl.record_transition_nowait("dexscreener", "opened", consecutive_failures=3, cooldown_seconds=180.0)
    await asyncio.sleep(0.05)
    cbl.record_transition_nowait("dexscreener", "closed", consecutive_failures=0, cooldown_seconds=0.0)
    await asyncio.sleep(0.05)
    cbl.record_transition_nowait("dexscreener", "opened", consecutive_failures=3, cooldown_seconds=180.0)
    await asyncio.sleep(0.05)

    count = await cbl.count_opened_since("dexscreener", "2000-01-01T00:00:00+00:00")
    assert count == 2


@pytest.mark.asyncio
async def test_count_opened_since_excludes_events_before_the_window():
    cbl.record_transition_nowait("dexscreener", "opened", consecutive_failures=3, cooldown_seconds=180.0)
    await asyncio.sleep(0.05)

    # A window starting in the far future excludes every past event.
    count = await cbl.count_opened_since("dexscreener", "2999-01-01T00:00:00+00:00")
    assert count == 0


@pytest.mark.asyncio
async def test_recent_events_scoped_by_service_never_pools_other_services():
    cbl.record_transition_nowait("dexscreener", "opened", consecutive_failures=3, cooldown_seconds=180.0)
    await asyncio.sleep(0.05)
    cbl.record_transition_nowait("goplus", "opened", consecutive_failures=5, cooldown_seconds=300.0)
    await asyncio.sleep(0.05)

    dex_rows = await cbl.recent_events(service="dexscreener")
    assert len(dex_rows) == 1
    assert dex_rows[0]["service"] == "dexscreener"
