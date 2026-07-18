"""/api — inventaire des intégrations externes (commande admin, 18/07)."""
from __future__ import annotations

import pytest

from aria_core.gateway import telegram_bot
from aria_core.services.api_registry import ApiEntry


class FakeMessage:
    def __init__(self, text: str = "/api"):
        self.text = text
        self.replies: list[str] = []

    async def reply_text(self, text: str) -> None:
        self.replies.append(text)


class FakeUser:
    def __init__(self, user_id: int):
        self.id = user_id


class FakeUpdate:
    def __init__(self, user_id: int = 42):
        self.message = FakeMessage()
        self.effective_user = FakeUser(user_id)
        self.callback_query = None


class FakeContext:
    def __init__(self):
        self.args = []


def _configure_admin(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda uid: uid == 42)
    monkeypatch.setattr(telegram_bot.settings, "admin_ids", [42])


@pytest.mark.asyncio
async def test_api_command_rejects_non_admin(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda uid: False)
    monkeypatch.setattr(telegram_bot.settings, "admin_ids", [999])
    update = FakeUpdate(user_id=42)

    await telegram_bot._handle_api(update, FakeContext())

    assert len(update.message.replies) == 1
    assert "réservée" in update.message.replies[0] or "restrict" in update.message.replies[0].lower()


@pytest.mark.asyncio
async def test_api_command_sends_formatted_inventory(monkeypatch):
    _configure_admin(monkeypatch)

    async def fake_build_inventory():
        return [
            ApiEntry("TestAPI", "LLM", "https://test.example.com", True, note="ok"),
        ]

    monkeypatch.setattr(
        "aria_core.services.api_registry.build_api_inventory", fake_build_inventory,
    )
    update = FakeUpdate(user_id=42)

    await telegram_bot._handle_api(update, FakeContext())

    assert len(update.message.replies) >= 1
    combined = "\n".join(update.message.replies)
    assert "TestAPI" in combined
    assert "test.example.com" in combined


@pytest.mark.asyncio
async def test_api_command_registered_in_menu():
    """Non-régression : /api doit rester découvrable (menu bot), pas seulement
    fonctionnel si on connaît déjà la commande."""
    import inspect

    src = inspect.getsource(telegram_bot._register_bot_commands)
    assert '"api"' in src
