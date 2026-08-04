"""aria_core.circuit_breaker_status -- live in-memory state aggregation
across the 5 service modules that have a real open/closed circuit (04/08).
Resets every module-level breaker state before/after each test so tests
never leak into each other or into real service state."""
from __future__ import annotations

import asyncio

import pytest

from aria_core import circuit_breaker_log as cbl
from aria_core import circuit_breaker_status as cbs
from aria_core import momentum_entry
from aria_core.services import dexscreener, goplus, wallet_transfers_fast


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(cbl, "DB_PATH", str(tmp_path / "circuit_breaker_status_test.db"))


@pytest.fixture(autouse=True)
def _reset_breaker_state():
    """Every breaker touched by these tests is process-global state -- reset
    before AND after so a failure here never leaks into other test files."""
    def _reset():
        dexscreener._consecutive_failures = 0
        dexscreener._circuit_open_until = 0.0
        goplus.goplus_client._consecutive_failures = 0
        goplus.goplus_client._circuit_open_until = 0.0
        goplus.goplus_client._auth_broken_until = 0.0
        wallet_transfers_fast._consecutive_failures.clear()
        wallet_transfers_fast._circuit_open_until.clear()
        momentum_entry._provider_fail_counts.clear()
        momentum_entry._provider_cooldown_until.clear()
    _reset()
    yield
    _reset()


@pytest.mark.asyncio
async def test_get_circuit_status_covers_all_12_tracked_states_closed_by_default():
    status = await cbs.get_circuit_status()
    expected = {
        "blockscout:base", "dexscreener", "goplus", "goplus_auth",
        "wallet_transfers_alchemy", "wallet_transfers_moralis",
        "ohlcv_geckoterminal", "ohlcv_mobula", "ohlcv_dexpaprika",
        "ohlcv_coinmarketcap", "ohlcv_codex", "ohlcv_dune",
    }
    assert expected.issubset(status.keys())
    for name in expected:
        assert status[name]["state"] == "closed"
        assert status[name]["circuit_state"] == "tracked"


@pytest.mark.asyncio
async def test_get_circuit_status_reflects_dexscreener_open_state_live():
    dexscreener._record_outcome(ok=False)
    dexscreener._record_outcome(ok=False)
    dexscreener._record_outcome(ok=False)
    status = await cbs.get_circuit_status()
    assert status["dexscreener"]["state"] == "open"
    assert status["dexscreener"]["consecutive_failures"] == 3


@pytest.mark.asyncio
async def test_sustained_outage_false_after_a_single_isolated_open():
    dexscreener._record_outcome(ok=False)
    dexscreener._record_outcome(ok=False)
    dexscreener._record_outcome(ok=False)
    await asyncio.sleep(0.05)
    status = await cbs.get_circuit_status()
    assert status["dexscreener"]["opened_count_last_hour"] == 1
    assert status["dexscreener"]["sustained_outage"] is False


@pytest.mark.asyncio
async def test_sustained_outage_true_after_repeated_reopens_within_the_window():
    for _ in range(2):
        dexscreener._record_outcome(ok=False)
        dexscreener._record_outcome(ok=False)
        dexscreener._record_outcome(ok=False)
        await asyncio.sleep(0.05)
        # Resets consecutive_failures to 0 -- enough for the NEXT triple-failure
        # to cross the threshold again and log a fresh "opened" (dexscreener's
        # own _record_outcome doesn't clear _circuit_open_until on success, but
        # that's a pre-existing quirk of the breaker itself, not something this
        # test needs to assert on).
        dexscreener._record_outcome(ok=True)
        await asyncio.sleep(0.05)
    # Re-open a third time so the CURRENT state is "open" (sustained_outage
    # requires both: currently open AND reopened >= threshold in the window).
    dexscreener._record_outcome(ok=False)
    dexscreener._record_outcome(ok=False)
    dexscreener._record_outcome(ok=False)
    await asyncio.sleep(0.05)

    status = await cbs.get_circuit_status()
    assert status["dexscreener"]["opened_count_last_hour"] == 3
    assert status["dexscreener"]["sustained_outage"] is True


@pytest.mark.asyncio
async def test_goplus_auth_state_independent_from_goplus_normal_circuit():
    """The two GoPlus mechanisms (code 4029 -> normal circuit, code 4012 ->
    auth cooldown) must never be conflated -- verified exact in the code
    during the 04/08 cross-review."""
    goplus.goplus_client._auth_broken_until = 99999999999.0
    status = await cbs.get_circuit_status()
    assert status["goplus_auth"]["state"] == "open"
    assert status["goplus"]["state"] == "closed"
