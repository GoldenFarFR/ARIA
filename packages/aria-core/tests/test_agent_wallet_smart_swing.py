"""Autonomous swing-pocket execution via a delegated spender (Smart Account
migration, Model B, 07/23). These cover the PURE safety-envelope builders
(constants + Spend Permission input) -- the Policy + live execution wiring are
a later, hardware-validated step (see docs/HANDOFF_COINBASE_CDP.md). Nothing
here touches the network or executes any real spend."""
from __future__ import annotations

import pytest

from aria_core import agent_wallet_smart_swing as sw


# ── constants / identities ───────────────────────────────────────────────────


def test_addresses_match_the_deployed_monitor():
    """Guard against a copy-paste drift from the deployed
    agent_wallet_monitor.MONITORED_WALLETS -- these must stay in lockstep
    (real capital: a wrong address would grant a spend permission on the wrong
    account)."""
    from aria_core.agent_wallet_monitor import MONITORED_WALLETS

    assert sw.SMART_ST_ADDRESS == MONITORED_WALLETS["aria-smart-st-EVM"]
    assert sw.SMART_VC_ADDRESS == MONITORED_WALLETS["aria-smart-vc-EVM"]


def test_spend_permission_manager_address_matches_the_sdk():
    from cdp.spend_permissions import SPEND_PERMISSION_MANAGER_ADDRESS

    assert sw.SPEND_PERMISSION_MANAGER_ADDRESS == SPEND_PERMISSION_MANAGER_ADDRESS


def test_gate_off_by_default(monkeypatch):
    monkeypatch.delenv(sw._SMART_SWING_GATE, raising=False)
    assert sw.smart_swing_enabled() is False


def test_gate_on_when_env_set(monkeypatch):
    monkeypatch.setenv(sw._SMART_SWING_GATE, "true")
    assert sw.smart_swing_enabled() is True


# ── usd_to_atomic_usdc ───────────────────────────────────────────────────────


@pytest.mark.parametrize("usd,atomic", [
    (50.0, 50_000_000), (1.0, 1_000_000), (0.5, 500_000), (15.0, 15_000_000),
])
def test_usd_to_atomic_usdc(usd, atomic):
    assert sw.usd_to_atomic_usdc(usd) == atomic


# ── build_spend_permission_input ─────────────────────────────────────────────


def test_default_spend_permission_encodes_the_operator_cap():
    """The operator's explicit 07/23 decision: $50/week, auto-renewing."""
    sp = sw.build_spend_permission_input()
    d = sp.model_dump()
    assert d["allowance"] == 50_000_000  # $50 in USDC atomic units
    assert d["period_in_days"] == 7
    assert d["account"] == sw.SMART_ST_ADDRESS  # pulls FROM the swing pocket
    assert d["spender"] == sw.SPENDER_ADDRESS   # ...to the dedicated spender
    from aria_core.agent_wallet_cdp_adapter import USDC_BASE_ADDRESS

    assert d["token"] == USDC_BASE_ADDRESS


def test_spend_permission_never_grants_on_the_vc_pocket():
    """aria-smart-vc must NEVER get a delegated spend permission (every VC
    action requires the Tangem owner) -- the builder always targets the swing
    pocket, structurally."""
    sp = sw.build_spend_permission_input()
    assert sp.model_dump()["account"] != sw.SMART_VC_ADDRESS


def test_custom_allowance_within_range():
    sp = sw.build_spend_permission_input(allowance_usd=100.0, period_days=14)
    d = sp.model_dump()
    assert d["allowance"] == 100_000_000
    assert d["period_in_days"] == 14


@pytest.mark.parametrize("bad_allowance", [0, -1.0, -50.0])
def test_rejects_non_positive_allowance(bad_allowance):
    with pytest.raises(ValueError):
        sw.build_spend_permission_input(allowance_usd=bad_allowance)


def test_rejects_allowance_above_sane_ceiling():
    """The core safety invariant: an 'unlimited'/absurd allowance can never be
    produced here (it would silently remove safety layer #2)."""
    with pytest.raises(ValueError):
        sw.build_spend_permission_input(allowance_usd=sw._MAX_SANE_ALLOWANCE_USD + 0.01)


def test_accepts_allowance_exactly_at_ceiling():
    sp = sw.build_spend_permission_input(allowance_usd=sw._MAX_SANE_ALLOWANCE_USD)
    assert sp.model_dump()["allowance"] == sw.usd_to_atomic_usdc(sw._MAX_SANE_ALLOWANCE_USD)


@pytest.mark.parametrize("bad_period", [0, -1, -7])
def test_rejects_non_positive_period(bad_period):
    with pytest.raises(ValueError):
        sw.build_spend_permission_input(period_days=bad_period)
