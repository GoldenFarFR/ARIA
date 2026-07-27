"""27/07 -- 3-pocket architecture plan, Phase 4: the weekly training protocol
(run_weekly_reset) now covers scalping in addition to swing (both are trading
pockets on a short/medium horizon), but VC deliberately never resets (judged
on a rolling history instead, per the plan's own decision). Regression
coverage for the exact wallets the heartbeat loops over."""
from __future__ import annotations

import pytest

from aria_core import heartbeat


@pytest.mark.asyncio
async def test_paper_weekly_review_cycle_resets_swing_and_scalping_never_vc(monkeypatch):
    due_calls: list[str] = []
    reset_calls: list[str] = []

    async def fake_due(wallet: str = "swing") -> bool:
        due_calls.append(wallet)
        return True

    def _fake_report(wallet: str) -> dict:
        return {
            "cycle_number": 1, "validated": True, "return_pct": 10.0,
            "next_cycle_number": 2, "target_equity": 1_100_000.0,
            "start_capital": 1_000_000.0, "end_equity": 1_100_000.0,
            "closed_trades": 0, "win_rate": None, "force_closed": 0,
            "satellite_added_this_cycle": [], "satellite_open_positions": 0,
            "satellite_reserved_usd": 0.0, "satellite_rejected_no_room": 0,
        }

    async def fake_reset(*, price_lookup=None, wallet: str = "swing") -> dict:
        reset_calls.append(wallet)
        return _fake_report(wallet)

    notified: list[str] = []

    async def fake_notify(text: str) -> None:
        notified.append(text)

    monkeypatch.setattr("aria_core.paper_trader.weekly_cycle_due", fake_due)
    monkeypatch.setattr("aria_core.paper_trader.run_weekly_reset", fake_reset)
    monkeypatch.setattr(heartbeat.aria_heartbeat, "_notify_telegram_trading", fake_notify)

    await heartbeat.aria_heartbeat._run_task("paper_weekly_review_cycle")

    assert due_calls == ["swing", "scalping"]
    assert reset_calls == ["swing", "scalping"]
    assert "vc" not in due_calls
    assert "vc" not in reset_calls
    assert len(notified) == 2


@pytest.mark.asyncio
async def test_paper_weekly_review_cycle_skips_wallet_not_due(monkeypatch):
    due_calls: list[str] = []
    reset_calls: list[str] = []

    async def fake_due(wallet: str = "swing") -> bool:
        due_calls.append(wallet)
        return wallet == "swing"  # only swing is due this tick

    async def fake_reset(*, price_lookup=None, wallet: str = "swing") -> dict:
        reset_calls.append(wallet)
        return {
            "cycle_number": 1, "validated": True, "return_pct": 10.0,
            "next_cycle_number": 2, "target_equity": 1_100_000.0,
            "start_capital": 1_000_000.0, "end_equity": 1_100_000.0,
            "closed_trades": 0, "win_rate": None, "force_closed": 0,
            "satellite_added_this_cycle": [], "satellite_open_positions": 0,
            "satellite_reserved_usd": 0.0, "satellite_rejected_no_room": 0,
        }

    async def fake_notify(text: str) -> None:
        pass

    monkeypatch.setattr("aria_core.paper_trader.weekly_cycle_due", fake_due)
    monkeypatch.setattr("aria_core.paper_trader.run_weekly_reset", fake_reset)
    monkeypatch.setattr(heartbeat.aria_heartbeat, "_notify_telegram_trading", fake_notify)

    await heartbeat.aria_heartbeat._run_task("paper_weekly_review_cycle")

    assert due_calls == ["swing", "scalping"]
    assert reset_calls == ["swing"]  # scalping wasn't due -- never reset
