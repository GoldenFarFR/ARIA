"""@claude — adressage explicite dans le chat Telegram opérateur/ARIA existant : le
message ne doit PAS déclencher le pipeline LLM d'ARIA (elle ne répond pas à la place de
Claude), juste un accusé de réception court. Le texte complet reste journalisé tel quel
dans le relais par process_webhook_update (inchangé) — Claude le lit à sa prochaine
lecture et répond, préfixé.
"""
from __future__ import annotations

import pytest

from aria_core.gateway import telegram_bot


class FakeMessage:
    def __init__(self, text: str):
        self.text = text
        self.replies: list[str] = []

    async def reply_text(self, text: str) -> None:
        self.replies.append(text)

    async def reply_chat_action(self, _action: str) -> None:
        pass


class FakeUser:
    def __init__(self, user_id: int):
        self.id = user_id


class FakeUpdate:
    def __init__(self, text: str, user_id: int = 42):
        self.message = FakeMessage(text)
        self.effective_user = FakeUser(user_id)
        self.callback_query = None


@pytest.mark.asyncio
async def test_at_claude_short_circuits_aria_pipeline(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)

    update = FakeUpdate("@claude peux-tu vérifier le déploiement Sepolia ?")
    await telegram_bot._handle_message(update, context=None)

    assert len(update.message.replies) == 1
    reply = update.message.replies[0]
    assert "claude" in reply.lower()
    assert "transmis" in reply.lower()


@pytest.mark.asyncio
async def test_at_claude_case_insensitive(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)

    update = FakeUpdate("@Claude des nouvelles ?")
    await telegram_bot._handle_message(update, context=None)

    assert len(update.message.replies) == 1
    assert "transmis" in update.message.replies[0].lower()


@pytest.mark.asyncio
async def test_at_claude_ignored_for_non_admin(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: False)

    async def fake_public(_update, _text):
        return None

    monkeypatch.setattr(telegram_bot, "_handle_public_message", fake_public)

    update = FakeUpdate("@claude coucou", user_id=999)
    await telegram_bot._handle_message(update, context=None)

    # Le non-admin ne déclenche jamais la logique @claude — chemin public inchangé,
    # aucune réponse écrite directement sur le message ici.
    assert update.message.replies == []
