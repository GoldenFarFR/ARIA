"""Regression test for a second real 07/24 production gap found while wiring
up the batch-of-10 losing-trade review: `trade_devils_advocate_cycle` had its
`_sync_x_curiosity_enabled()` gate fixed earlier the same day (see
test_heartbeat_gate_wiring_regression.py) but `_run_task()` never had a
matching dispatch branch -- the task could be correctly toggled `enabled=True`
and still be a complete no-op every tick (falls through the whole elif chain,
no `else`, silently does nothing). Confirms `_run_task` actually calls the
underlying cycle for both `trade_devils_advocate_cycle` (now fixed) and the
new `trade_loss_batch_review_cycle` (built same day), and that both notify via
`_notify_telegram_trading` (same paper-trading topic as every other
trading-related alert) rather than the generic `_notify_telegram`."""
from __future__ import annotations

import pytest

from aria_core import heartbeat


def _task(task_id: str) -> heartbeat.HeartbeatTask:
    match = [t for t in heartbeat.HEARTBEAT_TASKS if t.id == task_id]
    assert match, f"tâche introuvable : {task_id}"
    return match[0]


# ── gate wiring (trade_loss_batch_review_cycle) ─────────────────────────────────

def test_trade_loss_batch_review_cycle_respects_its_env_var(monkeypatch):
    monkeypatch.delenv("ARIA_TRADE_LOSS_BATCH_REVIEW_ENABLED", raising=False)
    heartbeat._sync_x_curiosity_enabled()
    assert _task("trade_loss_batch_review_cycle").enabled is False

    monkeypatch.setenv("ARIA_TRADE_LOSS_BATCH_REVIEW_ENABLED", "1")
    heartbeat._sync_x_curiosity_enabled()
    assert _task("trade_loss_batch_review_cycle").enabled is True

    monkeypatch.delenv("ARIA_TRADE_LOSS_BATCH_REVIEW_ENABLED", raising=False)
    heartbeat._sync_x_curiosity_enabled()
    assert _task("trade_loss_batch_review_cycle").enabled is False


# ── _run_task actually dispatches (the real gap) ────────────────────────────────

@pytest.mark.asyncio
async def test_run_task_actually_calls_devils_advocate_cycle(monkeypatch):
    called = {"n": 0}

    async def fake_cycle():
        called["n"] += 1
        return {"outcome": "ok", "checked": 0, "reviewed": 0, "flawed": 0, "results": []}

    monkeypatch.setattr(
        "aria_core.skills.trade_devils_advocate.run_trade_devils_advocate_cycle", fake_cycle
    )
    await heartbeat.aria_heartbeat._run_task("trade_devils_advocate_cycle")
    assert called["n"] == 1


@pytest.mark.asyncio
async def test_run_task_actually_calls_loss_batch_review_cycle(monkeypatch):
    called = {"n": 0}

    async def fake_cycle():
        called["n"] += 1
        return {"outcome": "ok", "batches_reviewed": 0, "patterns_found": 0, "still_pending": 0, "results": []}

    monkeypatch.setattr(
        "aria_core.skills.trade_loss_batch_review.run_trade_loss_batch_review_cycle", fake_cycle
    )
    await heartbeat.aria_heartbeat._run_task("trade_loss_batch_review_cycle")
    assert called["n"] == 1


# ── notification behavior ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_devils_advocate_flawed_verdict_notifies_trading_topic(monkeypatch):
    async def fake_cycle():
        return {
            "outcome": "ok", "checked": 1, "reviewed": 1, "flawed": 1,
            "results": [{"position_id": 1, "contract": "0xabc", "verdict": "flawed",
                         "flaw": "score fondamental ignoré", "lesson": "vérifier le score avant d'acheter"}],
        }

    monkeypatch.setattr(
        "aria_core.skills.trade_devils_advocate.run_trade_devils_advocate_cycle", fake_cycle
    )
    captured = {}

    async def fake_notify(text):
        captured["text"] = text

    monkeypatch.setattr(heartbeat.aria_heartbeat, "_notify_telegram_trading", fake_notify)
    await heartbeat.aria_heartbeat._run_task("trade_devils_advocate_cycle")
    assert "vérifier le score avant d'acheter" in captured["text"]


@pytest.mark.asyncio
async def test_devils_advocate_sound_verdict_never_notifies(monkeypatch):
    async def fake_cycle():
        return {
            "outcome": "ok", "checked": 1, "reviewed": 1, "flawed": 0,
            "results": [{"position_id": 1, "contract": "0xabc", "verdict": "sound", "flaw": "", "lesson": ""}],
        }

    monkeypatch.setattr(
        "aria_core.skills.trade_devils_advocate.run_trade_devils_advocate_cycle", fake_cycle
    )
    called = {"n": 0}

    async def fake_notify(text):
        called["n"] += 1

    monkeypatch.setattr(heartbeat.aria_heartbeat, "_notify_telegram_trading", fake_notify)
    await heartbeat.aria_heartbeat._run_task("trade_devils_advocate_cycle")
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_loss_batch_review_notifies_trading_topic_for_every_reviewed_batch(monkeypatch):
    """Suivi explicite demandé par l'opérateur : même un lot SANS pattern doit
    remonter (la visibilité elle-même est le but, pas seulement les patterns
    positifs)."""
    async def fake_cycle():
        return {
            "outcome": "ok", "batches_reviewed": 1, "patterns_found": 0, "still_pending": 0,
            "results": [{"batch_number": 1, "pattern_found": False, "pattern_summary": "", "adjustment": ""}],
        }

    monkeypatch.setattr(
        "aria_core.skills.trade_loss_batch_review.run_trade_loss_batch_review_cycle", fake_cycle
    )
    captured = {}

    async def fake_notify(text):
        captured["text"] = text

    monkeypatch.setattr(heartbeat.aria_heartbeat, "_notify_telegram_trading", fake_notify)
    await heartbeat.aria_heartbeat._run_task("trade_loss_batch_review_cycle")
    assert "lot n°1" in captured["text"]
    assert "Aucun dénominateur commun" in captured["text"]


@pytest.mark.asyncio
async def test_loss_batch_review_batch_error_never_notifies(monkeypatch):
    async def fake_cycle():
        return {
            "outcome": "ok", "batches_reviewed": 1, "patterns_found": 0, "still_pending": 0,
            "results": [{"batch_number": 1, "error": "panne réseau"}],
        }

    monkeypatch.setattr(
        "aria_core.skills.trade_loss_batch_review.run_trade_loss_batch_review_cycle", fake_cycle
    )
    called = {"n": 0}

    async def fake_notify(text):
        called["n"] += 1

    monkeypatch.setattr(heartbeat.aria_heartbeat, "_notify_telegram_trading", fake_notify)
    await heartbeat.aria_heartbeat._run_task("trade_loss_batch_review_cycle")
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_loss_batch_review_accumulating_outcome_never_notifies(monkeypatch):
    async def fake_cycle():
        return {"outcome": "accumulating", "pending": 3, "needed": 7}

    monkeypatch.setattr(
        "aria_core.skills.trade_loss_batch_review.run_trade_loss_batch_review_cycle", fake_cycle
    )
    called = {"n": 0}

    async def fake_notify(text):
        called["n"] += 1

    monkeypatch.setattr(heartbeat.aria_heartbeat, "_notify_telegram_trading", fake_notify)
    await heartbeat.aria_heartbeat._run_task("trade_loss_batch_review_cycle")
    assert called["n"] == 0
