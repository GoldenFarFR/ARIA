"""Item #212 (29/07) -- goplus_watchlist_cycle double-gate wiring: its own
dedicated ARIA_GOPLUS_WATCHLIST_ENABLED AND at least one real consumer of its
verdicts, so it never burns GoPlus quota for nobody.

02/09 -- the consumer set grew: specs/017's live_signal_observer evaluates
candidates while paper-trading is PAUSED, and every candidate stays stuck on
`honeypot_pending` until this cycle clears it. Paper-trading is no longer the
only consumer, so it can no longer be the only key."""
from __future__ import annotations

from aria_core import heartbeat, paper_pause


def _task(task_id: str) -> heartbeat.HeartbeatTask:
    match = [t for t in heartbeat.HEARTBEAT_TASKS if t.id == task_id]
    assert match, f"tâche introuvable : {task_id}"
    return match[0]


def test_disabled_when_both_gates_off(monkeypatch):
    monkeypatch.delenv("ARIA_PAPER_TRADING_ENABLED", raising=False)
    monkeypatch.delenv("ARIA_GOPLUS_WATCHLIST_ENABLED", raising=False)
    heartbeat._sync_x_curiosity_enabled()
    assert _task("goplus_watchlist_cycle").enabled is False


def test_disabled_when_only_paper_trading_on(monkeypatch):
    monkeypatch.setenv("ARIA_PAPER_TRADING_ENABLED", "1")
    monkeypatch.delenv("ARIA_GOPLUS_WATCHLIST_ENABLED", raising=False)
    heartbeat._sync_x_curiosity_enabled()
    assert _task("goplus_watchlist_cycle").enabled is False


def test_disabled_when_only_watchlist_gate_on(monkeypatch):
    monkeypatch.delenv("ARIA_PAPER_TRADING_ENABLED", raising=False)
    monkeypatch.setenv("ARIA_GOPLUS_WATCHLIST_ENABLED", "1")
    heartbeat._sync_x_curiosity_enabled()
    assert _task("goplus_watchlist_cycle").enabled is False


def test_enabled_when_both_gates_on(monkeypatch):
    monkeypatch.setenv("ARIA_PAPER_TRADING_ENABLED", "1")
    monkeypatch.setenv("ARIA_GOPLUS_WATCHLIST_ENABLED", "1")
    heartbeat._sync_x_curiosity_enabled()
    assert _task("goplus_watchlist_cycle").enabled is True

    monkeypatch.delenv("ARIA_GOPLUS_WATCHLIST_ENABLED", raising=False)
    heartbeat._sync_x_curiosity_enabled()
    assert _task("goplus_watchlist_cycle").enabled is False


# ---------------------------------------------------------------------------
# 02/09 -- live_signal_observer is a consumer too (specs/017)
# ---------------------------------------------------------------------------

def test_enabled_for_the_live_signal_observer_while_paper_trading_is_paused(monkeypatch):
    """The exact production situation on 02/09: /offpaper armed since 24/08,
    live signal observer running. Before this fix the cycle was off, the last
    honeypot check dated back 16 days, and no candidate could ever leave
    `honeypot_pending` -- so the live signal could never produce one complete
    evaluation."""
    monkeypatch.setenv("ARIA_PAPER_TRADING_ENABLED", "1")
    monkeypatch.setattr(paper_pause, "is_paused", lambda: True)
    monkeypatch.setenv("ARIA_GOPLUS_WATCHLIST_ENABLED", "1")
    monkeypatch.setenv("ARIA_LIVE_SIGNAL_OBSERVER_ENABLED", "1")
    heartbeat._sync_x_curiosity_enabled()
    assert _task("goplus_watchlist_cycle").enabled is True


def test_still_disabled_when_paper_paused_and_no_live_observer(monkeypatch):
    """No consumer at all -> still off. The fix widens the consumer set, it
    does not turn the cycle into an always-on quota drain."""
    monkeypatch.setenv("ARIA_PAPER_TRADING_ENABLED", "1")
    monkeypatch.setattr(paper_pause, "is_paused", lambda: True)
    monkeypatch.setenv("ARIA_GOPLUS_WATCHLIST_ENABLED", "1")
    monkeypatch.delenv("ARIA_LIVE_SIGNAL_OBSERVER_ENABLED", raising=False)
    heartbeat._sync_x_curiosity_enabled()
    assert _task("goplus_watchlist_cycle").enabled is False


def test_dedicated_gate_still_mandatory_even_with_the_live_observer(monkeypatch):
    monkeypatch.setattr(paper_pause, "is_paused", lambda: True)
    monkeypatch.delenv("ARIA_GOPLUS_WATCHLIST_ENABLED", raising=False)
    monkeypatch.setenv("ARIA_LIVE_SIGNAL_OBSERVER_ENABLED", "1")
    heartbeat._sync_x_curiosity_enabled()
    assert _task("goplus_watchlist_cycle").enabled is False
