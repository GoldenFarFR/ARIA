"""/alerts — dernier digest crypto-Twitter (Otto AI, x402), admin-only. Aucun réseau :
latest_reading est mockée (déjà testée en profondeur dans test_market_alerts.py). Même
patron que test_telegram_sentiment_command.py."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from aria_core.gateway import telegram_bot
from aria_core.skills.market_alerts import MarketAlertsReading


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
async def test_alerts_rejects_non_admin(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: False)
    monkeypatch.setattr(telegram_bot.settings, "admin_ids", [999])
    reading_mock = AsyncMock()
    monkeypatch.setattr("aria_core.skills.market_alerts.latest_reading", reading_mock)

    update = FakeUpdate("/alerts")
    await telegram_bot._handle_alerts(update, FakeContext())

    assert len(update.message.replies) == 1
    reading_mock.assert_not_called()


@pytest.mark.asyncio
async def test_alerts_admin_gets_full_report(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)
    reading = MarketAlertsReading(
        digest_text="[ALERT] whale moves $100M into ETH",
        source_timestamp="2026-07-19T15:03:30.842Z",
        computed_at="2026-07-19T15:04:00+00:00",
    )
    monkeypatch.setattr(
        "aria_core.skills.market_alerts.latest_reading", AsyncMock(return_value=reading),
    )

    update = FakeUpdate("/alerts")
    await telegram_bot._handle_alerts(update, FakeContext())

    reply = update.message.replies[0]
    assert "whale moves $100M into ETH" in reply
    assert "Otto AI" in reply


@pytest.mark.asyncio
async def test_alerts_no_data_yet_degrades_gracefully(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)
    monkeypatch.setattr(
        "aria_core.skills.market_alerts.latest_reading", AsyncMock(return_value=None),
    )

    update = FakeUpdate("/alerts")
    await telegram_bot._handle_alerts(update, FakeContext())

    assert "aucune lecture" in update.message.replies[0].lower()


def test_alerts_registered_in_menu_commands():
    names = [name for name, _desc in telegram_bot.TELEGRAM_MENU_COMMANDS]
    assert "alerts" in names
    # trié alphabétiquement (18/07, doctrine du menu) -- ne casse jamais le tri existant.
    assert names == sorted(names)
