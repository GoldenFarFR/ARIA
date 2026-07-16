"""Adaptateur CDP -- vérifie le parsing défensif et la dégradation fail-closed,
jamais un vrai appel réseau (aucun identifiant CDP dans cette suite)."""
from __future__ import annotations

import sys
import types

import pytest

from aria_core import agent_wallet_cdp_adapter as adapter


def _install_fake_cdp_module(monkeypatch, *, balances_result, swap_result=None, raise_on="none"):
    """Injecte un faux module `cdp` dans sys.modules pour intercepter
    `from cdp import CdpClient` sans dépendre du vrai package installé."""

    class FakeAccount:
        address = "0xabc123"

        async def swap(self, options):
            if raise_on == "swap":
                raise RuntimeError("facilitator timeout")
            return swap_result

    class FakeEvm:
        async def get_or_create_account(self, name):
            if raise_on == "account":
                raise RuntimeError("CDP API down")
            return FakeAccount()

        async def list_token_balances(self, address, network):
            if raise_on == "balances":
                raise RuntimeError("CDP API down")
            return balances_result

    class FakeCdpClient:
        def __init__(self):
            self.evm = FakeEvm()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    fake_cdp = types.ModuleType("cdp")
    fake_cdp.CdpClient = FakeCdpClient
    fake_swap_module = types.ModuleType("cdp.actions.evm.swap")
    fake_swap_module.AccountSwapOptions = lambda **kwargs: kwargs
    fake_actions = types.ModuleType("cdp.actions")
    fake_evm_pkg = types.ModuleType("cdp.actions.evm")

    monkeypatch.setitem(sys.modules, "cdp", fake_cdp)
    monkeypatch.setitem(sys.modules, "cdp.actions", fake_actions)
    monkeypatch.setitem(sys.modules, "cdp.actions.evm", fake_evm_pkg)
    monkeypatch.setitem(sys.modules, "cdp.actions.evm.swap", fake_swap_module)


@pytest.mark.asyncio
async def test_balance_none_when_cdp_package_not_installed(monkeypatch):
    monkeypatch.delitem(sys.modules, "cdp", raising=False)
    monkeypatch.setattr(
        "builtins.__import__",
        _raise_import_error_for("cdp"),
    )
    result = await adapter.usdc_balance_usd()
    assert result is None


def _raise_import_error_for(blocked_name):
    real_import = __import__

    def fake_import(name, *args, **kwargs):
        if name == blocked_name:
            raise ImportError("no cdp-sdk installed")
        return real_import(name, *args, **kwargs)

    return fake_import


@pytest.mark.asyncio
async def test_balance_parses_dict_shaped_response(monkeypatch):
    _install_fake_cdp_module(
        monkeypatch,
        balances_result={
            "balances": [
                {
                    "token": {"contractAddress": adapter.USDC_BASE_ADDRESS},
                    "amount": {"amount": "5000000", "decimals": 6},
                }
            ]
        },
    )
    result = await adapter.usdc_balance_usd()
    assert result == 5.0


@pytest.mark.asyncio
async def test_balance_parses_object_shaped_response(monkeypatch):
    class Amount:
        amount = "12500000"
        decimals = 6

    class Token:
        contract_address = adapter.USDC_BASE_ADDRESS

    class Entry:
        token = Token()
        amount = Amount()

    class Balances:
        balances = [Entry()]

    _install_fake_cdp_module(monkeypatch, balances_result=Balances())
    result = await adapter.usdc_balance_usd()
    assert result == 12.5


@pytest.mark.asyncio
async def test_balance_zero_when_usdc_not_held(monkeypatch):
    _install_fake_cdp_module(
        monkeypatch,
        balances_result={
            "balances": [
                {"token": {"contractAddress": "0xdeadbeef"}, "amount": {"amount": "1", "decimals": 18}}
            ]
        },
    )
    result = await adapter.usdc_balance_usd()
    assert result == 0.0


@pytest.mark.asyncio
async def test_balance_none_when_account_lookup_fails(monkeypatch):
    _install_fake_cdp_module(monkeypatch, balances_result=None, raise_on="account")
    result = await adapter.usdc_balance_usd()
    assert result is None


@pytest.mark.asyncio
async def test_balance_none_when_balances_call_fails(monkeypatch):
    _install_fake_cdp_module(monkeypatch, balances_result=None, raise_on="balances")
    result = await adapter.usdc_balance_usd()
    assert result is None


@pytest.mark.asyncio
async def test_execute_swap_returns_tx_hash_and_amount_out(monkeypatch):
    _install_fake_cdp_module(
        monkeypatch,
        balances_result=None,
        swap_result={"transaction_hash": "0xdeadbeef", "to_amount": "0.0015"},
    )
    result = await adapter.execute_swap(
        chain="base", token_in="USDC", token_out="WETH", amount_in_usd=5.0,
        wallet_address="0xabc123", slippage_bps=1000,
    )
    assert result["tx_hash"] == "0xdeadbeef"
    assert result["amount_out"] == 0.0015


@pytest.mark.asyncio
async def test_execute_swap_propagates_exception_on_failure(monkeypatch):
    _install_fake_cdp_module(monkeypatch, balances_result=None, raise_on="swap")
    with pytest.raises(RuntimeError):
        await adapter.execute_swap(
            chain="base", token_in="USDC", token_out="WETH", amount_in_usd=5.0,
            wallet_address="0xabc123", slippage_bps=1000,
        )
