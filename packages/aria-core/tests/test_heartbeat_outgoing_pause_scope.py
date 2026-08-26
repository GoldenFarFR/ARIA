"""26/08 fix -- `outgoing_pause` (the real-capital kill-switch, "/stop") used
to gate the WHOLE `_tick()` with a single early return, silently starving
every research/sourcing task too (vc_crawl, market_sentiment_cycle,
cabalspy_candidate_sourcing_cycle...). Found live: armed at 2026-08-25T14:04,
those cycles stopped ticking at that exact instant for 17h+, with zero
relation to a real trade. Same bug class already fixed in
`shadow_persistent.py` on 24/08, never carried over here."""
from __future__ import annotations

import asyncio

import pytest

from aria_core import heartbeat, outgoing_pause


def _task(task_id: str) -> heartbeat.HeartbeatTask:
    match = [t for t in heartbeat.HEARTBEAT_TASKS if t.id == task_id]
    assert match, f"tâche introuvable : {task_id}"
    return match[0]


def test_financial_risk_set_is_exactly_the_expected_three():
    """Verrouille la liste -- toute extension doit être une décision
    explicite, jamais une dérive silencieuse."""
    assert heartbeat._FINANCIAL_RISK_TASK_IDS == frozenset({
        "agent_wallet_pilot_cycle",
        "sepolia_autonomous_cycle",
        "robinhood_testnet_rehearsal_cycle",
    })


def test_income_and_shadow_tasks_are_never_in_the_financial_risk_set():
    """acp_provider_poll/acp_market_scan/revenue_autonomy process INCOME
    (x402 sales, market scans), never an outgoing spend -- and every
    paper/shadow pocket moves zero real capital. None of them should ever
    be paused by the real-capital kill-switch."""
    never_gated = {
        "acp_provider_poll", "acp_market_scan", "acp_email_watch", "revenue_autonomy",
        "paper_trade_cycle", "polymarket_paper_cycle", "vc_crawl",
        "momentum_discovery_cycle", "cabalspy_candidate_sourcing_cycle",
        "market_sentiment_cycle",
    }
    assert not (never_gated & heartbeat._FINANCIAL_RISK_TASK_IDS)


@pytest.mark.asyncio
async def test_tick_still_runs_research_tasks_while_paused(monkeypatch, tmp_path):
    """The exact regression: while /stop is armed, a non-financial task
    (vc_crawl) must still be attempted -- the bug made this return before
    even entering the task loop."""
    monkeypatch.setattr(outgoing_pause, "is_paused", lambda *a, **kw: True)
    monkeypatch.setattr(heartbeat, "_task_due", lambda *a, **kw: True)
    monkeypatch.setattr(heartbeat, "_save_heartbeat_state", lambda *a, **kw: None)
    monkeypatch.setattr(heartbeat, "_load_heartbeat_state", lambda: {})

    attempted: list[str] = []

    async def _fake_run_task(self, task_id):
        attempted.append(task_id)

    monkeypatch.setattr(heartbeat.AriaHeartbeat, "_run_task", _fake_run_task)

    hb = heartbeat.AriaHeartbeat()
    await hb._tick()

    assert "vc_crawl" in attempted, "a research task must still run while /stop is armed"
    assert "agent_wallet_pilot_cycle" not in attempted, "the real-capital task must stay paused"


@pytest.mark.asyncio
async def test_tick_still_gates_financial_risk_tasks_while_paused(monkeypatch):
    """The other half of the fix -- narrowing the gate must not accidentally
    remove it for the tasks that actually need it."""
    monkeypatch.setattr(outgoing_pause, "is_paused", lambda *a, **kw: True)
    monkeypatch.setattr(heartbeat, "_task_due", lambda *a, **kw: True)
    monkeypatch.setattr(heartbeat, "_save_heartbeat_state", lambda *a, **kw: None)
    monkeypatch.setattr(heartbeat, "_load_heartbeat_state", lambda: {})

    attempted: list[str] = []

    async def _fake_run_task(self, task_id):
        attempted.append(task_id)

    monkeypatch.setattr(heartbeat.AriaHeartbeat, "_run_task", _fake_run_task)

    hb = heartbeat.AriaHeartbeat()
    await hb._tick()

    for gated_id in heartbeat._FINANCIAL_RISK_TASK_IDS:
        assert gated_id not in attempted, f"{gated_id} must stay paused while /stop is armed"


def test_the_gate_condition_itself_is_lifted_once_unpaused(monkeypatch):
    """Sanity check, isolated from `enabled`'s own separate env-var gate
    (re-synced by `_sync_x_curiosity_enabled()` on every real tick, out of
    scope here): the narrowed gate does not become a permanent one -- once
    /stop is lifted, the exact condition added by this fix no longer skips
    a financial-risk task."""
    monkeypatch.setattr(outgoing_pause, "is_paused", lambda *a, **kw: False)
    for gated_id in heartbeat._FINANCIAL_RISK_TASK_IDS:
        assert not (gated_id in heartbeat._FINANCIAL_RISK_TASK_IDS and outgoing_pause.is_paused())
