"""Incident réel (12/07) : vérifie que le vrai chemin Telegram opérateur (_handle_message)
utilise bien detect_operator_lang() (jamais de repli anglais), et que le chemin public
(_handle_public_message) reste inchangé sur le même message ambigu -- pas juste un test
de la fonction locale.py isolée, mais du câblage réel.
"""
from __future__ import annotations

import pytest

from aria_core.brain import aria_brain
from aria_core.gateway import telegram_bot
from aria_core.models import ChatResponse

INCIDENT_TEXT = "tu a scanner de nouveau projet qui t'interresse ?"


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


@pytest.mark.asyncio
async def test_operator_path_never_passes_english_lang_on_ambiguous_message(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)
    seen = {}

    async def fake_process(self, text, lang="fr", public_mode=None):
        seen["lang"] = lang
        return ChatResponse(reply="ok", skill_used=None, actions_taken=[], data={})

    monkeypatch.setattr(type(aria_brain), "process", fake_process)

    update = FakeUpdate(INCIDENT_TEXT, user_id=7)
    await telegram_bot._handle_message(update, context=None)

    assert seen["lang"] == "fr"


@pytest.mark.asyncio
async def test_public_path_unchanged_on_same_ambiguous_message(monkeypatch):
    seen = {}

    async def fake_process(self, text, lang="fr", public_mode=None, visitor_id=""):
        seen["lang"] = lang
        return ChatResponse(reply="ok", skill_used=None, actions_taken=[], data={})

    monkeypatch.setattr(type(aria_brain), "process", fake_process)

    update = FakeUpdate(INCIDENT_TEXT, user_id=999)
    await telegram_bot._handle_public_message(update, INCIDENT_TEXT)

    # Chemin public inchangé -- hors scope de ce correctif, retombe toujours sur l'anglais.
    assert seen["lang"] == "en"
