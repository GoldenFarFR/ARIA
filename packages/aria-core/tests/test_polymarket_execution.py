"""Tests de l'adaptateur d'execution reelle Polymarket (08/03) -- aucun appel
reseau reel, SDK `py_clob_client_v2` fake injecte via sys.modules (meme
patron que test_agent_wallet_cdp_adapter.py pour `cdp`)."""
from __future__ import annotations

import sys
import types

import pytest


class FakeSignedOrder:
    def __init__(self, order_args):
        self.order_args = order_args


class FakeClobClient:
    last_instance = None

    def __init__(self, *, host, chain_id, key):
        self.host = host
        self.chain_id = chain_id
        self.key = key
        self.posted = []
        FakeClobClient.last_instance = self

    def create_order(self, order_args):
        return FakeSignedOrder(order_args)

    def post_order(self, signed_order):
        self.posted.append(signed_order)
        return {"orderID": "fake-order-id", "status": "matched"}


@pytest.fixture(autouse=True)
def _fake_sdk(monkeypatch):
    fake_client_module = types.ModuleType("py_clob_client_v2.client")
    fake_client_module.ClobClient = FakeClobClient

    class OrderArgsV2:
        def __init__(self, *, token_id, price, size, side):
            self.token_id = token_id
            self.price = price
            self.size = size
            self.side = side

    fake_types_module = types.ModuleType("py_clob_client_v2.clob_types")
    fake_types_module.OrderArgsV2 = OrderArgsV2

    fake_pkg = types.ModuleType("py_clob_client_v2")
    fake_pkg.client = fake_client_module
    fake_pkg.clob_types = fake_types_module

    monkeypatch.setitem(sys.modules, "py_clob_client_v2", fake_pkg)
    monkeypatch.setitem(sys.modules, "py_clob_client_v2.client", fake_client_module)
    monkeypatch.setitem(sys.modules, "py_clob_client_v2.clob_types", fake_types_module)
    yield
    for mod in ("py_clob_client_v2", "py_clob_client_v2.client", "py_clob_client_v2.clob_types"):
        sys.modules.pop(mod, None)


async def _ample_balance():
    return 1_000_000.0


async def _never_called_balance():
    raise AssertionError("balance_fn must not be called -- an earlier guardrail should have blocked first")


@pytest.mark.asyncio
async def test_build_signed_order_never_posts(monkeypatch):
    from aria_core.services import polymarket_execution as pe

    result = await pe.build_signed_order(
        token_id="tok-123", side="BUY", price=0.42, size=100.0, private_key="0xfakekey",
    )

    assert result.token_id == "tok-123"
    assert result.side == "BUY"
    assert FakeClobClient.last_instance.posted == []  # never posted


@pytest.mark.asyncio
async def test_post_signed_order_refused_when_gate_disabled(monkeypatch):
    from aria_core.services import polymarket_execution as pe

    monkeypatch.delenv("ARIA_POLYMARKET_REAL_TRADING_ENABLED", raising=False)
    order = await pe.build_signed_order(
        token_id="tok-123", side="BUY", price=0.42, size=10.0, private_key="0xfakekey",
    )

    result = await pe.post_signed_order(order, private_key="0xfakekey", balance_fn=_never_called_balance)

    assert result == {"status": "blocked", "reason": "gate_disabled"}


@pytest.mark.asyncio
async def test_post_signed_order_refused_by_kill_switch_even_with_gate_on(monkeypatch):
    from aria_core.services import polymarket_execution as pe
    from aria_core import outgoing_pause

    monkeypatch.setenv("ARIA_POLYMARKET_REAL_TRADING_ENABLED", "true")
    monkeypatch.setattr(outgoing_pause, "is_paused", lambda strict=True: True)
    order = await pe.build_signed_order(
        token_id="tok-123", side="BUY", price=0.42, size=10.0, private_key="0xfakekey",
    )

    result = await pe.post_signed_order(order, private_key="0xfakekey", balance_fn=_never_called_balance)

    assert result == {"status": "blocked", "reason": "kill_switch_active"}


@pytest.mark.asyncio
async def test_post_signed_order_refused_by_custody_pause_even_with_gate_on(monkeypatch):
    """08/03 security-review finding: custody_pause (auto-armed) must be
    checked alongside, never instead of, outgoing_pause (manual /stop) --
    an anomaly-triggered freeze elsewhere must still stop this wallet."""
    from aria_core.services import polymarket_execution as pe
    from aria_core import custody_pause, outgoing_pause

    monkeypatch.setenv("ARIA_POLYMARKET_REAL_TRADING_ENABLED", "true")
    monkeypatch.setattr(outgoing_pause, "is_paused", lambda strict=True: False)
    monkeypatch.setattr(custody_pause, "is_paused", lambda: True)
    order = await pe.build_signed_order(
        token_id="tok-123", side="BUY", price=0.42, size=10.0, private_key="0xfakekey",
    )

    result = await pe.post_signed_order(order, private_key="0xfakekey", balance_fn=_never_called_balance)

    assert result == {"status": "blocked", "reason": "custody_pause_active"}
    assert FakeClobClient.last_instance.posted == []


@pytest.mark.asyncio
async def test_post_signed_order_refused_over_hard_cap(monkeypatch):
    from aria_core.services import polymarket_execution as pe
    from aria_core import custody_pause, outgoing_pause

    monkeypatch.setenv("ARIA_POLYMARKET_REAL_TRADING_ENABLED", "true")
    monkeypatch.setattr(outgoing_pause, "is_paused", lambda strict=True: False)
    monkeypatch.setattr(custody_pause, "is_paused", lambda: False)
    order = await pe.build_signed_order(
        token_id="tok-123", side="BUY", price=0.9, size=100.0, private_key="0xfakekey",  # notional=90$
    )

    result = await pe.post_signed_order(order, private_key="0xfakekey", balance_fn=_never_called_balance)

    assert result == {"status": "blocked", "reason": "over_hard_cap"}
    assert FakeClobClient.last_instance.posted == []


@pytest.mark.asyncio
async def test_post_signed_order_refused_over_real_balance(monkeypatch):
    """system_issues #125b (18/08) -- closes the 2nd of the module's own
    documented 'known gaps' (08/03): a notional under MAX_BET_USD but over the
    wallet's REAL balance must still be refused, same doctrine as
    agent_wallet_pilot.attempt_swap."""
    from aria_core.services import polymarket_execution as pe
    from aria_core import custody_pause, outgoing_pause

    monkeypatch.setenv("ARIA_POLYMARKET_REAL_TRADING_ENABLED", "true")
    monkeypatch.setattr(outgoing_pause, "is_paused", lambda strict=True: False)
    monkeypatch.setattr(custody_pause, "is_paused", lambda: False)
    order = await pe.build_signed_order(
        token_id="tok-123", side="BUY", price=0.5, size=10.0, private_key="0xfakekey",  # notional=5$
    )

    async def _thin_balance():
        return 2.0  # sous le notional (5$), au-dessus du hard cap (10$) n'entre pas en jeu

    result = await pe.post_signed_order(order, private_key="0xfakekey", balance_fn=_thin_balance)

    assert result == {"status": "blocked", "reason": "insufficient_balance"}
    assert FakeClobClient.last_instance.posted == []


@pytest.mark.asyncio
async def test_post_signed_order_fails_closed_when_balance_unavailable(monkeypatch):
    """Either an exception OR a None returned by balance_fn must block --
    never assume a sufficient balance for lack of a better answer."""
    from aria_core.services import polymarket_execution as pe
    from aria_core import custody_pause, outgoing_pause

    monkeypatch.setenv("ARIA_POLYMARKET_REAL_TRADING_ENABLED", "true")
    monkeypatch.setattr(outgoing_pause, "is_paused", lambda strict=True: False)
    monkeypatch.setattr(custody_pause, "is_paused", lambda: False)
    order = await pe.build_signed_order(
        token_id="tok-123", side="BUY", price=0.5, size=10.0, private_key="0xfakekey",
    )

    async def _raising_balance():
        raise RuntimeError("RPC indisponible")

    result = await pe.post_signed_order(order, private_key="0xfakekey", balance_fn=_raising_balance)
    assert result == {"status": "blocked", "reason": "balance_unavailable"}
    assert FakeClobClient.last_instance.posted == []

    async def _none_balance():
        return None

    result = await pe.post_signed_order(order, private_key="0xfakekey", balance_fn=_none_balance)
    assert result == {"status": "blocked", "reason": "balance_unavailable"}


@pytest.mark.asyncio
async def test_post_signed_order_succeeds_when_all_gates_pass(monkeypatch):
    from aria_core.services import polymarket_execution as pe
    from aria_core import custody_pause, outgoing_pause

    monkeypatch.setenv("ARIA_POLYMARKET_REAL_TRADING_ENABLED", "true")
    monkeypatch.setattr(outgoing_pause, "is_paused", lambda strict=True: False)
    monkeypatch.setattr(custody_pause, "is_paused", lambda: False)
    order = await pe.build_signed_order(
        token_id="tok-123", side="BUY", price=0.5, size=10.0, private_key="0xfakekey",  # notional=5$
    )

    result = await pe.post_signed_order(order, private_key="0xfakekey", balance_fn=_ample_balance)

    assert result["status"] == "posted"
    assert result["result"]["orderID"] == "fake-order-id"


@pytest.mark.asyncio
async def test_post_signed_order_reraises_on_post_error_without_swallowing(monkeypatch, caplog):
    """08/03 security-review finding: an exception after the order may
    already have reached the exchange must never vanish silently -- it must
    propagate (never caught/return a fake 'blocked' result) and be logged
    CRITICAL."""
    import logging

    from aria_core.services import polymarket_execution as pe
    from aria_core import custody_pause, outgoing_pause

    monkeypatch.setenv("ARIA_POLYMARKET_REAL_TRADING_ENABLED", "true")
    monkeypatch.setattr(outgoing_pause, "is_paused", lambda strict=True: False)
    monkeypatch.setattr(custody_pause, "is_paused", lambda: False)
    order = await pe.build_signed_order(
        token_id="tok-123", side="BUY", price=0.5, size=10.0, private_key="0xfakekey",
    )

    def _raise(_self, _signed_order):
        raise RuntimeError("exchange timeout")

    monkeypatch.setattr(FakeClobClient, "post_order", _raise)

    with caplog.at_level(logging.CRITICAL):
        with pytest.raises(RuntimeError, match="exchange timeout"):
            await pe.post_signed_order(order, private_key="0xfakekey", balance_fn=_ample_balance)

    assert any("STATUS UNKNOWN" in rec.message for rec in caplog.records)


@pytest.mark.asyncio
async def test_private_key_never_appears_in_any_log_line(monkeypatch, caplog):
    """08/03 security-review finding: the 'never logged' guarantee was only
    enforced by manual code review -- this locks it as a real regression
    test."""
    import logging

    from aria_core.services import polymarket_execution as pe
    from aria_core import custody_pause, outgoing_pause

    monkeypatch.setenv("ARIA_POLYMARKET_REAL_TRADING_ENABLED", "true")
    monkeypatch.setattr(outgoing_pause, "is_paused", lambda strict=True: False)
    monkeypatch.setattr(custody_pause, "is_paused", lambda: False)
    secret_key = "0xTHIS_MUST_NEVER_APPEAR_IN_LOGS_abcdef123456"

    with caplog.at_level(logging.DEBUG):
        order = await pe.build_signed_order(
            token_id="tok-123", side="BUY", price=0.5, size=10.0, private_key=secret_key,
        )
        await pe.post_signed_order(order, private_key=secret_key, balance_fn=_ample_balance)

    for record in caplog.records:
        assert secret_key not in record.getMessage()


def test_polymarket_real_trading_enabled_defaults_false(monkeypatch):
    from aria_core.services import polymarket_execution as pe

    monkeypatch.delenv("ARIA_POLYMARKET_REAL_TRADING_ENABLED", raising=False)

    assert pe.polymarket_real_trading_enabled() is False


def test_polymarket_real_trading_enabled_true_variants(monkeypatch):
    from aria_core.services import polymarket_execution as pe

    for value in ("1", "true", "TRUE", "yes", "on"):
        monkeypatch.setenv("ARIA_POLYMARKET_REAL_TRADING_ENABLED", value)
        assert pe.polymarket_real_trading_enabled() is True


def test_require_sdk_raises_clear_error_without_the_optional_extra(monkeypatch):
    """The fake SDK injected by the autouse fixture is removed here so this
    test exercises the REAL import path -- the real `py_clob_client_v2`
    package is not installed in this test environment, so this reproduces
    the genuine "extra not installed" case."""
    import sys as _sys

    from aria_core.services import polymarket_execution as pe

    for mod in ("py_clob_client_v2", "py_clob_client_v2.client", "py_clob_client_v2.clob_types"):
        monkeypatch.delitem(_sys.modules, mod, raising=False)

    with pytest.raises(RuntimeError, match="polymarket_execution"):
        pe._require_sdk()
