"""27/07 -- 3-pocket architecture plan, Phase 4: the weekly training protocol
(run_weekly_reset) now covers scalping in addition to swing (both are trading
pockets on a short/medium horizon), but VC deliberately never resets (judged
on a rolling history instead, per the plan's own decision). Regression
coverage for the exact wallets the heartbeat loops over.

08/02 -- real bug found live (adversarial cross-review workflow): the loop
used to be a literal ("swing", "scalping") tuple, which stopped matching any
real scalping pocket the moment scalping_variants_enabled() migrated
"scalping"'s history to "scalping_v6" alongside 5 new scalping_v1..v5
pockets -- the +10%/1M$ weekly protocol silently stopped running for all 6
real scalping pockets. The tests below used to mock aria_core.paper_trader.
weekly_cycle_due directly, which is exactly what let that regression hide:
they never exercised the real wallet list (all_pocket_wallets()) at all.
Fixed to leave all_pocket_wallets() UNMOCKED so a future drift of this kind
fails a test again instead of passing silently."""
from __future__ import annotations

import pytest

from aria_core import heartbeat


def _fake_report(wallet: str) -> dict:
    return {
        "cycle_number": 1, "validated": True, "return_pct": 10.0,
        "next_cycle_number": 2, "target_equity": 1_100_000.0,
        "start_capital": 1_000_000.0, "end_equity": 1_100_000.0,
        "closed_trades": 0, "win_rate": None, "force_closed": 0,
        "satellite_added_this_cycle": [], "satellite_open_positions": 0,
        "satellite_reserved_usd": 0.0, "satellite_rejected_no_room": 0,
    }


@pytest.mark.asyncio
async def test_paper_weekly_review_cycle_resets_swing_and_scalping_never_vc(monkeypatch):
    """Gate OFF (default in tests): all_pocket_wallets() returns exactly
    ("scalping", "swing", "vc") -- real (unmocked) call, "vc" must never be
    among the wallets due/reset is called on."""
    due_calls: list[str] = []
    reset_calls: list[str] = []

    async def fake_due(wallet: str = "swing") -> bool:
        due_calls.append(wallet)
        return True

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

    assert set(due_calls) == {"scalping", "swing"}
    assert set(reset_calls) == {"scalping", "swing"}
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
        return _fake_report(wallet)

    async def fake_notify(text: str) -> None:
        pass

    monkeypatch.setattr("aria_core.paper_trader.weekly_cycle_due", fake_due)
    monkeypatch.setattr("aria_core.paper_trader.run_weekly_reset", fake_reset)
    monkeypatch.setattr(heartbeat.aria_heartbeat, "_notify_telegram_trading", fake_notify)

    await heartbeat.aria_heartbeat._run_task("paper_weekly_review_cycle")

    assert set(due_calls) == {"scalping", "swing"}
    assert reset_calls == ["swing"]  # scalping wasn't due -- never reset


@pytest.mark.asyncio
async def test_paper_weekly_review_cycle_covers_all_7_scalping_variants_when_gate_on(monkeypatch):
    """The exact regression this fix targets: with scalping_variants_enabled()
    on, all_pocket_wallets() replaces "scalping" with scalping_v1..v7 -- the
    weekly loop must follow, never keep looking for a "scalping" row that no
    longer exists."""
    monkeypatch.setenv("ARIA_MULTI_POCKET_SOURCING_ENABLED", "true")
    monkeypatch.setenv("ARIA_SCALPING_VARIANTS_ENABLED", "true")
    due_calls: list[str] = []

    async def fake_due(wallet: str = "swing") -> bool:
        due_calls.append(wallet)
        return False  # nothing due -- only care about which wallets were checked

    async def fake_reset(*, price_lookup=None, wallet: str = "swing") -> dict:
        return _fake_report(wallet)

    async def fake_notify(text: str) -> None:
        pass

    monkeypatch.setattr("aria_core.paper_trader.weekly_cycle_due", fake_due)
    monkeypatch.setattr("aria_core.paper_trader.run_weekly_reset", fake_reset)
    monkeypatch.setattr(heartbeat.aria_heartbeat, "_notify_telegram_trading", fake_notify)

    await heartbeat.aria_heartbeat._run_task("paper_weekly_review_cycle")

    assert set(due_calls) == {
        "scalping_v1", "scalping_v2", "scalping_v3", "scalping_v4", "scalping_v5", "scalping_v6",
        "scalping_v7", "scalping_v8", "swing",
    }
    assert "scalping" not in due_calls
    assert "vc" not in due_calls
