"""Jupiter quote client -- read-only, no key, no signing."""
from __future__ import annotations

import httpx
import pytest

from aria_core.services import jupiter


class _FakeClient:
    def __init__(self, payloads, statuses=None):
        self._payloads = list(payloads)
        self._statuses = list(statuses or [200] * len(payloads))
        self.calls = []

    async def get(self, url, params=None):
        self.calls.append(params)
        status = self._statuses.pop(0)
        payload = self._payloads.pop(0)
        return httpx.Response(status, json=payload, request=httpx.Request("GET", url))

    async def aclose(self):
        pass


@pytest.mark.asyncio
async def test_a_quote_reports_the_guaranteed_amount_not_just_the_headline():
    """`outAmount` is the optimistic figure; `otherAmountThreshold` is what the
    swap is guaranteed to yield at the stated slippage. Reporting only the
    former would flatter every downstream decision by exactly the slippage
    allowance."""
    client = _FakeClient([{"outAmount": "1000000", "otherAmountThreshold": "900000",
                           "priceImpactPct": "0.0123"}])

    quote = await jupiter.fetch_quote("mintA", "mintB", 5_000, client=client)

    assert quote["worst_case_out"] == 900_000
    assert quote["price_impact_pct"] == pytest.approx(1.23)


@pytest.mark.asyncio
async def test_slippage_is_clamped_to_the_project_ceiling():
    """Absolute project rule: never above 10%, never a tool's default. A caller
    asking for more is clamped, not obeyed."""
    client = _FakeClient([{"outAmount": "1", "otherAmountThreshold": "1"}])

    quote = await jupiter.fetch_quote("a", "b", 1, slippage_bps=5000, client=client)

    assert quote["slippage_bps_used"] == jupiter.MAX_SLIPPAGE_BPS
    assert client.calls[0]["slippageBps"] == str(jupiter.MAX_SLIPPAGE_BPS)


@pytest.mark.asyncio
async def test_a_route_less_token_raises_rather_than_returning_zero():
    """A fabricated quote would be acted on -- silence is safer than a zero."""
    client = _FakeClient([{"outAmount": None}])

    with pytest.raises(jupiter.JupiterQuoteError):
        await jupiter.fetch_quote("a", "b", 1, client=client)


@pytest.mark.asyncio
async def test_rate_limiting_is_retried_then_surfaced(monkeypatch):
    """Reactive backoff only: Jupiter's free-tier limit is not published and
    this dome never fabricates a numeric throttle."""
    async def _no_sleep(_s):
        return None

    monkeypatch.setattr(jupiter.asyncio, "sleep", _no_sleep)
    client = _FakeClient([{}, {}, {"outAmount": "5", "otherAmountThreshold": "4"}],
                         statuses=[429, 429, 200])

    quote = await jupiter.fetch_quote("a", "b", 1, client=client)

    assert quote["worst_case_out"] == 4
    assert len(client.calls) == 3


@pytest.mark.asyncio
async def test_a_non_positive_amount_is_refused():
    with pytest.raises(jupiter.JupiterQuoteError):
        await jupiter.fetch_quote("a", "b", 0)


def test_the_module_cannot_sign_or_send_anything():
    """Read-only by construction: this is the half of the swap path that
    touches nothing, and it must stay that way until signing is a separate,
    explicitly-authorised step."""
    import ast
    import inspect

    # AST, not a text scan: a text scan flags the module's OWN docstring
    # ("no key, no signing") and would either fail wrongly or be loosened
    # until it checks nothing. Same lesson already paid on
    # `safe_robinhood_simulation`.
    tree = ast.parse(inspect.getsource(jupiter))
    names = {
        node.attr if isinstance(node, ast.Attribute) else node.id
        for node in ast.walk(tree)
        if isinstance(node, (ast.Attribute, ast.Name))
    }
    for forbidden in ("Keypair", "sign_transaction", "sign_message", "send_raw_transaction"):
        assert forbidden not in names, f"the quote client must not call {forbidden}"

    # and it must never reach Jupiter's transaction-building endpoint
    urls = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    assert not any("swap/v1/swap" in u or "/v6/swap" in u for u in urls), (
        "building a swap transaction is a separate, explicitly-authorised step"
    )


@pytest.mark.asyncio
async def test_a_token_with_no_exit_route_is_flagged_unsellable():
    """A route in but none out is the clearest honeypot signature there is."""
    class _NoExit:
        def __init__(self):
            self.calls = 0

        async def get(self, url, params=None):
            self.calls += 1
            if self.calls == 1:
                return httpx.Response(200, json={"outAmount": "1000", "otherAmountThreshold": "900"},
                                      request=httpx.Request("GET", url))
            return httpx.Response(200, json={"outAmount": None},
                                  request=httpx.Request("GET", url))

        async def aclose(self):
            pass

    out = await jupiter.roundtrip_cost_pct("scamMint", 0.01, client=_NoExit())
    assert out["sellable"] is False
    assert out["roundtrip_loss_pct"] is None


@pytest.mark.asyncio
async def test_a_healthy_token_reports_its_real_roundtrip_cost():
    class _Healthy:
        def __init__(self):
            self.calls = 0

        async def get(self, url, params=None):
            self.calls += 1
            if self.calls == 1:
                return httpx.Response(200, json={"outAmount": "1000000", "otherAmountThreshold": "9"},
                                      request=httpx.Request("GET", url))
            # sells back 97.5% of the lamports put in
            return httpx.Response(200, json={"outAmount": str(int(0.01 * 1e9 * 0.975)),
                                             "otherAmountThreshold": "1"},
                                  request=httpx.Request("GET", url))

        async def aclose(self):
            pass

    out = await jupiter.roundtrip_cost_pct("goodMint", 0.01, client=_Healthy())
    assert out["sellable"] is True
    assert out["roundtrip_loss_pct"] == pytest.approx(2.5, abs=0.01)


@pytest.mark.asyncio
async def test_a_provider_failure_reports_none_never_a_silent_false():
    """Refusing a token because a provider hiccuped would be worse than not
    checking at all."""
    class _Broken:
        async def get(self, *a, **k):
            raise RuntimeError("provider down")

        async def aclose(self):
            pass

    out = await jupiter.roundtrip_cost_pct("anyMint", 0.01, client=_Broken())
    assert out["sellable"] is None
