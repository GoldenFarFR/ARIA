"""Dormant CDP Policy definition for the REAL agent-wallet pilot EOA.

Same doctrine as `test_agent_wallet_smart_swing.py`: nothing here touches the
network, no CDP credential is read, no policy is created on a real account --
the one function that could create anything takes an INJECTED (mocked) client
and an explicit operator acknowledgement. The tests validate that the objects
really construct (and really serialize) against the actually-installed
cdp-sdk 1.47.1, never against a hand-written stand-in."""
from __future__ import annotations

from pathlib import Path

import pytest

from aria_core import agent_wallet_cdp_policy as pol
from aria_core import agent_wallet_pilot as pilot
from aria_core.agent_wallet_cdp_adapter import USDC_BASE_ADDRESS, WALLET_NAME

ROUTER = "0x1111111111111111111111111111111111111111"
ROUTER_2 = "0x2222222222222222222222222222222222222222"


# ── constants / single source of truth ───────────────────────────────────────


def test_cap_is_derived_from_the_pilot_constant():
    """The CDP-side cap must be the SAME number the application layer enforces
    -- imported symbolically, never a second value that could silently drift."""
    assert pol.per_tx_cap_cents() == int(round(pilot.MAX_TRANSACTION_USD * 100))
    assert pol.per_tx_cap_cents() >= 0  # cdp-sdk field constraint (ge=0)


def test_target_account_tracks_the_adapter_wallet_name():
    assert pol.TARGET_ACCOUNT_NAME == WALLET_NAME


def test_module_never_hardcodes_the_real_capital_constants():
    """Guard against a copy-paste drift: neither the transfer address nor the
    cap may be re-typed here -- both come from agent_wallet_pilot."""
    src = Path(pol.__file__).read_text(encoding="utf-8")
    body = src.split('"""', 2)[-1]  # skip the module docstring
    assert pilot.ALLOWED_TRANSFER_ADDRESS not in body
    assert str(pilot.MAX_TRANSACTION_USD) not in body


def test_description_respects_the_cdp_api_constraint():
    """CDP rejects a description that doesn't match ^[A-Za-z0-9 ,.]{1,50}$
    (verified in the installed openapi model) -- at creation time, not at build
    time, so a bad description would only fail against the real API."""
    import re

    assert re.match(r"^[A-Za-z0-9 ,.]{1,50}$", pol.POLICY_DESCRIPTION)


# ── build_pilot_bounded_policy ───────────────────────────────────────────────


def test_policy_scope_and_two_accept_rules():
    p = pol.build_pilot_bounded_policy([ROUTER])
    # account scope, never project -- a project policy would also govern the
    # other CDP accounts (x402 wallet, smart-swing spender).
    assert p.scope == "account"
    assert len(p.rules) == 2
    assert all(r.action == "accept" for r in p.rules)
    # everything unmatched is default-denied by the Policy Engine: that is the
    # actual guardrail, so there must be no permissive catch-all rule.
    assert all(r.criteria for r in p.rules)


def test_rule1_allowlists_the_given_routers_and_bounds_usd():
    p = pol.build_pilot_bounded_policy([ROUTER, ROUTER_2])
    crits = p.rules[0].criteria
    assert [c.type for c in crits] == ["evmAddress", "netUSDChange"]
    assert crits[0].operator == "in"
    assert crits[0].addresses == [ROUTER, ROUTER_2]
    assert crits[1].operator == "<="
    assert crits[1].changeCents == pol.per_tx_cap_cents()


def test_rule2_pins_the_single_transfer_destination_by_decoded_param():
    """The pilot's USDC transfer is an ERC-20 call whose tx `to` is the USDC
    CONTRACT (verified in cdp-sdk's account_transfer_strategy) -- only a decoded
    data criterion can pin the real recipient."""
    p = pol.build_pilot_bounded_policy([ROUTER])
    crits = p.rules[1].criteria
    assert [c.type for c in crits] == ["evmData", "netUSDChange"]
    data_crit = crits[0]
    assert data_crit.abi.value == "erc20"
    cond = data_crit.conditions[0]
    assert cond.function == "transfer"
    param = cond.params[0]
    assert param.name == "to"
    assert param.operator == "in"
    assert param.values == [pilot.ALLOWED_TRANSFER_ADDRESS]
    assert crits[1].changeCents == pol.per_tx_cap_cents()


def test_transfer_destination_is_never_an_address_criterion():
    """Regression guard on the exact mistake this design avoids: an
    EvmAddressCriterion containing ALLOWED_TRANSFER_ADDRESS would match no real
    ERC-20 transfer at all (the tx targets the token contract), silently denying
    every legitimate transfer."""
    p = pol.build_pilot_bounded_policy([ROUTER])
    for rule in p.rules:
        for crit in rule.criteria:
            if crit.type == "evmAddress":
                assert pilot.ALLOWED_TRANSFER_ADDRESS not in crit.addresses
                assert USDC_BASE_ADDRESS not in crit.addresses


def test_both_rules_carry_the_same_usd_cap():
    p = pol.build_pilot_bounded_policy([ROUTER])
    caps = [c.changeCents for r in p.rules for c in r.criteria if c.type == "netUSDChange"]
    assert caps == [pol.per_tx_cap_cents(), pol.per_tx_cap_cents()]


@pytest.mark.parametrize("bad", [[], None, [""], ["   "]])
def test_rejects_an_empty_router_allowlist(bad):
    """Fail-closed: an empty rule 1 would deny every swap."""
    with pytest.raises(ValueError):
        pol.build_pilot_bounded_policy(bad)


@pytest.mark.parametrize("bad", ["0x123", "not-an-address", "0x" + "z" * 40, "1" * 42])
def test_rejects_a_malformed_router(bad):
    with pytest.raises(ValueError):
        pol.build_pilot_bounded_policy([bad])


def test_rejects_the_usdc_contract_as_a_router():
    """Allowlisting a token contract by ADDRESS would accept transfer(anyone,
    anything) and bypass the single-destination pin entirely."""
    with pytest.raises(ValueError):
        pol.build_pilot_bounded_policy([USDC_BASE_ADDRESS])
    with pytest.raises(ValueError):
        pol.build_pilot_bounded_policy([ROUTER, USDC_BASE_ADDRESS.lower()])


def test_rejects_the_transfer_address_as_a_router():
    with pytest.raises(ValueError):
        pol.build_pilot_bounded_policy([pilot.ALLOWED_TRANSFER_ADDRESS])


def test_policy_is_accepted_by_the_real_sdk_request_model():
    """Beyond model_dump: run the object through the SAME transformation
    `PoliciesClient.create_policy` applies, so a shape CDP would reject (bad
    description, unsupported criterion combination) fails HERE, not against the
    real API with real capital attached."""
    from cdp.openapi_client.models.create_policy_request import CreatePolicyRequest
    from cdp.policies.request_transformer import map_request_rules_to_openapi_format

    p = pol.build_pilot_bounded_policy([ROUTER])
    req = CreatePolicyRequest(
        scope=p.scope,
        description=p.description,
        rules=map_request_rules_to_openapi_format(p.rules),
    )
    assert req.scope == "account"
    assert len(req.rules) == 2


# ── create_pilot_bounded_policy (injected client, never a live one) ──────────


class _FakePoliciesClient:
    def __init__(self):
        self.calls = []

    async def create_policy(self, *, policy, idempotency_key=None):
        self.calls.append((policy, idempotency_key))
        return {"id": "policy-fake"}


@pytest.mark.asyncio
async def test_creation_refuses_without_an_explicit_operator_ack():
    client = _FakePoliciesClient()
    with pytest.raises(PermissionError):
        await pol.create_pilot_bounded_policy(
            policies_client=client, router_addresses=[ROUTER],
        )
    assert client.calls == []  # nothing was ever sent


@pytest.mark.asyncio
async def test_creation_refuses_without_an_injected_client():
    with pytest.raises(ValueError):
        await pol.create_pilot_bounded_policy(
            policies_client=None, router_addresses=[ROUTER], operator_ack=True,
        )


@pytest.mark.asyncio
async def test_creation_validates_the_routers_before_calling_the_client():
    client = _FakePoliciesClient()
    with pytest.raises(ValueError):
        await pol.create_pilot_bounded_policy(
            policies_client=client, router_addresses=["bad"], operator_ack=True,
        )
    assert client.calls == []


@pytest.mark.asyncio
async def test_creation_sends_exactly_the_built_policy():
    client = _FakePoliciesClient()
    result = await pol.create_pilot_bounded_policy(
        policies_client=client, router_addresses=[ROUTER],
        operator_ack=True, idempotency_key="idem-1",
    )
    assert result == {"id": "policy-fake"}
    assert len(client.calls) == 1
    sent, idem = client.calls[0]
    assert idem == "idem-1"
    assert sent.model_dump() == pol.build_pilot_bounded_policy([ROUTER]).model_dump()


# ── dormancy / isolation guards ──────────────────────────────────────────────


def test_module_never_builds_a_cdp_client_itself():
    """No credential is ever read here: the only path to Coinbase is an
    explicitly injected client."""
    src = Path(pol.__file__).read_text(encoding="utf-8")
    assert "CdpClient(" not in src  # named in prose only, never instantiated
    assert "CDP_API_KEY" not in src
    assert "os.environ" not in src


def test_module_is_wired_to_nothing_in_production():
    """Still fully DORMANT: no production module imports this file (only its own
    test may). The real policy creation stays a separate, operator-authorized
    step."""
    src_root = Path(pol.__file__).parent
    importers = [
        path.name
        for path in src_root.rglob("*.py")
        if path.name != "agent_wallet_cdp_policy.py"
        and "agent_wallet_cdp_policy" in path.read_text(encoding="utf-8")
    ]
    assert importers == [], f"unexpected production importer(s): {importers}"


def test_pilot_and_adapter_are_left_untouched_by_this_module():
    """The pilot's real execution path must stay unaware of this definition --
    nothing here is wired into the live swap/transfer flow."""
    from aria_core import agent_wallet_cdp_adapter as adapter

    assert "agent_wallet_cdp_policy" not in Path(adapter.__file__).read_text(encoding="utf-8")
    assert "agent_wallet_cdp_policy" not in Path(pilot.__file__).read_text(encoding="utf-8")
