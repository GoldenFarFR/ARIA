"""/runwayapi -- runway consolidé des 6 providers log-based migrés sur
resource_budget.py (#302, 13/08), admin-only, lecture seule. Même patron que
/topwallets (test_telegram_topwallets_command.py)."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from aria_core.gateway import telegram_bot


class FakeMessage:
    def __init__(self, text: str):
        self.text = text
        self.replies: list[str] = []

    async def reply_text(self, text: str, **kwargs) -> None:
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


def test_runway_api_registered_as_command_handler():
    app = MagicMock()
    telegram_bot._register_handlers(app)

    all_commands: set[str] = set()
    for call in app.add_handler.call_args_list:
        handler = call.args[0]
        commands = getattr(handler, "commands", None)
        if commands:
            all_commands |= set(commands)
    assert "runwayapi" in all_commands


def test_runway_api_in_menu_commands():
    names = [name for name, _ in telegram_bot.TELEGRAM_MENU_COMMANDS]
    assert "runwayapi" in names
    assert names == sorted(names)


@pytest.mark.asyncio
async def test_runway_api_admin_only_visitor_rejected(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: False)
    monkeypatch.setattr(telegram_bot.settings, "admin_ids", [999])

    update = FakeUpdate("/runwayapi", user_id=123)
    await telegram_bot._handle_runway_api(update, FakeContext())

    assert len(update.message.replies) == 1
    reply = update.message.replies[0].lower()
    assert "restricted" in reply or "administrator" in reply


async def _status(spent: int, cap: int) -> dict:
    return {"cap_credits": cap, "spent_credits": spent, "remaining_credits": max(0, cap - spent)}


@pytest.mark.asyncio
async def test_runway_api_reports_all_six_providers(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)
    monkeypatch.setattr(telegram_bot.settings, "admin_ids", [42])

    from aria_core.services import blockscout_credit_budget, coingecko, dune, firecrawl_budget, mobula, tavily_budget

    monkeypatch.setattr(coingecko, "monthly_status", lambda **_: _status(100, 9_500))
    monkeypatch.setattr(mobula, "monthly_status", lambda **_: _status(200, 9_500))
    monkeypatch.setattr(dune, "monthly_status", lambda **_: _status(50, 2_375))
    monkeypatch.setattr(blockscout_credit_budget, "daily_status", lambda **_: _status(1_000, 90_000))
    monkeypatch.setattr(firecrawl_budget, "monthly_status", lambda **_: _status(10, 900))
    monkeypatch.setattr(tavily_budget, "monthly_status", lambda **_: _status(20, 900))

    update = FakeUpdate("/runwayapi", user_id=42)
    await telegram_bot._handle_runway_api(update, FakeContext())

    reply = update.message.replies[0]
    for label in ("CoinGecko", "Mobula", "Dune", "Blockscout Pro", "Firecrawl", "Tavily"):
        assert label in reply
    assert "100/9500" in reply
    assert "1000/90000" in reply


@pytest.mark.asyncio
async def test_runway_api_one_broken_provider_never_blocks_the_others(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)
    monkeypatch.setattr(telegram_bot.settings, "admin_ids", [42])

    from aria_core.services import blockscout_credit_budget, coingecko, dune, firecrawl_budget, mobula, tavily_budget

    async def _broken(**_):
        raise RuntimeError("boom")

    monkeypatch.setattr(coingecko, "monthly_status", _broken)
    monkeypatch.setattr(mobula, "monthly_status", lambda **_: _status(1, 9_500))
    monkeypatch.setattr(dune, "monthly_status", lambda **_: _status(1, 2_375))
    monkeypatch.setattr(blockscout_credit_budget, "daily_status", lambda **_: _status(1, 90_000))
    monkeypatch.setattr(firecrawl_budget, "monthly_status", lambda **_: _status(1, 900))
    monkeypatch.setattr(tavily_budget, "monthly_status", lambda **_: _status(1, 900))

    update = FakeUpdate("/runwayapi", user_id=42)
    await telegram_bot._handle_runway_api(update, FakeContext())

    reply = update.message.replies[0]
    assert "CoinGecko" in reply and "indisponible" in reply
    assert "Mobula" in reply and "1/9500" in reply
