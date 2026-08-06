"""/llmspend -- 06/08, operator request: on-demand cost breakdown so a
"dépense louche" can be caught even for skills that never notify Telegram on
their own (VC intelligence, smart-money, source-code audit, the momentum
trading tie-breaker). Owner-only, same posture as the per-reply cost line."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from aria_core import llm_usage
from aria_core.gateway import telegram_bot
from aria_core.testing import configure_test_runtime


class FakeMessage:
    def __init__(self):
        self.replies: list[str] = []

    async def reply_text(self, text: str, **kwargs) -> None:
        self.replies.append(text)


class FakeUser:
    def __init__(self, user_id: int):
        self.id = user_id


class FakeUpdate:
    def __init__(self, user_id: int):
        self.message = FakeMessage()
        self.effective_user = FakeUser(user_id)


class FakeContext:
    def __init__(self):
        self.args: list[str] = []


def test_llmspend_registered_as_command_handler():
    app = MagicMock()
    telegram_bot._register_handlers(app)
    all_commands: set[str] = set()
    for call in app.add_handler.call_args_list:
        handler = call.args[0]
        commands = getattr(handler, "commands", None)
        if commands:
            all_commands |= set(commands)
    assert "llmspend" in all_commands


def test_llmspend_in_menu_commands_alphabetically():
    names = [name for name, _ in telegram_bot.TELEGRAM_MENU_COMMANDS]
    assert "llmspend" in names
    assert names == sorted(names)


@pytest.mark.asyncio
async def test_non_owner_admin_refused(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)
    monkeypatch.setattr(telegram_bot, "is_owner", lambda uid: uid == 7)
    update = FakeUpdate(user_id=999)
    await telegram_bot._handle_llmspend(update, FakeContext())
    assert "Réservé à l'opérateur" in update.message.replies[0]


@pytest.mark.asyncio
async def test_owner_sees_real_breakdown(monkeypatch, tmp_path):
    configure_test_runtime(data_dir=tmp_path)
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)
    monkeypatch.setattr(telegram_bot, "is_owner", lambda uid: uid == 7)

    now = datetime.now(timezone.utc)
    llm_usage.record_llm_usage(
        provider="anthropic", model="claude-haiku-4-5-20251001",
        input_tokens=1_000_000, output_tokens=1_000_000, at=now,
    )
    llm_usage.record_llm_usage(
        provider="grok", model="x-ai-grok-4-3",
        input_tokens=1000, output_tokens=1000, at=now,
    )

    update = FakeUpdate(user_id=7)
    await telegram_bot._handle_llmspend(update, FakeContext())
    reply = update.message.replies[0]

    assert "6.00000$" in reply  # $1 in + $5 out on 1M/1M tokens, known price
    assert "anthropic/claude-haiku-4-5-20251001" in reply
    assert "grok/x-ai-grok-4-3" in reply
    assert "prix inconnu" in reply
