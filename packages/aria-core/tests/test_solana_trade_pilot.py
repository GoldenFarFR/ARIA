"""Bounded real-capital buys on Solana.

Every test here asserts a REFUSAL. That is the point: this module exists to
say no, and a guard nobody proved says no is decoration. The operator asked to
fund a wallet on 21/08; these run first, with the gate closed and no key.
"""

from __future__ import annotations

import asyncio

import pytest

from aria_core import solana_trade_pilot as pilot


def _run(coro):
    return asyncio.run(coro)


async def _balance(v=10.0):
    return v


async def _swap_ok(**kw):
    return {"tx_hash": "5xTestSignature", "entry_price": 0.000123}


async def _swap_boom(**kw):
    raise RuntimeError("rpc exploded")


def _enable(monkeypatch, *, paused=False, custody=False):
    monkeypatch.setenv("ARIA_SOLANA_TRADE_PILOT_ENABLED", "true")
    monkeypatch.setattr(pilot.outgoing_pause, "is_paused", lambda **kw: paused)
    monkeypatch.setattr(pilot.outgoing_pause, "blocked_notice", lambda w: f"{w} est bloque (/stop)")
    monkeypatch.setattr(pilot.custody_pause, "is_paused", lambda: custody)
    monkeypatch.setattr(pilot.custody_pause, "blocked_notice", lambda w: f"{w} est bloque (custody)")
    async def _noop(**kw):
        return None
    monkeypatch.setattr(pilot.agent_wallet_log, "record_transaction", _noop)


def test_the_gate_is_closed_by_default(monkeypatch):
    monkeypatch.delenv("ARIA_SOLANA_TRADE_PILOT_ENABLED", raising=False)
    assert pilot.solana_trade_pilot_enabled() is False


@pytest.mark.parametrize("value", ["", "false", "1", "yes", "TRUE ", "True"])
def test_only_an_explicit_true_opens_the_gate(monkeypatch, value):
    # "1"/"yes" must NOT open a real-money gate: a typo in .env cannot be the
    # difference between paper and real capital.
    monkeypatch.setenv("ARIA_SOLANA_TRADE_PILOT_ENABLED", value)
    assert pilot.solana_trade_pilot_enabled() is (value.strip().lower() == "true")


def test_a_closed_gate_blocks_everything(monkeypatch):
    monkeypatch.delenv("ARIA_SOLANA_TRADE_PILOT_ENABLED", raising=False)
    async def _noop(**kw):
        return None
    monkeypatch.setattr(pilot.agent_wallet_log, "record_transaction", _noop)
    r = _run(pilot.attempt_buy(mint="M", amount_in_usd=0.05,
                               balance_fn=_balance, swap_fn=_swap_ok))
    assert r.ok is False and "désactivé" in r.reason


def test_the_kill_switch_blocks(monkeypatch):
    _enable(monkeypatch, paused=True)
    r = _run(pilot.attempt_buy(mint="M", amount_in_usd=0.05,
                               balance_fn=_balance, swap_fn=_swap_ok))
    assert r.ok is False and "/stop" in r.reason


def test_the_custody_pause_blocks(monkeypatch):
    _enable(monkeypatch, custody=True)
    r = _run(pilot.attempt_buy(mint="M", amount_in_usd=0.05,
                               balance_fn=_balance, swap_fn=_swap_ok))
    assert r.ok is False and "custody" in r.reason


def test_above_the_hard_cap_is_refused(monkeypatch):
    _enable(monkeypatch)
    r = _run(pilot.attempt_buy(mint="M", amount_in_usd=pilot.MAX_TRADE_USD + 0.01,
                               balance_fn=_balance, swap_fn=_swap_ok))
    assert r.ok is False and "plafond dur" in r.reason


def test_below_the_floor_is_refused(monkeypatch):
    # Rent-exemption on a fresh token account makes a tiny trade a guaranteed
    # loss whatever the price does.
    _enable(monkeypatch)
    r = _run(pilot.attempt_buy(mint="M", amount_in_usd=0.001,
                               balance_fn=_balance, swap_fn=_swap_ok))
    assert r.ok is False and "plancher" in r.reason


def test_an_unreadable_balance_blocks_rather_than_assumes(monkeypatch):
    _enable(monkeypatch)
    async def _boom():
        raise RuntimeError("rpc down")
    r = _run(pilot.attempt_buy(mint="M", amount_in_usd=0.05,
                               balance_fn=_boom, swap_fn=_swap_ok))
    assert r.ok is False and "fail-closed" in r.reason


def test_a_none_balance_blocks_too(monkeypatch):
    _enable(monkeypatch)
    async def _none():
        return None
    r = _run(pilot.attempt_buy(mint="M", amount_in_usd=0.05,
                               balance_fn=_none, swap_fn=_swap_ok))
    assert r.ok is False and "fail-closed" in r.reason


def test_more_than_the_real_balance_is_refused(monkeypatch):
    _enable(monkeypatch)
    async def _poor():
        return 0.01
    r = _run(pilot.attempt_buy(mint="M", amount_in_usd=0.05,
                               balance_fn=_poor, swap_fn=_swap_ok))
    assert r.ok is False and "solde réel" in r.reason


def test_a_failing_swap_is_reported_not_raised(monkeypatch):
    _enable(monkeypatch)
    r = _run(pilot.attempt_buy(mint="M", amount_in_usd=0.05,
                               balance_fn=_balance, swap_fn=_swap_boom))
    assert r.ok is False and "swap échoué" in r.reason


def test_a_swap_without_a_hash_counts_as_failure(monkeypatch):
    # No signature means no proof it landed. Reporting success there would put
    # a phantom position in the ledger.
    _enable(monkeypatch)
    async def _no_hash(**kw):
        return {"entry_price": 0.1}
    r = _run(pilot.attempt_buy(mint="M", amount_in_usd=0.05,
                               balance_fn=_balance, swap_fn=_no_hash))
    assert r.ok is False and "sans hash" in r.reason


def test_the_nominal_case_goes_through(monkeypatch):
    _enable(monkeypatch)
    r = _run(pilot.attempt_buy(mint="M", amount_in_usd=0.05,
                               balance_fn=_balance, swap_fn=_swap_ok))
    assert r.ok is True
    assert r.tx_hash == "5xTestSignature"
    assert r.amount_in_usd == 0.05


def test_slippage_is_always_forced_and_never_a_parameter(monkeypatch):
    # The caller cannot widen it: attempt_buy takes no slippage argument at all.
    _enable(monkeypatch)
    seen = {}
    async def _capture(**kw):
        seen.update(kw)
        return {"tx_hash": "sig"}
    _run(pilot.attempt_buy(mint="M", amount_in_usd=0.05,
                           balance_fn=_balance, swap_fn=_capture))
    assert seen["slippage_bps"] == pilot.MAX_SLIPPAGE_BPS
    assert pilot.MAX_SLIPPAGE_BPS <= 1000  # absolute project rule: never above 10%
    import inspect
    assert "slippage" not in inspect.signature(pilot.attempt_buy).parameters


def test_no_caller_can_raise_the_cap():
    # There must be no parameter, anywhere, that lets a caller trade more.
    import inspect
    params = set(inspect.signature(pilot.attempt_buy).parameters)
    assert not {"cap", "max_usd", "force", "override", "dry_run"} & params
