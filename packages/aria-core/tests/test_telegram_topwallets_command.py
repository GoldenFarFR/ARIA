"""/topwallets -- classement top 50 meilleurs investisseurs (21/07), admin-only,
lecture seule. Même patron que /funnel (test_telegram_funnel_command.py)."""
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


def test_topwallets_registered_as_command_handler():
    app = MagicMock()
    telegram_bot._register_handlers(app)

    all_commands: set[str] = set()
    for call in app.add_handler.call_args_list:
        handler = call.args[0]
        commands = getattr(handler, "commands", None)
        if commands:
            all_commands |= set(commands)
    assert "topwallets" in all_commands


def test_topwallets_in_menu_commands():
    names = [name for name, _ in telegram_bot.TELEGRAM_MENU_COMMANDS]
    assert "topwallets" in names
    assert names == sorted(names)


@pytest.mark.asyncio
async def test_topwallets_admin_only_visitor_rejected(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: False)
    monkeypatch.setattr(telegram_bot.settings, "admin_ids", [999])

    update = FakeUpdate("/topwallets", user_id=123)
    await telegram_bot._handle_topwallets(update, FakeContext())

    assert len(update.message.replies) == 1
    reply = update.message.replies[0].lower()
    assert "restricted" in reply or "administrator" in reply


@pytest.mark.asyncio
async def test_topwallets_empty_leaderboard(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)
    monkeypatch.setattr(telegram_bot.settings, "admin_ids", [42])
    monkeypatch.setattr(
        "aria_core.services.smart_money_leaderboard.get_leaderboard", lambda: _empty()
    )

    update = FakeUpdate("/topwallets", user_id=42)
    await telegram_bot._handle_topwallets(update, FakeContext())

    assert "vide" in update.message.replies[0].lower()


async def _empty():
    return []


@pytest.mark.asyncio
async def test_topwallets_formats_ranked_list(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)
    monkeypatch.setattr(telegram_bot.settings, "admin_ids", [42])

    async def _fake_leaderboard():
        return [
            {"rank": 1, "wallet": "0x" + "a" * 40, "composite_percentile": 92.0},
            {"rank": 2, "wallet": "0x" + "b" * 40, "composite_percentile": 81.0},
        ]

    monkeypatch.setattr(
        "aria_core.services.smart_money_leaderboard.get_leaderboard", _fake_leaderboard
    )

    update = FakeUpdate("/topwallets", user_id=42)
    await telegram_bot._handle_topwallets(update, FakeContext())

    reply = update.message.replies[0]
    assert "1." in reply and "92e" in reply
    assert "2." in reply and "81e" in reply
