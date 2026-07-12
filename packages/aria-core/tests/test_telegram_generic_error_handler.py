"""Ticket #144, point 2/3 : quand aria_brain.process() lève (ex. le bug UnboundLocalError
du 12/07, avant correctif), le handler générique de _handle_message doit :
- logger la stack complète côté serveur (jamais affichée à l'opérateur) ;
- répondre un message sobre, sans le nom brut de la classe d'exception ni le texte
  cassé "kikou" (copie-coller d'un autre message d'erreur, sans rapport).
"""
from __future__ import annotations

import logging

import pytest

from aria_core.brain import aria_brain
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
    def __init__(self, text: str, user_id: int):
        self.message = FakeMessage(text)
        self.effective_user = FakeUser(user_id)
        self.callback_query = None


@pytest.mark.asyncio
async def test_generic_crash_logs_full_traceback_and_replies_sober_message(monkeypatch, caplog):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)

    async def failing_process(self, text, lang="fr", public_mode=None):
        raise UnboundLocalError("cannot access local variable 'wants_capability_improvement'")

    monkeypatch.setattr(type(aria_brain), "process", failing_process)

    update = FakeUpdate("donne moi ton avis", user_id=7)
    with caplog.at_level(logging.ERROR, logger="aria_core.gateway.telegram_bot"):
        await telegram_bot._handle_message(update, context=None)

    # Traceback complet loggé côté serveur.
    assert any("Telegram brain.process failed" in r.message for r in caplog.records)
    assert any(r.exc_info for r in caplog.records)

    # Réponse opérateur sobre : jamais le nom de l'exception, jamais "kikou".
    assert len(update.message.replies) == 1
    reply = update.message.replies[0]
    assert "UnboundLocalError" not in reply
    assert "kikou" not in reply
