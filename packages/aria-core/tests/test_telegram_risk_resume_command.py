"""/riskresume -- lève le coupe-circuit dur du portefeuille paper (20/07, revue
croisée externe). Owner-only, même gate que /stop /resume (test_telegram_fallback_
notice.py) -- pas is_admin comme /funnel (test_telegram_funnel_command.py)."""
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
    def __init__(self, text: str, user_id: int = 7):
        self.message = FakeMessage(text)
        self.effective_user = FakeUser(user_id)
        self.callback_query = None


class FakeContext:
    def __init__(self):
        self.args: list[str] = []


def test_riskresume_registered_as_command_handler():
    app = MagicMock()
    telegram_bot._register_handlers(app)

    all_commands: set[str] = set()
    for call in app.add_handler.call_args_list:
        handler = call.args[0]
        commands = getattr(handler, "commands", None)
        if commands:
            all_commands |= set(commands)
    assert "riskresume" in all_commands


def test_riskresume_in_menu_commands_alphabetically():
    names = [name for name, _ in telegram_bot.TELEGRAM_MENU_COMMANDS]
    assert "riskresume" in names
    assert names == sorted(names)


@pytest.mark.asyncio
async def test_riskresume_rejects_non_owner(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_owner", lambda uid: uid == 7)
    calls = {"resume": 0}
    monkeypatch.setattr(
        telegram_bot.risk_guard, "resume_new_entries",
        lambda **kw: calls.__setitem__("resume", calls["resume"] + 1),
    )

    update = FakeUpdate("/riskresume", user_id=123)
    await telegram_bot._handle_risk_resume(update, FakeContext())

    assert len(update.message.replies) == 1
    assert "propriétaire" in update.message.replies[0].lower()
    assert calls["resume"] == 0


@pytest.mark.asyncio
async def test_riskresume_when_not_blocked_says_nothing_to_resume(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_owner", lambda uid: uid == 7)
    monkeypatch.setattr(
        telegram_bot.risk_guard, "new_entry_block_status",
        lambda: {"blocked": False, "since": None, "reason": "", "readable": True},
    )
    calls = {"resume": 0}
    monkeypatch.setattr(
        telegram_bot.risk_guard, "resume_new_entries",
        lambda **kw: calls.__setitem__("resume", calls["resume"] + 1),
    )

    update = FakeUpdate("/riskresume", user_id=7)
    await telegram_bot._handle_risk_resume(update, FakeContext())

    assert "rien à reprendre" in update.message.replies[0].lower()
    assert calls["resume"] == 0


@pytest.mark.asyncio
async def test_riskresume_when_blocked_lifts_and_reports_reason(monkeypatch):
    from datetime import datetime, timezone

    monkeypatch.setattr(telegram_bot, "is_owner", lambda uid: uid == 7)
    since = datetime(2026, 7, 20, 10, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        telegram_bot.risk_guard, "new_entry_block_status",
        lambda: {
            "blocked": True, "since": since,
            "reason": "5 pertes consécutives", "readable": True,
        },
    )
    captured = {}

    def fake_resume(**kw):
        captured.update(kw)

    monkeypatch.setattr(telegram_bot.risk_guard, "resume_new_entries", fake_resume)

    update = FakeUpdate("/riskresume", user_id=7)
    await telegram_bot._handle_risk_resume(update, FakeContext())

    assert captured["by"] == 7
    reply = update.message.replies[0]
    assert "levé" in reply.lower()
    assert "5 pertes consécutives" in reply
    assert "2026-07-20 10:00 UTC" in reply
