"""Adaptateur CDP -- vérifie le parsing défensif et la dégradation fail-closed,
jamais un vrai appel réseau (aucun identifiant CDP dans cette suite)."""
from __future__ import annotations

import sys
import types

import pytest

from aria_core import agent_wallet_cdp_adapter as adapter


class _FakeApiError(Exception):
    """Stands in for cdp.openapi_client.errors.ApiError -- only the
    ``http_code`` attribute _get_wallet_account actually reads."""

    def __init__(self, http_code):
        super().__init__(f"fake ApiError http_code={http_code}")
        self.http_code = http_code


def _install_fake_cdp_module(
    monkeypatch, *, balances_result, swap_result=None, transfer_result=None, raise_on="none",
):
    """Injecte un faux module `cdp` dans sys.modules pour intercepter
    `from cdp import CdpClient` sans dépendre du vrai package installé."""

    class FakeAccount:
        address = "0xabc123"

        async def swap(self, options):
            if raise_on == "swap":
                raise RuntimeError("facilitator timeout")
            return swap_result

        async def transfer(self, **kwargs):
            if raise_on == "transfer":
                raise RuntimeError("réseau indisponible")
            return transfer_result

    class FakeEvm:
        async def get_account(self, name):
            if raise_on == "account":
                raise RuntimeError("CDP API down")
            if raise_on == "account_not_found":
                raise _FakeApiError(http_code=404)
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
    fake_cdp.parse_units = lambda amount, decimals: int(float(amount) * (10**decimals))
    fake_swap_module = types.ModuleType("cdp.actions.evm.swap")
    fake_swap_module.AccountSwapOptions = lambda **kwargs: kwargs
    fake_actions = types.ModuleType("cdp.actions")
    fake_evm_pkg = types.ModuleType("cdp.actions.evm")
    fake_openapi_client = types.ModuleType("cdp.openapi_client")
    fake_errors_module = types.ModuleType("cdp.openapi_client.errors")
    fake_errors_module.ApiError = _FakeApiError

    monkeypatch.setitem(sys.modules, "cdp", fake_cdp)
    monkeypatch.setitem(sys.modules, "cdp.actions", fake_actions)
    monkeypatch.setitem(sys.modules, "cdp.actions.evm", fake_evm_pkg)
    monkeypatch.setitem(sys.modules, "cdp.actions.evm.swap", fake_swap_module)
    monkeypatch.setitem(sys.modules, "cdp.openapi_client", fake_openapi_client)
    monkeypatch.setitem(sys.modules, "cdp.openapi_client.errors", fake_errors_module)


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
async def test_get_wallet_account_never_auto_creates_on_missing_name(monkeypatch):
    """The 21/07 and 23/07 incidents: a WALLET_NAME that no longer resolves on
    CDP must never silently create a fresh empty wallet -- fail closed instead."""
    _install_fake_cdp_module(monkeypatch, balances_result=None, raise_on="account_not_found")
    result = await adapter.usdc_balance_usd()
    assert result is None  # degrades the same as any other account-lookup failure


@pytest.mark.asyncio
async def test_get_wallet_account_raises_runtime_error_directly(monkeypatch):
    """Direct unit test of the guard itself (not just its degrade-to-None effect
    through usdc_balance_usd), so the fail-closed exception type is pinned."""
    _install_fake_cdp_module(monkeypatch, balances_result=None, raise_on="account_not_found")
    from cdp import CdpClient

    async with CdpClient() as cdp:
        with pytest.raises(RuntimeError, match="not found"):
            await adapter._get_wallet_account(cdp)


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
async def test_execute_swap_converts_amount_to_atomic_units(monkeypatch):
    """Bug réel corrigé le 17/07 : from_amount attend des unités atomiques
    ("smallest units", confirmé dans le SDK installé), pas un montant en dollars
    passé tel quel -- aurait fait échouer/mal-interpréter chaque swap réel."""
    _install_fake_cdp_module(
        monkeypatch,
        balances_result=None,
        swap_result={"transaction_hash": "0xdeadbeef", "to_amount": "0.0015"},
    )
    captured = {}
    real_options = sys.modules["cdp.actions.evm.swap"].AccountSwapOptions

    def spy_options(**kwargs):
        captured.update(kwargs)
        return real_options(**kwargs)

    monkeypatch.setattr(sys.modules["cdp.actions.evm.swap"], "AccountSwapOptions", spy_options)

    await adapter.execute_swap(
        chain="base", token_in="USDC", token_out="WETH", amount_in_usd=5.0,
        wallet_address="0xabc123", slippage_bps=1000,
    )

    # 5.0 USDC (6 décimales) -> 5 000 000 unités atomiques, jamais "5.0" tel quel.
    assert captured["from_amount"] == 5_000_000


@pytest.mark.asyncio
async def test_execute_swap_propagates_exception_on_failure(monkeypatch):
    _install_fake_cdp_module(monkeypatch, balances_result=None, raise_on="swap")
    with pytest.raises(RuntimeError):
        await adapter.execute_swap(
            chain="base", token_in="USDC", token_out="WETH", amount_in_usd=5.0,
            wallet_address="0xabc123", slippage_bps=1000,
        )


@pytest.mark.asyncio
async def test_list_all_token_balances_returns_every_token(monkeypatch):
    _install_fake_cdp_module(
        monkeypatch,
        balances_result={
            "balances": [
                {
                    "token": {"contractAddress": adapter.USDC_BASE_ADDRESS, "symbol": "USDC"},
                    "amount": {"amount": "5000000", "decimals": 6},
                },
                {
                    "token": {"contractAddress": "0xdeadbeef", "symbol": "SOMEGEM"},
                    "amount": {"amount": "1500000000000000000", "decimals": 18},
                },
            ]
        },
    )
    result = await adapter.list_all_token_balances()
    assert result == [
        {"address": adapter.USDC_BASE_ADDRESS, "symbol": "USDC", "amount": 5.0},
        {"address": "0xdeadbeef", "symbol": "SOMEGEM", "amount": 1.5},
    ]


@pytest.mark.asyncio
async def test_list_all_token_balances_none_when_cdp_missing(monkeypatch):
    monkeypatch.delitem(sys.modules, "cdp", raising=False)
    monkeypatch.setattr("builtins.__import__", _raise_import_error_for("cdp"))
    result = await adapter.list_all_token_balances()
    assert result is None


@pytest.mark.asyncio
async def test_list_all_token_balances_empty_list_when_wallet_empty(monkeypatch):
    _install_fake_cdp_module(monkeypatch, balances_result={"balances": []})
    result = await adapter.list_all_token_balances()
    assert result == []


@pytest.mark.asyncio
async def test_transfer_usdc_returns_tx_hash(monkeypatch):
    _install_fake_cdp_module(
        monkeypatch, balances_result=None, transfer_result="0xt2ansferhash",
    )
    result = await adapter.transfer_usdc(
        chain="base", to_address="0x33783cCb570Cb279C25F836806B5c4C3C8309777", amount_usd=1.0,
    )
    assert result["tx_hash"] == "0xt2ansferhash"


@pytest.mark.asyncio
async def test_transfer_usdc_propagates_exception_on_failure(monkeypatch):
    _install_fake_cdp_module(monkeypatch, balances_result=None, raise_on="transfer")
    with pytest.raises(RuntimeError):
        await adapter.transfer_usdc(
            chain="base", to_address="0x33783cCb570Cb279C25F836806B5c4C3C8309777", amount_usd=1.0,
        )
