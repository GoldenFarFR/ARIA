"""/mode [standard|scalping] -- Item #101, 26/07: the only way to switch the
Milly ($1M) test's entry mode. Same pattern as /langue
(test_vc_lang_and_concurrency.py) and /performance
(test_telegram_performance_command.py)."""
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


def test_mode_registered_as_command_handler():
    app = MagicMock()
    telegram_bot._register_handlers(app)

    all_commands: set[str] = set()
    for call in app.add_handler.call_args_list:
        handler = call.args[0]
        commands = getattr(handler, "commands", None)
        if commands:
            all_commands |= set(commands)
    assert "mode" in all_commands


@pytest.mark.asyncio
async def test_mode_admin_only_visitor_rejected(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: False)
    monkeypatch.setattr(telegram_bot.settings, "admin_ids", [999])

    update = FakeUpdate("/mode", user_id=123)
    await telegram_bot._handle_trading_mode(update, FakeContext())

    assert len(update.message.replies) == 1
    reply = update.message.replies[0].lower()
    assert "restricted" in reply or "administrator" in reply


@pytest.mark.asyncio
async def test_mode_no_argument_shows_current(tmp_path, monkeypatch):
    from aria_core import paper_trader

    monkeypatch.setattr(paper_trader, "DB_PATH", str(tmp_path / "paper.db"))
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)
    monkeypatch.setattr(telegram_bot.settings, "admin_ids", [42])

    update = FakeUpdate("/mode", user_id=42)
    await telegram_bot._handle_trading_mode(update, FakeContext())

    assert "standard" in update.message.replies[0].lower()


@pytest.mark.asyncio
async def test_mode_switches_to_scalping_and_persists(tmp_path, monkeypatch):
    from aria_core import paper_trader

    monkeypatch.setattr(paper_trader, "DB_PATH", str(tmp_path / "paper.db"))
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)
    monkeypatch.setattr(telegram_bot.settings, "admin_ids", [42])

    update = FakeUpdate("/mode scalping", user_id=42)
    await telegram_bot._handle_trading_mode(update, FakeContext(["scalping"]))

    assert "scalping" in update.message.replies[0].lower()
    assert await paper_trader.get_trading_mode() == "scalping"

    # Vérifie l'état courant reflété correctement à un appel suivant sans argument.
    update2 = FakeUpdate("/mode", user_id=42)
    await telegram_bot._handle_trading_mode(update2, FakeContext())
    assert "scalping" in update2.message.replies[0].lower()


@pytest.mark.asyncio
async def test_mode_invalid_argument_shows_usage(tmp_path, monkeypatch):
    from aria_core import paper_trader

    monkeypatch.setattr(paper_trader, "DB_PATH", str(tmp_path / "paper.db"))
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)
    monkeypatch.setattr(telegram_bot.settings, "admin_ids", [42])

    update = FakeUpdate("/mode swing", user_id=42)
    await telegram_bot._handle_trading_mode(update, FakeContext(["swing"]))

    assert "usage" in update.message.replies[0].lower()
    assert await paper_trader.get_trading_mode() == "standard"
