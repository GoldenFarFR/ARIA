"""/performance -- full winrate/PnL/expectancy breakdown of every closed
paper-trading trade (07/23), admin-only, read-only. Same pattern as /topwallets
(test_telegram_topwallets_command.py)."""
from __future__ import annotations

from unittest.mock import MagicMock

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


def test_performance_registered_as_command_handler():
    app = MagicMock()
    telegram_bot._register_handlers(app)

    all_commands: set[str] = set()
    for call in app.add_handler.call_args_list:
        handler = call.args[0]
        commands = getattr(handler, "commands", None)
        if commands:
            all_commands |= set(commands)
    assert "performance" in all_commands


@pytest.mark.asyncio
async def test_performance_admin_only_visitor_rejected(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: False)
    monkeypatch.setattr(telegram_bot.settings, "admin_ids", [999])

    update = FakeUpdate("/performance", user_id=123)
    await telegram_bot._handle_performance(update, FakeContext())

    assert len(update.message.replies) == 1
    reply = update.message.replies[0].lower()
    assert "restricted" in reply or "administrator" in reply


@pytest.mark.asyncio
async def test_performance_no_trades_yet(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)
    monkeypatch.setattr(telegram_bot.settings, "admin_ids", [42])

    async def _empty():
        return []

    monkeypatch.setattr("aria_core.performance_breakdown.get_all_closed_trades", _empty)

    update = FakeUpdate("/performance", user_id=42)
    await telegram_bot._handle_performance(update, FakeContext())

    assert "aucun trade" in update.message.replies[0].lower()


@pytest.mark.asyncio
async def test_performance_formats_global_and_breakdown(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)
    monkeypatch.setattr(telegram_bot.settings, "admin_ids", [42])

    async def _fake_trades():
        return [
            {"pnl_usd": 500.0, "closed_at": "2026-07-20T10:00:00", "conviction_tier": "strong"},
            {"pnl_usd": -100.0, "closed_at": "2026-07-21T10:00:00", "conviction_tier": "weak"},
        ]

    monkeypatch.setattr("aria_core.performance_breakdown.get_all_closed_trades", _fake_trades)

    update = FakeUpdate("/performance", user_id=42)
    await telegram_bot._handle_performance(update, FakeContext())

    reply = update.message.replies[0]
    assert "Trades : 2" in reply
    assert "Palier de conviction" in reply
    assert "strong" in reply and "weak" in reply
