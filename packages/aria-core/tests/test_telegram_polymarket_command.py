"""/polymarket -- operator-facing snapshot of the dedicated Polymarket paper
portfolio (26/07, Item #108), admin-only, read-only. Same pattern as
/performance (test_telegram_performance_command.py)."""
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


def test_polymarket_registered_as_command_handler():
    app = MagicMock()
    telegram_bot._register_handlers(app)

    all_commands: set[str] = set()
    for call in app.add_handler.call_args_list:
        handler = call.args[0]
        commands = getattr(handler, "commands", None)
        if commands:
            all_commands |= set(commands)
    assert "polymarket" in all_commands


@pytest.mark.asyncio
async def test_polymarket_admin_only_visitor_rejected(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: False)
    monkeypatch.setattr(telegram_bot.settings, "admin_ids", [999])

    update = FakeUpdate("/polymarket", user_id=123)
    await telegram_bot._handle_polymarket(update, FakeContext())

    assert len(update.message.replies) == 1
    reply = update.message.replies[0].lower()
    assert "restricted" in reply or "administrator" in reply


@pytest.mark.asyncio
async def test_polymarket_reports_the_real_portfolio_state(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)
    monkeypatch.setattr(telegram_bot.settings, "admin_ids", [42])

    async def fake_report(*, recent_closed_limit: int = 5) -> str:
        return "[FICTIF] Portefeuille Polymarket (paper trading)\nÉquité : $100,000"

    monkeypatch.setattr("aria_core.polymarket_paper_trader.format_portfolio_report", fake_report)

    update = FakeUpdate("/polymarket", user_id=42)
    await telegram_bot._handle_polymarket(update, FakeContext())

    assert "Portefeuille Polymarket" in update.message.replies[0]
