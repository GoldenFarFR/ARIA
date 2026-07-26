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


# ── _patch_cdp_swap_fee_validation (26/07 -- real `URANUS` incident, 19/07) ──
# 5 real swap attempts all failed with the same Pydantic error on the REAL
# installed cdp-sdk (CommonSwapResponseFees.gasFee rejecting None) -- these
# tests exercise the REAL cdp package (never the fake module above, which
# replaces cdp.openapi_client entirely and would make these submodules
# unimportable) to prove the fix genuinely works against the actual SDK.

@pytest.fixture(autouse=True)
def _reset_cdp_patch_guard(monkeypatch):
    """The patch guard is a module-level flag -- reset it around every test in
    this file so an earlier test's patch application never silently skips a
    later test's own check."""
    monkeypatch.setattr(adapter, "_cdp_swap_fee_validation_patched", False)
    yield


def test_patch_relaxes_gas_fee_validation_on_the_real_sdk():
    """Must run BEFORE any other test in this file calls the real (unmocked)
    ``_patch_cdp_swap_fee_validation()`` -- unlike the module-level flag reset
    by the fixture above, mutating ``CommonSwapResponseFees.model_fields`` is a
    real, permanent change to the imported class for the rest of the process
    (Pydantic model rebuilds aren't undoable by monkeypatch). This is the only
    test asserting the PRE-patch failure mode, so it must be the first to
    touch the real class -- kept in one self-contained test rather than split
    across two, precisely to avoid that ordering trap."""
    from cdp.openapi_client.models.common_swap_response_fees import CommonSwapResponseFees

    with pytest.raises(Exception):  # noqa: PT011 -- real pydantic.ValidationError, unpatched
        CommonSwapResponseFees.from_dict({"gasFee": None, "protocolFee": None})

    adapter._patch_cdp_swap_fee_validation()

    result = CommonSwapResponseFees.from_dict({"gasFee": None, "protocolFee": None})
    assert result.gas_fee is None
    assert result.protocol_fee is None


def test_patch_still_parses_a_real_valid_fee_after_patching():
    """The patch only relaxes null -- a genuinely present fee must still parse
    correctly (never silently dropped)."""
    from cdp.openapi_client.models.common_swap_response_fees import CommonSwapResponseFees

    adapter._patch_cdp_swap_fee_validation()

    result = CommonSwapResponseFees.from_dict({
        "gasFee": {"amount": "100", "token": adapter.USDC_BASE_ADDRESS},
        "protocolFee": None,
    })
    assert result.gas_fee is not None
    assert result.gas_fee.amount == "100"
    assert result.protocol_fee is None


def test_patch_is_idempotent():
    adapter._patch_cdp_swap_fee_validation()
    adapter._patch_cdp_swap_fee_validation()  # must not raise on a 2nd call
    assert adapter._cdp_swap_fee_validation_patched is True


def test_patch_degrades_softly_on_unexpected_sdk_shape(monkeypatch):
    """If the installed SDK ever changes shape (renamed field, model_rebuild
    signature change...), the patch must degrade softly rather than raise up
    into execute_swap -- never blocks a real swap attempt just because this
    one narrow workaround itself broke. Forced by monkeypatching model_fields
    to a dict missing the expected keys (deterministic regardless of Python's
    own sys.modules import cache, unlike faking the parent package -- once
    the real submodule has been imported anywhere in the process, as the test
    above already does, re-faking its parent package no longer blocks a fresh
    import of an already-cached submodule)."""
    from cdp.openapi_client.models.common_swap_response_fees import CommonSwapResponseFees

    monkeypatch.setattr(CommonSwapResponseFees, "model_fields", {})
    adapter._patch_cdp_swap_fee_validation()  # must not raise
    assert adapter._cdp_swap_fee_validation_patched is False


@pytest.mark.asyncio
async def test_execute_swap_calls_the_patch_before_swapping(monkeypatch):
    called = {"value": False}

    def fake_patch():
        called["value"] = True

    monkeypatch.setattr(adapter, "_patch_cdp_swap_fee_validation", fake_patch)
    _install_fake_cdp_module(
        monkeypatch,
        balances_result=None,
        swap_result={"transaction_hash": "0xdeadbeef", "to_amount": "0.0015"},
    )

    await adapter.execute_swap(
        chain="base", token_in="USDC", token_out="WETH", amount_in_usd=5.0,
        wallet_address="0xabc123", slippage_bps=1000,
    )

    assert called["value"] is True


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
