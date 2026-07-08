"""/cycles — les 3 derniers cycles Bitcoin. Aucun réseau : analyze_btc_cycles est mocké."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from aria_core.gateway import telegram_bot


class FakeMessage:
    def __init__(self, text: str):
        self.text = text
        self.replies: list[str] = []

    async def reply_text(self, text: str) -> None:
        self.replies.append(text)


class FakeUser:
    def __init__(self, user_id: int):
        self.id = user_id


class FakeUpdate:
    def __init__(self, text: str, user_id: int = 42):
        self.message = FakeMessage(text)
        self.effective_user = FakeUser(user_id)
        self.callback_query = None


class FakeContext:
    def __init__(self, args: list[str] | None = None):
        self.args = args or []


@pytest.mark.asyncio
async def test_cycles_rejects_non_admin(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: False)
    monkeypatch.setattr(telegram_bot.settings, "admin_ids", [999])
    analyze_mock = AsyncMock()
    monkeypatch.setattr("aria_core.skills.btc_cycles.analyze_btc_cycles", analyze_mock)

    update = FakeUpdate("/cycles")
    await telegram_bot._handle_cycles(update, FakeContext())

    assert len(update.message.replies) == 1
    analyze_mock.assert_not_called()


@pytest.mark.asyncio
async def test_cycles_reports_unavailable(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)
    monkeypatch.setattr(
        "aria_core.skills.btc_cycles.analyze_btc_cycles",
        AsyncMock(return_value={"available": False, "error": "historique BTC indisponible"}),
    )

    update = FakeUpdate("/cycles")
    await telegram_bot._handle_cycles(update, FakeContext())

    assert "indisponible" in update.message.replies[-1].lower()


@pytest.mark.asyncio
async def test_cycles_formats_real_report(monkeypatch):
    from aria_core.skills.btc_cycles import CycleStats

    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)
    stats = CycleStats(
        name="cycle A", window_start="2020-01-01", window_end="2020-12-31",
        low_price=100.0, low_date="2020-01-01", high_price=300.0, high_date="2020-06-01",
        gain_low_to_high_pct=200.0, drawdown_high_to_end_pct=-50.0, phases=[],
    )
    monkeypatch.setattr(
        "aria_core.skills.btc_cycles.analyze_btc_cycles",
        AsyncMock(return_value={"available": True, "cycles": [stats], "narrative": "Récit factuel."}),
    )

    update = FakeUpdate("/cycles")
    await telegram_bot._handle_cycles(update, FakeContext())

    # Un message de progression, puis le rapport formaté.
    assert len(update.message.replies) == 2
    report = update.message.replies[-1]
    assert "300" in report and "Récit factuel." in report
    assert "pas une loi de marché" in report.lower()
