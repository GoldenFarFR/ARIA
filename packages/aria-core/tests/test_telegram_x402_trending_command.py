"""/x402trending -- découverte de services x402 (registre CDP officiel), triés par
volume 30j (19/07). Admin-only, lecture seule -- aucun paiement déclenché. Même patron
que /agentwallet (test_telegram_agent_wallet_command.py)."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from aria_core.gateway import telegram_bot
from aria_core.services import x402_bazaar


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


def test_x402trending_registered_as_command_handler():
    app = MagicMock()
    telegram_bot._register_handlers(app)

    all_commands: set[str] = set()
    for call in app.add_handler.call_args_list:
        handler = call.args[0]
        commands = getattr(handler, "commands", None)
        if commands:
            all_commands |= set(commands)
    assert "x402trending" in all_commands


def test_x402trending_in_menu_commands():
    names = [name for name, _ in telegram_bot.TELEGRAM_MENU_COMMANDS]
    assert "x402trending" in names
    assert names == sorted(names)


@pytest.mark.asyncio
async def test_x402trending_admin_only_visitor_rejected(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: False)
    monkeypatch.setattr(telegram_bot.settings, "admin_ids", [999])

    update = FakeUpdate("/x402trending", user_id=123)
    await telegram_bot._handle_x402_trending(update, FakeContext())

    assert len(update.message.replies) == 1
    reply = update.message.replies[0].lower()
    assert "restricted" in reply or "administrator" in reply


@pytest.mark.asyncio
async def test_x402trending_no_query_calls_discover_trending_empty_query(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)
    monkeypatch.setattr(telegram_bot.settings, "admin_ids", [42])

    captured = {}

    async def fake_discover_trending(*, query="", **kwargs):
        captured["query"] = query
        return x402_bazaar.X402BazaarSearchResult(available=True, resources=[])

    monkeypatch.setattr(
        "aria_core.services.x402_bazaar.discover_trending", fake_discover_trending
    )

    update = FakeUpdate("/x402trending", user_id=42)
    await telegram_bot._handle_x402_trending(update, FakeContext())

    assert captured["query"] == ""
    assert "Aucun résultat" in update.message.replies[0]


@pytest.mark.asyncio
async def test_x402trending_passes_query_args_through(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)
    monkeypatch.setattr(telegram_bot.settings, "admin_ids", [42])

    captured = {}

    async def fake_discover_trending(*, query="", **kwargs):
        captured["query"] = query
        return x402_bazaar.X402BazaarSearchResult(available=True, resources=[])

    monkeypatch.setattr(
        "aria_core.services.x402_bazaar.discover_trending", fake_discover_trending
    )

    update = FakeUpdate("/x402trending crypto market data", user_id=42)
    await telegram_bot._handle_x402_trending(update, FakeContext(args=["crypto", "market", "data"]))

    assert captured["query"] == "crypto market data"


@pytest.mark.asyncio
async def test_x402trending_shows_real_shaped_result(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)
    monkeypatch.setattr(telegram_bot.settings, "admin_ids", [42])

    async def fake_discover_trending(*, query="", **kwargs):
        return x402_bazaar.X402BazaarSearchResult(
            available=True,
            resources=[
                x402_bazaar.X402BazaarResource(
                    resource_url="https://x402.tavily.com/search",
                    service_name="Tavily Search",
                    curated=True,
                    calls_last_30d=48319,
                    unique_payers_last_30d=374,
                    price_usd=0.49,
                )
            ],
        )

    monkeypatch.setattr(
        "aria_core.services.x402_bazaar.discover_trending", fake_discover_trending
    )

    update = FakeUpdate("/x402trending", user_id=42)
    await telegram_bot._handle_x402_trending(update, FakeContext())

    reply = update.message.replies[0]
    assert "Tavily Search" in reply
    assert "48319" in reply
    assert "aucun paiement" in reply.lower()
