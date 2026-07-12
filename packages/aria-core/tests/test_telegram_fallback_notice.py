"""#135 : le signal de fallback LLM (Groq/xAI au lieu de Spark) doit apparaître dans la
réponse Telegram UNIQUEMENT quand (1) le tour a réellement basculé sur le fallback ET
(2) l'expéditeur est le propriétaire (owner_chat_id) -- jamais un simple admin, jamais
le public, et silence total si Spark a répondu normalement.
"""
from __future__ import annotations

import pytest

from aria_core.brain import aria_brain
from aria_core.gateway import telegram_bot
from aria_core.models import ChatResponse


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
    def __init__(self, text: str, user_id: int):
        self.message = FakeMessage(text)
        self.effective_user = FakeUser(user_id)
        self.callback_query = None


def _fake_process(fallback: bool):
    async def _process(self, text, lang="fr", public_mode=None):
        data = {}
        if fallback:
            data["llm_fallback_used"] = True
            data["llm_fallback_provider"] = "groq"
        return ChatResponse(reply="analyse terminée", skill_used=None, actions_taken=[], data=data)

    return _process


@pytest.mark.asyncio
async def test_owner_sees_notice_when_fallback_used(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)
    monkeypatch.setattr(telegram_bot, "is_owner", lambda uid: uid == 7)
    monkeypatch.setattr(type(aria_brain), "process", _fake_process(fallback=True))

    update = FakeUpdate("comment on gère VaultX ?", user_id=7)
    await telegram_bot._handle_message(update, context=None)

    assert len(update.message.replies) == 1
    reply = update.message.replies[0]
    assert "analyse terminée" in reply
    assert "Groq" in reply
    assert "fallback" in reply.lower()


@pytest.mark.asyncio
async def test_non_owner_admin_does_not_see_notice_even_with_fallback(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)
    monkeypatch.setattr(telegram_bot, "is_owner", lambda uid: uid == 7)
    monkeypatch.setattr(type(aria_brain), "process", _fake_process(fallback=True))

    update = FakeUpdate("comment on gère VaultX ?", user_id=999)
    await telegram_bot._handle_message(update, context=None)

    assert len(update.message.replies) == 1
    reply = update.message.replies[0]
    assert reply == "analyse terminée"
    assert "Groq" not in reply
    assert "fallback" not in reply.lower()


@pytest.mark.asyncio
async def test_owner_sees_no_notice_when_primary_succeeded(monkeypatch):
    """Silence total : Spark actif = aucun bandeau, réponse strictement inchangée."""
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)
    monkeypatch.setattr(telegram_bot, "is_owner", lambda uid: uid == 7)
    monkeypatch.setattr(type(aria_brain), "process", _fake_process(fallback=False))

    update = FakeUpdate("comment on gère VaultX ?", user_id=7)
    await telegram_bot._handle_message(update, context=None)

    assert len(update.message.replies) == 1
    assert update.message.replies[0] == "analyse terminée"
