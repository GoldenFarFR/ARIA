"""Regression test for a real 24/08 production crash: the very first live
tick of `robinhood_testnet_rehearsal_cycle` (right after
`ARIA_ROBINHOOD_TESTNET_REHEARSAL_ENABLED` was turned on for the first time)
raised `UnboundLocalError: cannot access local variable 'agent_wallet_pilot_
cycle' where it is not associated with a value` -- a copy-paste leftover from
the `agent_wallet_pilot_cycle` block above it in `_run_task`'s dispatch,
referencing a module never imported in this branch. Fixed by dropping the
stray `agent_wallet_pilot_cycle.format_agent_wallet_swap_alert(...)` call
(no Robinhood-side equivalent exists, and `append_memory` already logs the
outcome); this test exercises the exact "failed"/"blocked" path that crashed
live, so a future copy-paste of this kind fails a test again instead of
crashing the first real tick."""
from __future__ import annotations

import pytest

from aria_core import heartbeat


@pytest.mark.asyncio
async def test_robinhood_testnet_rehearsal_cycle_failed_outcome_does_not_raise(monkeypatch):
    async def fake_cycle():
        return {"outcome": "failed", "reason": "synthetic test failure"}

    monkeypatch.setattr(
        "aria_core.onchain.robinhood_pilot_cycle.run_robinhood_testnet_rehearsal_cycle",
        fake_cycle,
    )

    await heartbeat.aria_heartbeat._run_task("robinhood_testnet_rehearsal_cycle")


@pytest.mark.asyncio
async def test_robinhood_testnet_rehearsal_cycle_blocked_outcome_does_not_raise(monkeypatch):
    async def fake_cycle():
        return {"outcome": "blocked", "reason": "kill-switch active"}

    monkeypatch.setattr(
        "aria_core.onchain.robinhood_pilot_cycle.run_robinhood_testnet_rehearsal_cycle",
        fake_cycle,
    )

    await heartbeat.aria_heartbeat._run_task("robinhood_testnet_rehearsal_cycle")


@pytest.mark.asyncio
async def test_robinhood_testnet_rehearsal_cycle_ok_outcome_does_not_raise(monkeypatch):
    async def fake_cycle():
        return {"outcome": "ok"}

    monkeypatch.setattr(
        "aria_core.onchain.robinhood_pilot_cycle.run_robinhood_testnet_rehearsal_cycle",
        fake_cycle,
    )

    await heartbeat.aria_heartbeat._run_task("robinhood_testnet_rehearsal_cycle")
