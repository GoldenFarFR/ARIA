"""Real Jupiter swap, proven against mainnet state without sending it."""
from __future__ import annotations

import base64

import httpx
import pytest

from aria_core.onchain import jupiter_swap_simulation as sim

_FAKE_TX = base64.b64encode(b"a-real-looking-transaction").decode()


class _Client:
    def __init__(self, responses):
        self._responses = list(responses)
        self.posts = []
        self.gets = []

    async def post(self, url, json=None):
        self.posts.append((url, json))
        status, payload = self._responses.pop(0)
        return httpx.Response(status, json=payload, request=httpx.Request("POST", url))

    async def get(self, url, params=None):
        self.gets.append(params)
        status, payload = self._responses.pop(0)
        return httpx.Response(status, json=payload, request=httpx.Request("GET", url))

    async def aclose(self):
        pass


@pytest.mark.asyncio
async def test_a_viable_swap_reports_ok_with_its_compute_cost():
    client = _Client([(200, {"result": {"value": {"err": None, "unitsConsumed": 180_000,
                                                  "logs": ["Program log: swap"]}}})])

    out = await sim.simulate_swap_transaction(_FAKE_TX, rpc_http_url="https://rpc", client=client)

    assert out["ok"] is True
    assert out["compute_units"] == 180_000
    body = client.posts[0][1]["params"][1]
    # unsigned simulation is only possible because signature checking is off
    assert body["sigVerify"] is False
    # a stale blockhash would look like a broken swap when the quote merely expired
    assert body["replaceRecentBlockhash"] is True


@pytest.mark.asyncio
async def test_a_swap_that_would_fail_on_chain_is_reported_as_failing():
    """A swap reported as viable when it is not would be acted on. This is the
    whole reason to simulate before committing capital."""
    client = _Client([(200, {"result": {"value": {"err": {"InstructionError": [2, "Custom"]},
                                                  "unitsConsumed": 40_000, "logs": []}}})])

    out = await sim.simulate_swap_transaction(_FAKE_TX, rpc_http_url="https://rpc", client=client)

    assert out["ok"] is False
    assert out["error"] is not None


@pytest.mark.asyncio
async def test_implausible_compute_usage_raises_rather_than_reporting_success():
    client = _Client([(200, {"result": {"value": {"err": None,
                                                  "unitsConsumed": 5_000_000, "logs": []}}})])

    with pytest.raises(sim.SwapSimulationError):
        await sim.simulate_swap_transaction(_FAKE_TX, rpc_http_url="https://rpc", client=client)


@pytest.mark.asyncio
async def test_building_a_swap_takes_a_public_key_and_returns_an_unsigned_tx():
    client = _Client([(200, {"swapTransaction": _FAKE_TX})])

    tx = await sim.build_swap_transaction(
        {"outAmount": "1", "slippage_bps_used": 1000}, "PubKey111", client=client,
    )

    assert tx == _FAKE_TX
    assert client.posts[0][1]["userPublicKey"] == "PubKey111"
    assert client.posts[0][1]["wrapAndUnwrapSol"] is True


@pytest.mark.asyncio
async def test_a_quote_above_the_slippage_ceiling_is_refused():
    """Absolute project rule, enforced again at the build step rather than
    trusted from upstream."""
    with pytest.raises(sim.SwapSimulationError):
        await sim.build_swap_transaction(
            {"outAmount": "1", "slippage_bps_used": 3000}, "PubKey111",
        )


@pytest.mark.asyncio
async def test_a_missing_swap_transaction_raises():
    client = _Client([(200, {})])

    with pytest.raises(sim.SwapSimulationError):
        await sim.build_swap_transaction({"outAmount": "1"}, "PubKey111", client=client)


def test_the_module_has_no_send_path_and_no_key_handling():
    """Structural guardrail: signing and sending stay a separate, explicitly
    authorised step. Checked by AST, not by text -- a text scan trips on this
    module's own docstring, a lesson already paid twice in this repo today."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(sim))
    names = {
        n.attr if isinstance(n, ast.Attribute) else n.id
        for n in ast.walk(tree) if isinstance(n, (ast.Attribute, ast.Name))
    }
    for forbidden in ("Keypair", "sign_transaction", "sign_message", "sign"):
        assert forbidden not in names, f"this module must never call {forbidden}"

    literals = {n.value for n in ast.walk(tree)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    assert "sendTransaction" not in literals, "this module must never send"
    assert any("simulateTransaction" == v for v in literals), "it must simulate"


@pytest.mark.asyncio
async def test_a_program_address_is_refused_as_a_payer():
    """21/08 -- passing the System Program as `userPublicKey` produced the
    opaque RPC error "Transaction failed to sanitize accounts offsets
    correctly", costing half an hour debugging a format problem that never
    existed. The guard turns it into an immediate, explicit refusal."""
    with pytest.raises(sim.SwapSimulationError, match="program address"):
        await sim.build_swap_transaction(
            {"outAmount": "1", "slippage_bps_used": 1000},
            "11111111111111111111111111111111",
        )


@pytest.mark.asyncio
async def test_our_derived_fields_are_stripped_before_the_build():
    """Jupiter validates the quote object it receives and rejects unknown
    keys, so the enrichment added by our own client must not leak into it."""
    client = _Client([(200, {"swapTransaction": _FAKE_TX})])

    await sim.build_swap_transaction(
        {"outAmount": "1", "slippage_bps_used": 1000, "worst_case_out": 9,
         "price_impact_pct": 1.5, "inputMint": "a"},
        "SomeRealLookingPubkey1111111111111111111111", client=client,
    )

    sent = client.posts[0][1]["quoteResponse"]
    assert "worst_case_out" not in sent
    assert "price_impact_pct" not in sent
    assert "slippage_bps_used" not in sent
    assert sent["inputMint"] == "a"
