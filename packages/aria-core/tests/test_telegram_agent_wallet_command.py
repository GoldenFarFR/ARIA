"""/agentwallet -- solde reel du wallet agent CDP (#204, 16/07), admin-only,
lecture seule. Meme patron que /feedback (test_telegram_feedback_command.py)."""
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


def test_agentwallet_registered_as_command_handler():
    app = MagicMock()
    telegram_bot._register_handlers(app)

    all_commands: set[str] = set()
    for call in app.add_handler.call_args_list:
        handler = call.args[0]
        commands = getattr(handler, "commands", None)
        if commands:
            all_commands |= set(commands)
    assert "agentwallet" in all_commands


@pytest.mark.asyncio
async def test_agentwallet_admin_only_visitor_rejected(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: False)
    monkeypatch.setattr(telegram_bot.settings, "admin_ids", [999])

    update = FakeUpdate("/agentwallet", user_id=123)
    await telegram_bot._handle_agent_wallet(update, FakeContext())

    assert len(update.message.replies) == 1
    reply = update.message.replies[0].lower()
    assert "restricted" in reply or "administrator" in reply


@pytest.mark.asyncio
async def test_agentwallet_shows_both_balances(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)
    monkeypatch.setattr(telegram_bot.settings, "admin_ids", [42])

    async def fake_summary():
        return {
            "wallet_address": "0xF04625162b616c5ad9788811b7be8CDd425B37Ef",
            "chain": "base",
            "usdc_usd": 1.0,
            "eth": 0.0009,
            "other_tokens": [{"address": "0xdeadbeef", "symbol": "SOMEGEM", "amount": 42.0}],
        }

    monkeypatch.setattr(
        "aria_core.agent_wallet_monitor.get_wallet_balance_summary", fake_summary,
    )

    update = FakeUpdate("/agentwallet", user_id=42)
    await telegram_bot._handle_agent_wallet(update, FakeContext())

    reply = update.message.replies[0]
    assert "1.0000 USDC" in reply
    assert "0.000900 ETH" in reply
    assert "0xF04625162b616c5ad9788811b7be8CDd425B37Ef" in reply
    assert "42.0 SOMEGEM" in reply


@pytest.mark.asyncio
async def test_agentwallet_degrades_honestly_when_balances_unavailable(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)
    monkeypatch.setattr(telegram_bot.settings, "admin_ids", [42])

    async def fake_summary():
        return {
            "wallet_address": "0xF04625162b616c5ad9788811b7be8CDd425B37Ef",
            "chain": "base",
            "usdc_usd": None,
            "eth": None,
        }

    monkeypatch.setattr(
        "aria_core.agent_wallet_monitor.get_wallet_balance_summary", fake_summary,
    )

    update = FakeUpdate("/agentwallet", user_id=42)
    await telegram_bot._handle_agent_wallet(update, FakeContext())

    reply = update.message.replies[0].lower()
    assert "indisponible" in reply
