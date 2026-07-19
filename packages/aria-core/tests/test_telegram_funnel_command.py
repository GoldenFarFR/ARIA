"""/funnel [heures] -- cumul du funnel de rejet momentum (19/07), admin-only,
lecture seule. Même patron que /agentwallet (test_telegram_agent_wallet_command.py)."""
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


def test_funnel_registered_as_command_handler():
    app = MagicMock()
    telegram_bot._register_handlers(app)

    all_commands: set[str] = set()
    for call in app.add_handler.call_args_list:
        handler = call.args[0]
        commands = getattr(handler, "commands", None)
        if commands:
            all_commands |= set(commands)
    assert "funnel" in all_commands


def test_funnel_in_menu_commands():
    names = [name for name, _ in telegram_bot.TELEGRAM_MENU_COMMANDS]
    assert "funnel" in names
    assert names == sorted(names)


@pytest.mark.asyncio
async def test_funnel_admin_only_visitor_rejected(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: False)
    monkeypatch.setattr(telegram_bot.settings, "admin_ids", [999])

    update = FakeUpdate("/funnel", user_id=123)
    await telegram_bot._handle_funnel(update, FakeContext())

    assert len(update.message.replies) == 1
    reply = update.message.replies[0].lower()
    assert "restricted" in reply or "administrator" in reply


@pytest.mark.asyncio
async def test_funnel_default_window_is_48h(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)
    monkeypatch.setattr(telegram_bot.settings, "admin_ids", [42])

    captured = {}

    async def fake_summarize_since(hours=48.0):
        captured["hours"] = hours
        return {}

    monkeypatch.setattr(
        "aria_core.momentum_funnel_log.summarize_since", fake_summarize_since
    )

    update = FakeUpdate("/funnel", user_id=42)
    await telegram_bot._handle_funnel(update, FakeContext())

    assert captured["hours"] == 48.0
    assert "Aucun rejet" in update.message.replies[0]


@pytest.mark.asyncio
async def test_funnel_custom_window_argument(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)
    monkeypatch.setattr(telegram_bot.settings, "admin_ids", [42])

    captured = {}

    async def fake_summarize_since(hours=48.0):
        captured["hours"] = hours
        return {"no_entry_signal": 5}

    monkeypatch.setattr(
        "aria_core.momentum_funnel_log.summarize_since", fake_summarize_since
    )

    update = FakeUpdate("/funnel 12", user_id=42)
    await telegram_bot._handle_funnel(update, FakeContext(args=["12"]))

    assert captured["hours"] == 12.0
    assert "no_entry_signal" in update.message.replies[0]


@pytest.mark.asyncio
async def test_funnel_invalid_argument_falls_back_to_default(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)
    monkeypatch.setattr(telegram_bot.settings, "admin_ids", [42])

    captured = {}

    async def fake_summarize_since(hours=48.0):
        captured["hours"] = hours
        return {}

    monkeypatch.setattr(
        "aria_core.momentum_funnel_log.summarize_since", fake_summarize_since
    )

    update = FakeUpdate("/funnel pas-un-nombre", user_id=42)
    await telegram_bot._handle_funnel(update, FakeContext(args=["pas-un-nombre"]))

    assert captured["hours"] == 48.0
