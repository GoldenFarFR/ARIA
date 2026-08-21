"""Automatic sweep of excess capital to cold storage.

Operator decision 21/08: above 500$, the surplus leaves for his Tangem in
batches of at least 5$. Like its sibling pilot, this module exists to refuse --
so every refusal is asserted here, starting with the one that matters most:
an unset destination must block, never default to somewhere else.
"""

from __future__ import annotations

import asyncio

import pytest

from aria_core import solana_cold_sweep as sweep

COLD = "TangemColdWalletAddressForTests1111111111111"


def _run(coro):
    return asyncio.run(coro)


async def _transfer_ok(**kw):
    return {"tx_hash": "5xSweepSignature"}


def _enable(monkeypatch, *, balance=600.0, paused=False, custody=False, dest=COLD):
    monkeypatch.setenv("ARIA_SOLANA_COLD_SWEEP_ENABLED", "true")
    monkeypatch.setattr(sweep, "COLD_WALLET_ADDRESS", dest)
    monkeypatch.setattr(sweep.outgoing_pause, "is_paused", lambda **kw: paused)
    monkeypatch.setattr(sweep.outgoing_pause, "blocked_notice", lambda w: f"{w} bloque (/stop)")
    monkeypatch.setattr(sweep.custody_pause, "is_paused", lambda: custody)
    monkeypatch.setattr(sweep.custody_pause, "blocked_notice", lambda w: f"{w} bloque (custody)")
    async def _noop(**kw):
        return None
    monkeypatch.setattr(sweep.agent_wallet_log, "record_transaction", _noop)
    async def _bal():
        return balance
    return _bal


def test_the_gate_is_closed_by_default(monkeypatch):
    monkeypatch.delenv("ARIA_SOLANA_COLD_SWEEP_ENABLED", raising=False)
    assert sweep.cold_sweep_enabled() is False


def test_an_empty_destination_blocks_rather_than_defaults(monkeypatch):
    # THE critical one: an unset destination must never mean "skip the check".
    bal = _enable(monkeypatch, dest="")
    r = _run(sweep.attempt_sweep(balance_fn=bal, transfer_fn=_transfer_ok))
    assert r.ok is False and "non renseignée" in r.reason


def test_the_kill_switch_blocks(monkeypatch):
    bal = _enable(monkeypatch, paused=True)
    r = _run(sweep.attempt_sweep(balance_fn=bal, transfer_fn=_transfer_ok))
    assert r.ok is False and "/stop" in r.reason


def test_the_custody_pause_blocks(monkeypatch):
    bal = _enable(monkeypatch, custody=True)
    r = _run(sweep.attempt_sweep(balance_fn=bal, transfer_fn=_transfer_ok))
    assert r.ok is False and "custody" in r.reason


def test_an_unreadable_balance_blocks(monkeypatch):
    _enable(monkeypatch)
    async def _boom():
        raise RuntimeError("rpc down")
    r = _run(sweep.attempt_sweep(balance_fn=_boom, transfer_fn=_transfer_ok))
    assert r.ok is False and "fail-closed" in r.reason


@pytest.mark.parametrize("balance", [0.0, 100.0, 499.99, 500.0])
def test_below_the_threshold_nothing_moves(monkeypatch, balance):
    bal = _enable(monkeypatch, balance=balance)
    r = _run(sweep.attempt_sweep(balance_fn=bal, transfer_fn=_transfer_ok))
    assert r.ok is False and "sous le seuil" in r.reason


@pytest.mark.parametrize("balance", [500.5, 502.0, 504.99])
def test_a_surplus_below_the_batch_floor_waits(monkeypatch, balance):
    # Operator's point: without this, every dollar over the line would trigger
    # its own transfer and fees would eat the sweep.
    bal = _enable(monkeypatch, balance=balance)
    r = _run(sweep.attempt_sweep(balance_fn=bal, transfer_fn=_transfer_ok))
    assert r.ok is False and "lot minimum" in r.reason


def test_the_working_float_always_stays_behind(monkeypatch):
    seen = {}
    async def _capture(**kw):
        seen.update(kw)
        return {"tx_hash": "sig"}
    bal = _enable(monkeypatch, balance=1200.0)
    r = _run(sweep.attempt_sweep(balance_fn=bal, transfer_fn=_capture))
    assert r.ok is True
    assert seen["amount_usd"] == pytest.approx(700.0)  # 1200 - 500, never the whole balance
    assert seen["to_address"] == COLD


def test_the_destination_is_never_a_parameter():
    # No caller can redirect the funds: attempt_sweep takes no destination.
    import inspect
    params = set(inspect.signature(sweep.attempt_sweep).parameters)
    assert not {"to_address", "destination", "address", "override"} & params


def test_a_failing_transfer_is_reported_not_raised(monkeypatch):
    bal = _enable(monkeypatch)
    async def _boom(**kw):
        raise RuntimeError("network")
    r = _run(sweep.attempt_sweep(balance_fn=bal, transfer_fn=_boom))
    assert r.ok is False and "transfert échoué" in r.reason


def test_a_transfer_without_a_hash_counts_as_failure(monkeypatch):
    bal = _enable(monkeypatch)
    async def _no_hash(**kw):
        return {}
    r = _run(sweep.attempt_sweep(balance_fn=bal, transfer_fn=_no_hash))
    assert r.ok is False and "sans hash" in r.reason


def test_the_nominal_sweep_goes_through(monkeypatch):
    bal = _enable(monkeypatch, balance=600.0)
    r = _run(sweep.attempt_sweep(balance_fn=bal, transfer_fn=_transfer_ok))
    assert r.ok is True and r.amount_usd == pytest.approx(100.0)
    assert r.tx_hash == "5xSweepSignature"
