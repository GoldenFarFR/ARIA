"""The single door to Solana: pooling, failover, spreading, priorities."""
from __future__ import annotations

import pytest

from aria_core.services import chainstack_ru_budget
from aria_core.services.solana_gateway import SolanaGateway
from aria_core.services.solana_rpc_budget import Priority

PAID_A = "https://a.paid.example/rpc"
PAID_B = "https://b.paid.example/rpc"


class _Resp:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body if body is not None else {"result": "ok"}

    def json(self):
        return self._body


class _Client:
    """Records which endpoint each call went to."""

    def __init__(self, responses=None):
        self.hits: list[str] = []
        self.responses = responses or {}

    async def post(self, url, json=None, **kw):
        self.hits.append(url)
        answer = self.responses.get(url, _Resp())
        if isinstance(answer, Exception):
            raise answer
        return answer


def _gw(urls=((PAID_A, True), (PAID_B, True))) -> SolanaGateway:
    gw = SolanaGateway(rate_per_second=1000.0)   # rate is not what is tested here
    gw.configure(urls=list(urls))
    return gw


class TestPooling:
    @pytest.mark.asyncio
    async def test_traffic_stays_on_the_first_endpoint_while_it_has_headroom(self):
        """Cascade, not round-robin (operator's design, 22/08). Round-robin
        sent as much traffic to the backup as to the primary even when the
        primary had headroom, and burned the backup that a burst needs."""
        gw, client = _gw(), _Client()
        for _ in range(3):
            await gw.call("getHealth", client=client)
        assert set(client.hits) == {PAID_A}, "the second provider stays fresh"

    @pytest.mark.asyncio
    async def test_traffic_spills_to_the_next_endpoint_past_the_threshold(self):
        """The spill happens BEFORE the ceiling: reaching it is the refusal."""
        from aria_core.services import solana_gateway as mod

        gw = SolanaGateway(rate_per_second=10.0)
        gw.configure(urls=[(PAID_A, True), (PAID_B, True)])
        client = _Client()
        # Drain the first endpoint past OVERFLOW_AT without spending real time.
        first = gw._endpoints[0]
        first.budget._tokens = first.budget.burst * (1 - mod.OVERFLOW_AT) * 0.5
        await gw.call("getHealth", client=client)
        assert client.hits == [PAID_B]

    @pytest.mark.asyncio
    async def test_a_saturated_pool_serves_only_exits(self):
        """Back-pressure (operator's design): under critical pressure the pool
        stops taking anything that is not a sell, BEFORE being refused. Asking
        and being refused still costs a round trip; standing down costs nothing
        and leaves the capacity to whoever must get through."""
        gw = SolanaGateway(rate_per_second=1000.0)
        gw.configure(urls=[(PAID_A, True), (PAID_B, True)])
        for endpoint in gw._endpoints:
            endpoint.budget._tokens = 0.0

        client = _Client()
        assert gw.level() == "critical"
        assert await gw.call("getHealth", client=client) is None, "normal stands down"
        assert await gw.call(
            "sendTransaction", client=client, priority=Priority.HIGH
        ) == {"result": "ok"}, "a sell always gets through"

    @pytest.mark.asyncio
    async def test_duplicates_are_dropped(self):
        gw = _gw(urls=[(PAID_A, True), (PAID_A, True), (PAID_B, True)])
        assert len(gw._endpoints) == 2

    def test_public_endpoints_are_added_last(self):
        gw = SolanaGateway()
        gw.configure(urls=None)
        assert gw._endpoints, "there is always at least a public floor"
        assert gw._endpoints[-1].paid is False


class TestFailover:
    @pytest.mark.asyncio
    async def test_a_429_benches_that_endpoint_and_the_call_still_succeeds(self):
        """One provider dying must not fail the call -- the exact failure of
        22/08, when Helius' quota ran out and every module fell over while a
        healthy provider sat unused."""
        gw = _gw()
        client = _Client({PAID_A: _Resp(429), PAID_B: _Resp(200)})
        out = await gw.call("getHealth", client=client)
        assert out == {"result": "ok"}
        assert PAID_B in client.hits

    @pytest.mark.asyncio
    async def test_a_403_is_treated_as_quota_not_as_a_blip(self):
        """Chainstack answered 403 with its quota spent. Retrying it in two
        minutes would just burn the retry."""
        from aria_core.services import solana_gateway as mod

        gw = _gw()
        client = _Client({PAID_A: _Resp(403), PAID_B: _Resp(200)})
        await gw.call("getHealth", client=client)
        benched = [e for e in gw._endpoints if e.url == PAID_A][0]
        assert benched.benched_until > 0
        assert not benched.healthy()

    @pytest.mark.asyncio
    async def test_a_network_error_falls_through_to_the_next(self):
        gw = _gw()
        client = _Client({PAID_A: RuntimeError("connection reset")})
        assert await gw.call("getHealth", client=client) == {"result": "ok"}

    @pytest.mark.asyncio
    async def test_every_endpoint_down_returns_None_never_a_fake_result(self):
        """None means 'could not be done', and a caller must never read it as
        an empty answer -- treating unknown as zero is how this dome loses
        money."""
        gw = _gw()
        client = _Client({PAID_A: _Resp(429), PAID_B: _Resp(429)})
        assert await gw.call("getHealth", client=client) is None

    @pytest.mark.asyncio
    async def test_public_endpoints_take_over_when_paid_ones_are_down(self):
        gw = _gw(urls=[(PAID_A, True), ("https://public.example/rpc", False)])
        client = _Client({PAID_A: _Resp(429)})
        assert await gw.call("getHealth", client=client) == {"result": "ok"}
        assert "https://public.example/rpc" in client.hits


class TestChainstackRuAccounting:
    """26/08 -- this gateway is the single door 5 real-money-adjacent callers
    (jupiter_swap_signer.py, solana_rent_recovery.py, solana_agent_wallet.py,
    pumpfun_curve_price.py) share, and none of them was ever counted against
    chainstack_ru_budget before this. Measured gap: 419,197 real RU billed on
    Solana on 25/08 vs 57,796 this dome's own budget saw that day."""

    def setup_method(self):
        chainstack_ru_budget._pending_units.clear()

    def teardown_method(self):
        chainstack_ru_budget._pending_units.clear()

    @pytest.mark.asyncio
    async def test_a_paid_endpoint_attempt_counts_one_ru_even_on_failure(self):
        """Counted before the attempt, not after success: Chainstack bills on
        receipt, so a 429/5xx/timeout still spent the RU."""
        gw = _gw(urls=[(PAID_A, True), (PAID_B, True)])
        client = _Client({PAID_A: _Resp(500)})
        await gw.call("getHealth", client=client)
        # One attempt on PAID_A (failed, benched) + one on PAID_B (succeeded).
        assert chainstack_ru_budget._pending_units.get("solana") == 2

    @pytest.mark.asyncio
    async def test_a_public_endpoint_attempt_never_spends_chainstack_ru(self):
        gw = _gw(urls=[(PAID_A, True), ("https://public.example/rpc", False)])
        client = _Client({PAID_A: _Resp(429)})
        await gw.call("getHealth", client=client)
        # Only the paid attempt on PAID_A should count -- the public fallback
        # that actually served the response must not.
        assert chainstack_ru_budget._pending_units.get("solana") == 1


class TestPriority:
    @pytest.mark.asyncio
    async def test_a_low_priority_call_gives_up_rather_than_queueing(self):
        # Very low rate: the bucket cannot refill between the two calls,
        # so the assertion does not depend on wall-clock timing.
        gw = SolanaGateway(rate_per_second=0.01)
        gw.configure(urls=[(PAID_A, True)])
        client = _Client()
        assert await gw.call("getHealth", client=client) is not None
        # Bucket empty: LOW skips its turn instead of delaying a sell.
        assert await gw.call("getHealth", client=client, priority=Priority.LOW) is None

    @pytest.mark.asyncio
    async def test_a_sell_is_served_even_when_the_bucket_is_empty(self):
        gw = SolanaGateway(rate_per_second=100.0)
        gw.configure(urls=[(PAID_A, True)])
        client = _Client()
        for _ in range(3):
            assert await gw.call(
                "sendTransaction", client=client, priority=Priority.HIGH
            ) is not None


class TestSizing:
    def test_total_rate_adds_up_across_healthy_endpoints(self):
        """Two providers at 22.5 give 45 -- that is what makes a 59-position
        exit survivable."""
        gw = SolanaGateway(rate_per_second=22.5)
        gw.configure(urls=[(PAID_A, True), (PAID_B, True)])
        assert gw.stats()["total_rate_per_second"] == pytest.approx(45.0)

    def test_a_benched_endpoint_no_longer_counts_toward_capacity(self):
        gw = SolanaGateway(rate_per_second=22.5)
        gw.configure(urls=[(PAID_A, True), (PAID_B, True)])
        gw._endpoints[0].bench(quota=True)
        assert gw.stats()["total_rate_per_second"] == pytest.approx(22.5)

    def test_there_is_a_single_shared_gateway(self):
        from aria_core.services import solana_gateway as mod

        assert isinstance(mod.gateway, SolanaGateway)


class TestPressureFeedback:
    """Throttles now report a POOL-wide state, so a chain failure is visible
    before it happens rather than through the errors it causes."""

    def test_pressure_ignores_benched_endpoints(self):
        """A benched endpoint contributes no capacity. Counting it as idle
        would report calm while the survivors drown -- the exact blindness that
        let the 847-error storm build unnoticed."""
        gw = SolanaGateway(rate_per_second=10.0)
        gw.configure(urls=[(PAID_A, True), (PAID_B, True)])
        gw._endpoints[0].budget._tokens = 0.0        # drained
        gw._endpoints[1].bench(quota=True)           # benched
        # Not exactly 1.0: the bucket refills continuously, so an equality here
        # would fail on the microseconds between draining and measuring.
        assert gw.pressure() > 0.99

    def test_every_endpoint_down_reads_as_full_pressure(self):
        gw = SolanaGateway()
        gw.configure(urls=[(PAID_A, True)])
        gw._endpoints[0].bench(quota=True)
        assert gw.pressure() == 1.0
        assert gw.level() == "critical"

    def test_low_priority_stands_down_before_normal_does(self):
        """Graduated response: ease off the cheapest work first."""
        from aria_core.services import solana_gateway as mod

        gw = SolanaGateway(rate_per_second=10.0)
        gw.configure(urls=[(PAID_A, True)])
        endpoint = gw._endpoints[0]
        endpoint.budget._tokens = endpoint.budget.burst * (1 - mod.PRESSURE_TENSE) * 0.9

        assert gw.level() == "tense"
        assert gw.should_stand_down(Priority.LOW) is True
        assert gw.should_stand_down(Priority.NORMAL) is False
        assert gw.should_stand_down(Priority.HIGH) is False

    def test_a_sell_never_stands_down(self):
        gw = SolanaGateway(rate_per_second=10.0)
        gw.configure(urls=[(PAID_A, True)])
        gw._endpoints[0].budget._tokens = 0.0
        assert gw.level() == "critical"
        assert gw.should_stand_down(Priority.HIGH) is False

    def test_stats_expose_the_level_for_diagnostics(self):
        gw = SolanaGateway()
        gw.configure(urls=[(PAID_A, True)])
        stats = gw.stats()
        assert "pressure" in stats and "level" in stats


class TestSharedBudgetCollisionDiagnostics:
    """27/08, backlog #364 step 1 -- observe-only. This gateway's own
    per-endpoint buckets and solana_rpc_budget's shared singleton are two
    SEPARATE regulators for the same real providers; a caller that acquires
    from the singleton directly still makes its own HTTP call outside this
    gateway. These tests only cover visibility, never behaviour."""

    @pytest.fixture(autouse=True)
    def _reset_shared_budget(self):
        from aria_core.services import solana_rpc_budget as shared

        shared.budget.calls_by_caller = {}
        yield
        shared.budget.calls_by_caller = {}

    @pytest.mark.asyncio
    async def test_stats_expose_direct_caller_traffic_alongside_gateway_stats(self):
        from aria_core.services import solana_rpc_budget as shared

        await shared.acquire(Priority.NORMAL, caller="pumpfun_curve_tracker")

        gw = SolanaGateway()
        gw.configure(urls=[(PAID_A, True)])
        assert gw.stats()["shared_budget_direct_calls"] == {"pumpfun_curve_tracker": 1}

    def test_a_pressure_transition_logs_the_direct_caller_totals(self, caplog):
        import logging

        from aria_core.services import solana_rpc_budget as shared

        shared.budget.calls_by_caller = {"pumpfun_curve_tracker": 3}

        gw = SolanaGateway(rate_per_second=10.0)
        gw.configure(urls=[(PAID_A, True)])
        gw._endpoints[0].budget._tokens = 0.0  # forces calm -> critical

        with caplog.at_level(logging.WARNING):
            assert gw.level() == "critical"

        assert any("pumpfun_curve_tracker" in r.message for r in caplog.records)


class TestSelfRegulation:
    """Budget-aware, not just rate-aware. An endpoint can be perfectly fluid
    second to second and still burn a month's quota in three days -- which is
    what happened to Helius, invisible to instantaneous throttling."""

    def test_an_unmetered_endpoint_has_no_burn_rate(self):
        """No published quota means no guessed one, per the dome rule."""
        gw = _gw(urls=[(PAID_A, True)])
        assert gw._endpoints[0].burn_rate() is None

    def test_spending_ahead_of_schedule_shows_a_burn_rate_above_one(self):
        import time as _t

        gw = _gw(urls=[(PAID_A, True)])
        e = gw._endpoints[0]
        e.quota_total = 1000
        e.quota_period_seconds = 1000.0
        e.quota_started_at = _t.monotonic() - 100.0    # 10% of the period gone
        e.quota_spent = 300                            # but 30% of the budget
        assert e.burn_rate() == pytest.approx(3.0, abs=0.1)

    def test_an_endpoint_burning_too_fast_is_skipped_while_another_has_room(self):
        """The pool rebalances ITSELF rather than waiting for a quota to run
        out -- that is the self-managed part."""
        import time as _t

        gw = _gw()
        greedy = gw._endpoints[0]
        greedy.quota_total = 1000
        greedy.quota_period_seconds = 1000.0
        greedy.quota_started_at = _t.monotonic() - 100.0
        greedy.quota_spent = 500                       # far ahead of schedule
        assert gw._pick().url == PAID_B

    def test_exhaustion_is_predicted_before_it_happens(self):
        """The 'I cannot take requests until ...' figure, known in advance."""
        import time as _t

        gw = _gw(urls=[(PAID_A, True)])
        e = gw._endpoints[0]
        e.quota_total = 1000
        e.quota_spent = 900
        e.quota_started_at = _t.monotonic() - 900.0    # 1 call/s
        left = e.exhausts_in_seconds()
        assert left == pytest.approx(100.0, rel=0.1)

    def test_a_predicted_exhaustion_raises_a_warning(self):
        import time as _t

        gw = _gw(urls=[(PAID_A, True)])
        e = gw._endpoints[0]
        e.quota_total = 1000
        e.quota_spent = 990
        e.quota_started_at = _t.monotonic() - 990.0
        assert any("quota epuise" in w for w in gw.warnings())

    def test_losing_the_last_spare_endpoint_is_surfaced(self):
        """One healthy endpoint out of several means no failover left -- worth
        knowing before the last one dies too."""
        gw = _gw()
        gw._endpoints[0].bench(quota=True)
        assert any("un seul endpoint sain" in w for w in gw.warnings())


class TestMemoryAcrossRestarts:
    """This service was restarted a dozen times in one night. Without memory,
    each restart forgets which provider is exhausted and hammers it again."""

    def test_a_benched_endpoint_stays_benched_after_a_restart(self, tmp_path):
        path = str(tmp_path / "state.json")
        gw = _gw()
        gw._endpoints[0].bench(quota=True)
        gw.save_state(path)

        fresh = _gw()
        fresh.load_state(path)
        assert not fresh._endpoints[0].healthy()

    def test_quota_spending_survives_a_restart(self, tmp_path):
        path = str(tmp_path / "state.json")
        gw = _gw()
        gw._endpoints[0].quota_total = 1000
        gw._endpoints[0].quota_spent = 400
        gw.save_state(path)

        fresh = _gw()
        fresh.load_state(path)
        assert fresh._endpoints[0].quota_spent == 400

    def test_a_missing_state_file_is_simply_a_fresh_start(self, tmp_path):
        gw = _gw()
        gw.load_state(str(tmp_path / "absent.json"))
        assert all(e.healthy() for e in gw._endpoints)

    def test_a_corrupt_state_file_never_raises(self, tmp_path):
        path = tmp_path / "state.json"
        path.write_text("{ not json")
        gw = _gw()
        gw.load_state(str(path))
        assert all(e.healthy() for e in gw._endpoints)
