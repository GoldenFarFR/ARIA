"""Shared RPC budget: rate, burst, priority, and the give-up path.

Everything runs against a FAKE clock -- no sleeping, so the timing behaviour is
actually asserted rather than approximated by wall-clock tolerance.
"""
from __future__ import annotations

import pytest

from aria_core.services.solana_rpc_budget import (
    BudgetTimeout,
    Priority,
    SolanaRpcBudget,
)


class FakeClock:
    """Advances only when someone sleeps. Makes every assertion exact."""

    def __init__(self):
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds


def _budget(rate=10.0, burst=10.0):
    clock = FakeClock()
    return SolanaRpcBudget(rate_per_second=rate, burst=burst,
                           clock=clock, sleep=clock.sleep), clock


class TestRateAndBurst:
    @pytest.mark.asyncio
    async def test_a_full_bucket_serves_a_burst_without_waiting(self):
        """The whole point of a bucket over a fixed interval: several loops
        waking together are absorbed, not refused."""
        budget, clock = _budget(rate=10.0, burst=10.0)
        for _ in range(10):
            assert await budget.acquire(Priority.NORMAL) is True
        assert clock.now == 0.0

    @pytest.mark.asyncio
    async def test_past_the_burst_calls_are_paced_at_the_rate(self):
        budget, clock = _budget(rate=10.0, burst=10.0)
        for _ in range(10):
            await budget.acquire(Priority.NORMAL)
        await budget.acquire(Priority.NORMAL)
        # One more token at 10/s means exactly 0.1s of waiting.
        assert clock.now == pytest.approx(0.1, abs=1e-6)

    @pytest.mark.asyncio
    async def test_idle_time_accrues_credit_up_to_the_burst_cap(self):
        """Credit accumulates while quiet -- but never beyond the cap, since
        the provider would refuse a minute's worth spent at once anyway."""
        budget, clock = _budget(rate=10.0, burst=10.0)
        for _ in range(10):
            await budget.acquire(Priority.NORMAL)
        clock.now += 60.0
        assert budget.available() == pytest.approx(10.0)

    def test_a_non_positive_rate_is_refused(self):
        with pytest.raises(ValueError):
            SolanaRpcBudget(rate_per_second=0)


class TestPriority:
    @pytest.mark.asyncio
    async def test_low_priority_gives_up_instead_of_queueing(self):
        """The mechanism that keeps things flowing: a price refresh that waits
        keeps its slot and delays the sell behind it."""
        budget, clock = _budget(rate=1.0, burst=1.0)
        assert await budget.acquire(Priority.NORMAL) is True
        assert await budget.acquire(Priority.LOW) is False
        assert budget.skipped[Priority.LOW] == 1

    @pytest.mark.asyncio
    async def test_high_priority_waits_rather_than_skipping(self):
        """A sell is never dropped -- being late beats not happening."""
        budget, clock = _budget(rate=10.0, burst=1.0)
        await budget.acquire(Priority.HIGH)
        assert await budget.acquire(Priority.HIGH) is True
        assert clock.now > 0.0

    @pytest.mark.asyncio
    async def test_a_normal_caller_stands_aside_while_a_sell_is_pending(self):
        """The ordering rule itself, asserted directly rather than through the
        event loop's scheduling: with a token available and a HIGH caller
        registered as waiting, a NORMAL caller must NOT take it.

        This is what stops a burst of discovery from starving an exit."""
        budget, clock = _budget(rate=10.0, burst=10.0)
        budget._waiting[Priority.HIGH] = 1          # a sell is queued

        assert budget.available() >= 1.0, "a token is available"
        assert budget._higher_pending(Priority.NORMAL) is True
        assert budget._higher_pending(Priority.LOW) is True
        assert budget._higher_pending(Priority.HIGH) is False, (
            "a sell never stands aside for itself"
        )

    @pytest.mark.asyncio
    async def test_a_sell_takes_the_token_a_normal_caller_left(self):
        budget, clock = _budget(rate=10.0, burst=10.0)
        budget._waiting[Priority.HIGH] = 1
        # HIGH itself is not blocked by its own pending count.
        assert await budget.acquire(Priority.HIGH) is True
        assert clock.now == 0.0, "and it did not have to wait"

    @pytest.mark.asyncio
    async def test_high_priority_never_returns_false(self):
        """False means 'I chose to skip', which a sell must never do."""
        budget, clock = _budget(rate=100.0, burst=1.0)
        for _ in range(5):
            assert await budget.acquire(Priority.HIGH) is True


class TestTimeout:
    @pytest.mark.asyncio
    async def test_a_starved_sell_raises_rather_than_hanging(self):
        """Silently blocking an exit forever is worse than surfacing it."""
        budget, clock = _budget(rate=0.001, burst=1.0)
        await budget.acquire(Priority.HIGH)
        with pytest.raises(BudgetTimeout):
            await budget.acquire(Priority.HIGH)

    @pytest.mark.asyncio
    async def test_low_priority_never_raises(self):
        budget, clock = _budget(rate=0.001, burst=1.0)
        await budget.acquire(Priority.NORMAL)
        assert await budget.acquire(Priority.LOW) is False


class TestCalibration:
    def test_the_default_rate_stays_under_the_verified_limit(self):
        """250 rps is Chainstack's GENERAL per-plan dashboard figure on
        Growth, not the real ceiling -- Solana Mainnet's own cap is 50 rps on
        Growth (confirmed live 2026.08.24, see pumpfun_curve_tracker.py's
        CHAINSTACK_MAX_RPS; was 5 rps pre-upgrade on the Developer plan, this
        budget ran at 22.5 -- 90% of the wrong general number -- for days
        while already wired into real-capital callers before that first
        mismatch was caught). Guards the invariant, not the number -- raising
        it past the real ceiling is what produced the outage this module
        exists to fix."""
        from aria_core.services import solana_rpc_budget as mod

        assert mod.DEFAULT_RATE_PER_SECOND <= 50.0 * 0.95

    def test_there_is_a_single_shared_instance(self):
        """A second instance is the same bug as a second throttle."""
        from aria_core.services import solana_rpc_budget as mod

        assert isinstance(mod.budget, SolanaRpcBudget)

    @pytest.mark.asyncio
    async def test_stats_report_what_was_served_and_what_stood_aside(self):
        budget, clock = _budget(rate=1.0, burst=1.0)
        await budget.acquire(Priority.NORMAL)
        await budget.acquire(Priority.LOW)
        stats = budget.stats()
        assert stats["granted"]["NORMAL"] == 1
        assert stats["skipped"]["LOW"] == 1


class TestCallerDiagnostics:
    """27/08, backlog #364 step 1 -- observe-only instrumentation so a real
    collision between this singleton and solana_gateway's own per-endpoint
    buckets can be sized from a week of real data, before any structural fix.
    Must never affect whether permission is granted."""

    @pytest.mark.asyncio
    async def test_a_granted_call_is_counted_under_its_caller(self):
        budget, clock = _budget(rate=10.0, burst=10.0)
        await budget.acquire(Priority.NORMAL, caller="pumpfun_curve_tracker")
        await budget.acquire(Priority.NORMAL, caller="pumpfun_curve_tracker")
        await budget.acquire(Priority.NORMAL, caller="solana_late_bonding_shadow")

        assert budget.calls_by_caller == {
            "pumpfun_curve_tracker": 2,
            "solana_late_bonding_shadow": 1,
        }

    @pytest.mark.asyncio
    async def test_an_unidentified_caller_is_still_counted(self):
        budget, clock = _budget(rate=10.0, burst=10.0)
        await budget.acquire(Priority.NORMAL)  # no caller kwarg passed

        assert budget.calls_by_caller == {"unknown": 1}

    @pytest.mark.asyncio
    async def test_a_skipped_low_priority_call_is_never_counted(self):
        """Diagnostics must reflect real provider traffic -- a LOW call that
        gave up never reached the network, so counting it would overstate the
        real collision risk this instrumentation exists to measure."""
        budget, clock = _budget(rate=1.0, burst=1.0)
        await budget.acquire(Priority.NORMAL, caller="a")
        granted = await budget.acquire(Priority.LOW, caller="a")

        assert granted is False
        assert budget.calls_by_caller == {"a": 1}

    @pytest.mark.asyncio
    async def test_stats_exposes_calls_by_caller(self):
        budget, clock = _budget(rate=10.0, burst=10.0)
        await budget.acquire(Priority.NORMAL, caller="pumpfun_curve_tracker")

        assert budget.stats()["calls_by_caller"] == {"pumpfun_curve_tracker": 1}
