"""The single door to Solana: pooling, failover, spreading, priorities."""
from __future__ import annotations

import pytest

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
    async def test_load_spreads_across_endpoints(self):
        """Capacity ADDS UP -- that is the whole point of several providers."""
        gw, client = _gw(), _Client()
        for _ in range(6):
            await gw.call("getHealth", client=client)
        assert set(client.hits) == {PAID_A, PAID_B}
        assert abs(client.hits.count(PAID_A) - client.hits.count(PAID_B)) <= 1

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
