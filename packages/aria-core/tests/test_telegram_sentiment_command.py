"""/sentiment — dernière lecture de sentiment de marché, admin-only. Aucun réseau :
latest_readings est mockée (déjà testée en profondeur dans test_market_sentiment.py)."""
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
async def test_sentiment_rejects_non_admin(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: False)
    monkeypatch.setattr(telegram_bot.settings, "admin_ids", [999])
    readings_mock = AsyncMock()
    monkeypatch.setattr(
        "aria_core.skills.market_sentiment.latest_readings", readings_mock,
    )

    update = FakeUpdate("/sentiment")
    await telegram_bot._handle_sentiment(update, FakeContext())

    assert len(update.message.replies) == 1
    readings_mock.assert_not_called()


@pytest.mark.asyncio
async def test_sentiment_admin_gets_full_report(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)
    rows = [{
        "pair": "BTC", "regime": "euphorie", "detail": "RSI 80, Bollinger 1.1",
        "computed_at": "2026-07-10T00:00:00+00:00",
    }]
    monkeypatch.setattr(
        "aria_core.skills.market_sentiment.latest_readings", AsyncMock(return_value=rows),
    )

    update = FakeUpdate("/sentiment")
    await telegram_bot._handle_sentiment(update, FakeContext())

    reply = update.message.replies[0]
    assert "BTC" in reply
    assert "RSI 80" in reply
