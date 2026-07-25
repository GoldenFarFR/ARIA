"""Real CDP adapter for the smart-swing seams -- verifies each thin wrapper
calls the right cdp-sdk method with the right arguments and translates the
SDK's return shape into what agent_wallet_smart_swing.py's seams expect. Never
a live network call (no CDP credentials in this suite): the CDP client/SDK is
mocked via a fake `cdp` package injected into sys.modules, exactly like
test_agent_wallet_cdp_adapter.py. Every function has at least one failure-path
test."""
from __future__ import annotations

import sys
import types

import pytest

from aria_core import agent_wallet_smart_swing as sw
from aria_core import agent_wallet_smart_swing_cdp_adapter as adapter
from aria_core.agent_wallet_cdp_adapter import USDC_BASE_ADDRESS


def _usdc_entry(amount_atomic: str, *, address: str = USDC_BASE_ADDRESS, symbol: str = "USDC", decimals: int = 6):
    return {"token": {"contractAddress": address, "symbol": symbol}, "amount": {"amount": amount_atomic, "decimals": decimals}}


class _FakeQuote:
    """Stands in for cdp QuoteSwapResult / SwapUnavailableResult. `execute` must
    never be called by the read-only quote path or the quote-based swap path."""

    def __init__(self, *, to_amount, min_to_amount="0", liquidity_available=True):
        self.to_amount = to_amount
        self.min_to_amount = min_to_amount
        self.liquidity_available = liquidity_available
        self.execute_calls = 0

    async def execute(self, *args, **kwargs):
        self.execute_calls += 1
        raise AssertionError("quote.execute() must never be called by the adapter")


def _install_fake_cdp(
    monkeypatch,
    *,
    balances_by_address=None,
    default_balances=None,
    quote_result=None,
    swap_result=None,
    use_spend_result="0xpullhash",
    transfer_result="0xreturnhash",
    raise_on="none",
):
    """Inject a fake `cdp` package intercepting `from cdp import ...` without the
    real SDK. Returns a `calls` dict capturing every SDK invocation for
    assertions."""
    calls: dict = {
        "get_account": [], "list_token_balances": [], "create_swap_quote": [],
        "use_spend_permission": [], "swap": [], "transfer": [],
    }
    balances_by_address = balances_by_address or {}

    class FakeAccount:
        address = sw.SPENDER_ADDRESS

        async def use_spend_permission(self, spend_permission, value, network):
            calls["use_spend_permission"].append(
                {"spend_permission": spend_permission, "value": value, "network": network}
            )
            if raise_on == "use_spend_permission":
                raise RuntimeError("spend permission network unsupported")
            return use_spend_result

        async def swap(self, options):
            calls["swap"].append(options)
            if raise_on == "swap":
                raise RuntimeError("facilitator timeout")
            return swap_result

        async def transfer(self, to, amount, token, network):
            calls["transfer"].append(
                {"to": to, "amount": amount, "token": token, "network": network}
            )
            if raise_on == "transfer":
                raise RuntimeError("network unavailable")
            return transfer_result

    class FakeEvm:
        async def get_account(self, address=None, name=None):
            calls["get_account"].append({"address": address, "name": name})
            if raise_on == "get_account":
                raise RuntimeError("CDP account lookup failed")
            return FakeAccount()

        async def list_token_balances(self, address, network):
            calls["list_token_balances"].append({"address": address, "network": network})
            if raise_on == "list_token_balances":
                raise RuntimeError("CDP API down")
            entries = balances_by_address.get(address, default_balances)
            return {"balances": entries or []}

        async def create_swap_quote(self, **kwargs):
            calls["create_swap_quote"].append(kwargs)
            if raise_on == "create_swap_quote":
                raise RuntimeError("quote service down")
            return quote_result

    class FakeCdpClient:
        def __init__(self):
            self.evm = FakeEvm()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    fake_cdp = types.ModuleType("cdp")
    fake_cdp.CdpClient = FakeCdpClient
    fake_cdp.parse_units = lambda amount, decimals: int(float(amount) * (10 ** decimals))

    fake_swap_module = types.ModuleType("cdp.actions.evm.swap")
    fake_swap_module.AccountSwapOptions = lambda **kwargs: types.SimpleNamespace(**kwargs)
    fake_actions = types.ModuleType("cdp.actions")
    fake_evm_pkg = types.ModuleType("cdp.actions.evm")

    fake_spend_mod = types.ModuleType("cdp.spend_permissions")
    fake_spend_mod.SpendPermissionInput = lambda **kwargs: types.SimpleNamespace(**kwargs)

    monkeypatch.setitem(sys.modules, "cdp", fake_cdp)
    monkeypatch.setitem(sys.modules, "cdp.actions", fake_actions)
    monkeypatch.setitem(sys.modules, "cdp.actions.evm", fake_evm_pkg)
    monkeypatch.setitem(sys.modules, "cdp.actions.evm.swap", fake_swap_module)
    monkeypatch.setitem(sys.modules, "cdp.spend_permissions", fake_spend_mod)
    return calls


def _raise_import_error_for(blocked_name):
    real_import = __import__

    def fake_import(name, *args, **kwargs):
        if name == blocked_name:
            raise ImportError("no cdp-sdk installed")
        return real_import(name, *args, **kwargs)

    return fake_import


# ── balance reads ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_usdc_balance_reads_the_given_address(monkeypatch):
    calls = _install_fake_cdp(
        monkeypatch,
        balances_by_address={sw.SMART_ST_ADDRESS: [_usdc_entry("2500000")]},
    )
    result = await adapter.usdc_balance_usd(address=sw.SMART_ST_ADDRESS)
    assert result == 2.5
    assert calls["list_token_balances"][0]["address"] == sw.SMART_ST_ADDRESS


@pytest.mark.asyncio
async def test_usdc_balance_zero_when_not_held(monkeypatch):
    _install_fake_cdp(
        monkeypatch,
        default_balances=[_usdc_entry("1", address="0xdeadbeef", symbol="GEM", decimals=18)],
    )
    result = await adapter.usdc_balance_usd(address=sw.SPENDER_ADDRESS)
    assert result == 0.0


@pytest.mark.asyncio
async def test_usdc_balance_none_when_call_fails(monkeypatch):
    _install_fake_cdp(monkeypatch, raise_on="list_token_balances")
    assert await adapter.usdc_balance_usd(address=sw.SMART_ST_ADDRESS) is None


@pytest.mark.asyncio
async def test_usdc_balance_none_when_cdp_missing(monkeypatch):
    monkeypatch.delitem(sys.modules, "cdp", raising=False)
    monkeypatch.setattr("builtins.__import__", _raise_import_error_for("cdp"))
    assert await adapter.usdc_balance_usd(address=sw.SMART_ST_ADDRESS) is None


@pytest.mark.asyncio
async def test_token_balance_finds_the_token(monkeypatch):
    _install_fake_cdp(
        monkeypatch,
        balances_by_address={
            sw.SPENDER_ADDRESS: [
                _usdc_entry("5000000"),
                _usdc_entry("1500000000000000000", address="0xGEM", symbol="GEM", decimals=18),
            ]
        },
    )
    result = await adapter.token_balance(address=sw.SPENDER_ADDRESS, token_address="0xGEM")
    assert result == 1.5


@pytest.mark.asyncio
async def test_token_balance_zero_when_not_held(monkeypatch):
    _install_fake_cdp(monkeypatch, default_balances=[_usdc_entry("5000000")])
    result = await adapter.token_balance(address=sw.SPENDER_ADDRESS, token_address="0xNOTHELD")
    assert result == 0.0


@pytest.mark.asyncio
async def test_token_balance_none_when_call_fails(monkeypatch):
    _install_fake_cdp(monkeypatch, raise_on="list_token_balances")
    assert await adapter.token_balance(address=sw.SPENDER_ADDRESS, token_address="0xGEM") is None


# ── BalanceFn bindings target the RIGHT account ──────────────────────────────


@pytest.mark.asyncio
async def test_buy_balance_fn_reads_aria_smart_st_not_the_spender(monkeypatch):
    """The buy's affordability seam must read the pull SOURCE (aria-smart-st),
    never the spender (which holds ~0 USDC before a pull -- would block every
    buy). Matches run_swing_entry_canary's docstring."""
    calls = _install_fake_cdp(
        monkeypatch,
        balances_by_address={
            sw.SMART_ST_ADDRESS: [_usdc_entry("200000000")],  # 200 USDC in the pocket
            sw.SPENDER_ADDRESS: [],  # spender empty
        },
    )
    result = await adapter.smart_st_usdc_balance_fn()
    assert result == 200.0
    assert calls["list_token_balances"][0]["address"] == sw.SMART_ST_ADDRESS


@pytest.mark.asyncio
async def test_spender_usdc_balance_fn_reads_the_spender(monkeypatch):
    calls = _install_fake_cdp(
        monkeypatch, balances_by_address={sw.SPENDER_ADDRESS: [_usdc_entry("3000000")]}
    )
    result = await adapter.spender_usdc_balance_fn()
    assert result == 3.0
    assert calls["list_token_balances"][0]["address"] == sw.SPENDER_ADDRESS


@pytest.mark.asyncio
async def test_spender_token_balance_factory_binds_the_token_and_spender(monkeypatch):
    calls = _install_fake_cdp(
        monkeypatch,
        balances_by_address={
            sw.SPENDER_ADDRESS: [_usdc_entry("42000000000000000000", address="0xHELD", symbol="HELD", decimals=18)]
        },
    )
    fn = adapter.make_spender_token_balance_fn("0xHELD")
    result = await fn()
    assert result == 42.0
    assert calls["list_token_balances"][0]["address"] == sw.SPENDER_ADDRESS


# ── spend_pull_fn ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_spend_pull_calls_use_spend_permission_with_the_builder(monkeypatch):
    calls = _install_fake_cdp(monkeypatch, use_spend_result="0xPULL")
    result = await adapter.spend_pull_fn(value_atomic=3_000_000, network="base")
    assert result == {"tx_hash": "0xPULL"}
    call = calls["use_spend_permission"][0]
    assert call["value"] == 3_000_000
    assert call["network"] == "base"
    # spend_permission comes from build_spend_permission_input() -> aria-smart-st
    # is the account, the spender is the spender, token is USDC, cap is the
    # operator's 2500$ (atomic).
    perm = call["spend_permission"]
    assert perm.account == sw.SMART_ST_ADDRESS
    assert perm.spender == sw.SPENDER_ADDRESS
    assert perm.token == USDC_BASE_ADDRESS
    assert perm.allowance == sw.usd_to_atomic_usdc(sw.SPEND_PERMISSION_ALLOWANCE_USD)


@pytest.mark.asyncio
async def test_spend_pull_resolves_spender_by_address_never_by_name(monkeypatch):
    calls = _install_fake_cdp(monkeypatch)
    await adapter.spend_pull_fn(value_atomic=1_000_000)
    assert calls["get_account"][0] == {"address": sw.SPENDER_ADDRESS, "name": None}


@pytest.mark.asyncio
async def test_spend_pull_propagates_exception_on_failure(monkeypatch):
    """The seam contract expects a raise on failure (execute_smart_swing_swap
    marks it failed & NOT stranded -- nothing pulled), never a graceful shape."""
    _install_fake_cdp(monkeypatch, raise_on="use_spend_permission")
    with pytest.raises(RuntimeError):
        await adapter.spend_pull_fn(value_atomic=1_000_000)


# ── buy_swap_fn ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_buy_swap_quotes_then_executes_and_reports_amounts(monkeypatch):
    quote = _FakeQuote(to_amount="1500000000000000000")  # 1.5 tokens @ 18 decimals
    calls = _install_fake_cdp(
        monkeypatch, quote_result=quote, swap_result={"transaction_hash": "0xSWAP"}
    )
    result = await adapter.buy_swap_fn(
        network="base", token_in=USDC_BASE_ADDRESS, token_out="0xGEM",
        amount_in_usd=3.0, slippage_bps=sw.MAX_SLIPPAGE_BPS,
    )
    assert result["tx_hash"] == "0xSWAP"
    assert result["amount_out_atomic"] == 1_500_000_000_000_000_000
    assert result["amount_out"] == 1.5  # 18-decimal default for a bought token
    # from_amount = 3.0 USDC -> 3_000_000 atomic (6 decimals), passed as a string.
    q = calls["create_swap_quote"][0]
    assert q["from_amount"] == "3000000"
    assert q["taker"] == sw.SPENDER_ADDRESS
    assert q["slippage_bps"] == sw.MAX_SLIPPAGE_BPS
    # quote-based execution: the swap got the quote object, never re-quoted inline.
    assert calls["swap"][0].swap_quote is quote
    assert quote.execute_calls == 0


@pytest.mark.asyncio
async def test_buy_swap_forces_max_slippage_even_if_a_smaller_value_passed(monkeypatch):
    quote = _FakeQuote(to_amount="1000000000000000000")
    calls = _install_fake_cdp(monkeypatch, quote_result=quote, swap_result={"transaction_hash": "0x1"})
    await adapter.buy_swap_fn(
        network="base", token_in=USDC_BASE_ADDRESS, token_out="0xGEM",
        amount_in_usd=1.0, slippage_bps=50,  # ignored, forced to 10%
    )
    assert calls["create_swap_quote"][0]["slippage_bps"] == sw.MAX_SLIPPAGE_BPS


@pytest.mark.asyncio
async def test_buy_swap_raises_when_liquidity_unavailable(monkeypatch):
    unavailable = _FakeQuote(to_amount=None, liquidity_available=False)
    calls = _install_fake_cdp(monkeypatch, quote_result=unavailable, swap_result={"transaction_hash": "0x"})
    with pytest.raises(RuntimeError, match="liquidity"):
        await adapter.buy_swap_fn(
            network="base", token_in=USDC_BASE_ADDRESS, token_out="0xGEM", amount_in_usd=3.0,
        )
    # never executed the swap after an unavailable quote (no funds moved).
    assert calls["swap"] == []


@pytest.mark.asyncio
async def test_buy_swap_propagates_exception_when_swap_fails(monkeypatch):
    quote = _FakeQuote(to_amount="1000000000000000000")
    _install_fake_cdp(monkeypatch, quote_result=quote, raise_on="swap")
    with pytest.raises(RuntimeError):
        await adapter.buy_swap_fn(
            network="base", token_in=USDC_BASE_ADDRESS, token_out="0xGEM", amount_in_usd=3.0,
        )


# ── sell_swap_fn ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sell_swap_uses_exact_atomic_and_reports_exact_usdc(monkeypatch):
    quote = _FakeQuote(to_amount="2960000")  # 2.96 USDC out @ 6 decimals
    calls = _install_fake_cdp(
        monkeypatch, quote_result=quote, swap_result={"transaction_hash": "0xSELL"}
    )
    result = await adapter.sell_swap_fn(
        network="base", token_in="0xGEM", token_out=USDC_BASE_ADDRESS,
        amount_in_tokens=1.5, amount_in_tokens_atomic=1_500_000_000_000_000_000,
        slippage_bps=sw.MAX_SLIPPAGE_BPS,
    )
    assert result["tx_hash"] == "0xSELL"
    assert result["amount_out_atomic"] == 2_960_000
    assert result["amount_out"] == 2.96  # USDC = 6 decimals -> exact human USD
    # from_amount is the exact atomic quantity threaded from the buy.
    assert calls["create_swap_quote"][0]["from_amount"] == "1500000000000000000"
    assert calls["swap"][0].swap_quote is quote


@pytest.mark.asyncio
async def test_sell_swap_raises_without_exact_atomic(monkeypatch):
    """The adapter cannot infer the sold token's decimals -- fail-closed. The
    seam contract handles this as a failed, NOT-stranded sell."""
    calls = _install_fake_cdp(monkeypatch, quote_result=_FakeQuote(to_amount="1"))
    with pytest.raises(ValueError, match="amount_in_tokens_atomic"):
        await adapter.sell_swap_fn(
            network="base", token_in="0xGEM", token_out=USDC_BASE_ADDRESS,
            amount_in_tokens=1.5, amount_in_tokens_atomic=None,
        )
    assert calls["create_swap_quote"] == []  # never touched the network


@pytest.mark.asyncio
async def test_sell_swap_raises_when_liquidity_unavailable(monkeypatch):
    unavailable = _FakeQuote(to_amount=None, liquidity_available=False)
    _install_fake_cdp(monkeypatch, quote_result=unavailable)
    with pytest.raises(RuntimeError, match="liquidity"):
        await adapter.sell_swap_fn(
            network="base", token_in="0xGEM", token_out=USDC_BASE_ADDRESS,
            amount_in_tokens=1.5, amount_in_tokens_atomic=1_500_000_000_000_000_000,
        )


# ── return_transfer_fn (the safety-critical destination pin) ─────────────────


@pytest.mark.asyncio
async def test_return_transfer_sends_to_aria_smart_st_with_exact_atomic(monkeypatch):
    calls = _install_fake_cdp(monkeypatch, transfer_result="0xRET")
    result = await adapter.return_transfer_fn(
        to_address=sw.SMART_ST_ADDRESS, token_address=USDC_BASE_ADDRESS,
        amount_out=2.96, amount_out_atomic=2_960_000, network="base",
    )
    assert result == {"tx_hash": "0xRET"}
    call = calls["transfer"][0]
    assert call["to"] == sw.SMART_ST_ADDRESS
    assert call["amount"] == 2_960_000
    assert call["token"] == USDC_BASE_ADDRESS
    assert call["network"] == "base"


@pytest.mark.asyncio
async def test_return_transfer_derives_atomic_from_human_when_atomic_absent(monkeypatch):
    calls = _install_fake_cdp(monkeypatch)
    await adapter.return_transfer_fn(
        to_address=sw.SMART_ST_ADDRESS, token_address=USDC_BASE_ADDRESS,
        amount_out=1.5, amount_out_atomic=None,
    )
    assert calls["transfer"][0]["amount"] == 1_500_000  # 1.5 USDC @ 6 decimals


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_to",
    [
        sw.SMART_VC_ADDRESS,  # the OTHER smart account -- must never receive
        sw.SPENDER_ADDRESS,
        "0x584b2B35dac347B2317da0d21b95063de51257Ef",  # the EOA pilot's allowlisted addr
        "0x0000000000000000000000000000000000000000",
        "",
    ],
)
async def test_return_transfer_refuses_any_other_destination(monkeypatch, bad_to):
    """Defense-in-depth: the ONLY place the spender's output may go is
    aria-smart-st. Any other to_address raises and NEVER calls transfer --
    structurally impossible to redirect."""
    calls = _install_fake_cdp(monkeypatch)
    with pytest.raises(ValueError, match="aria-smart-st"):
        await adapter.return_transfer_fn(
            to_address=bad_to, token_address=USDC_BASE_ADDRESS,
            amount_out=2.96, amount_out_atomic=2_960_000,
        )
    assert calls["transfer"] == []  # no transfer attempted at all


@pytest.mark.asyncio
async def test_return_transfer_rejects_non_positive_amount(monkeypatch):
    calls = _install_fake_cdp(monkeypatch)
    with pytest.raises(ValueError, match="positive"):
        await adapter.return_transfer_fn(
            to_address=sw.SMART_ST_ADDRESS, token_address=USDC_BASE_ADDRESS,
            amount_out=0.0, amount_out_atomic=0,
        )
    assert calls["transfer"] == []


@pytest.mark.asyncio
async def test_return_transfer_propagates_exception_on_failure(monkeypatch):
    """A failed return after a successful sell -> the seam contract marks the
    USDC stranded (raise expected, not a graceful shape)."""
    _install_fake_cdp(monkeypatch, raise_on="transfer")
    with pytest.raises(RuntimeError):
        await adapter.return_transfer_fn(
            to_address=sw.SMART_ST_ADDRESS, token_address=USDC_BASE_ADDRESS,
            amount_out=2.96, amount_out_atomic=2_960_000,
        )


# ── quote_fn (read-only precheck) ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_quote_fn_returns_the_raw_quote_and_pins_the_spender_taker(monkeypatch):
    quote = _FakeQuote(to_amount="1000000000000000000", min_to_amount="900000000000000000")
    calls = _install_fake_cdp(monkeypatch, quote_result=quote)
    result = await adapter.quote_fn(
        from_token=USDC_BASE_ADDRESS, to_token="0xGEM",
        from_amount_atomic=3_000_000, network="base", slippage_bps=sw.MAX_SLIPPAGE_BPS,
    )
    assert result is quote  # returned raw for the orchestration to read
    q = calls["create_swap_quote"][0]
    assert q["from_amount"] == "3000000"
    assert q["taker"] == sw.SPENDER_ADDRESS
    assert q["slippage_bps"] == sw.MAX_SLIPPAGE_BPS
    assert quote.execute_calls == 0  # purely read-only, never executes


@pytest.mark.asyncio
async def test_quote_fn_returns_unavailable_result_unchanged(monkeypatch):
    """The orchestration's _fetch_quote inspects liquidity_available itself --
    quote_fn just returns whatever create_swap_quote returned, including a
    SwapUnavailableResult."""
    unavailable = _FakeQuote(to_amount=None, liquidity_available=False)
    _install_fake_cdp(monkeypatch, quote_result=unavailable)
    result = await adapter.quote_fn(
        from_token=USDC_BASE_ADDRESS, to_token="0xGEM", from_amount_atomic=3_000_000,
    )
    assert result is unavailable


@pytest.mark.asyncio
async def test_quote_fn_propagates_exception(monkeypatch):
    """_fetch_quote wraps quote_fn in try/except and rejects fail-closed, so a
    raise here is the expected failure signal."""
    _install_fake_cdp(monkeypatch, raise_on="create_swap_quote")
    with pytest.raises(RuntimeError):
        await adapter.quote_fn(
            from_token=USDC_BASE_ADDRESS, to_token="0xGEM", from_amount_atomic=3_000_000,
        )


# ── dormancy / isolation guards ──────────────────────────────────────────────


def test_adapter_never_references_the_vc_pocket_or_pilot_wallet():
    """The spender adapter must only ever touch the SPENDER + aria-smart-st.
    Guard against a copy-paste drift that pulls in aria-smart-vc (no delegation
    allowed) or the EOA pilot's name-based wallet."""
    from pathlib import Path

    src = Path(adapter.__file__).read_text(encoding="utf-8")
    assert "SMART_VC_ADDRESS" not in src
    assert sw.SMART_VC_ADDRESS not in src
    assert "WALLET_NAME" not in src  # never the pilot's rename-fragile name lookup


def test_adapter_is_wired_to_nothing_in_production():
    """Still fully DORMANT: no production module imports this adapter (only its
    own test file may). Mirrors the 'grep to confirm no accidental wiring' check."""
    from pathlib import Path

    src_root = Path(adapter.__file__).parent
    importers = []
    for path in src_root.rglob("*.py"):
        if path.name == "agent_wallet_smart_swing_cdp_adapter.py":
            continue
        if "agent_wallet_smart_swing_cdp_adapter" in path.read_text(encoding="utf-8"):
            importers.append(path.name)
    assert importers == [], f"unexpected production importer(s): {importers}"
