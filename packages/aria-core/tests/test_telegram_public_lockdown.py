"""24/07 -- decision opérateur explicite ("verrouille aria") : la conversation
Telegram publique (visiteur non-admin -> aria_brain.process en public_mode) est
verrouillée par défaut, ahead of the real-capital "jour J". Vérifie le
comportement PAR DÉFAUT (gate absent) -- le comportement du chemin actif (gate
mis à "1") reste couvert par test_telegram_web_fetch.py/test_telegram_operator_lang.py,
réactivé explicitement là où c'est le sujet du test."""
from __future__ import annotations

import pytest

from aria_core.gateway import telegram_bot
from aria_core.narrative import telegram_visitor_start


class FakeMessage:
    def __init__(self, text: str = ""):
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


def test_public_conversation_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ARIA_TELEGRAM_PUBLIC_CONVERSATION_ENABLED", raising=False)
    assert telegram_bot._telegram_public_conversation_enabled() is False


def test_public_conversation_enabled_when_explicitly_set(monkeypatch):
    monkeypatch.setenv("ARIA_TELEGRAM_PUBLIC_CONVERSATION_ENABLED", "true")
    assert telegram_bot._telegram_public_conversation_enabled() is True


@pytest.mark.asyncio
async def test_non_admin_free_text_never_reaches_the_llm_by_default(monkeypatch):
    """Le vrai comportement demandé : plus aucun appel LLM pour un visiteur,
    même un message parfaitement anodin sans URL."""
    monkeypatch.delenv("ARIA_TELEGRAM_PUBLIC_CONVERSATION_ENABLED", raising=False)

    async def fail_if_called(*_a, **_kw):
        raise AssertionError("aria_brain.process ne doit jamais être appelé, bot verrouillé")

    from aria_core.brain import aria_brain

    monkeypatch.setattr(type(aria_brain), "process", fail_if_called)

    update = FakeUpdate("bonjour, comment ça va ?", user_id=123456)
    await telegram_bot._handle_message(update, context=None)

    assert len(update.message.replies) == 1
    assert "réservé à l'équipe" in update.message.replies[0]


@pytest.mark.asyncio
async def test_non_admin_url_message_also_locked_not_the_web_fetch_decline(monkeypatch):
    """Même un message contenant une URL reçoit le message de verrouillage
    générique -- pas le message spécifique web-fetch (qui suppose la
    conversation active), le gate est vérifié en premier."""
    monkeypatch.delenv("ARIA_TELEGRAM_PUBLIC_CONVERSATION_ENABLED", raising=False)
    update = FakeUpdate("regarde https://withluma.app c'est quoi ?", user_id=7)
    await telegram_bot._handle_public_message(update, update.message.text)

    assert len(update.message.replies) == 1
    assert "réservé à l'équipe" in update.message.replies[0]
    assert "en dehors de l'équipe" not in update.message.replies[0]


@pytest.mark.asyncio
async def test_admin_conversation_never_affected_by_this_gate(monkeypatch):
    """Le gate ne s'applique qu'au chemin PUBLIC (_handle_public_message) --
    un admin garde sa vraie conversation LLM même avec ce gate désactivé
    (verrouillage par défaut)."""
    monkeypatch.delenv("ARIA_TELEGRAM_PUBLIC_CONVERSATION_ENABLED", raising=False)
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)

    from aria_core.brain import aria_brain

    async def fake_process(self, text, lang="fr", public_mode=None):
        class _R:
            reply = "réponse admin normale"
            data = {}

        return _R()

    monkeypatch.setattr(type(aria_brain), "process", fake_process)

    update = FakeUpdate("comment vas-tu aujourd'hui ?", user_id=7)
    await telegram_bot._handle_message(update, context=None)

    assert update.message.replies == ["réponse admin normale"]


def test_visitor_welcome_no_longer_promises_a_conversation():
    msg = telegram_visitor_start("https://ariavanguardzhc.com", "l'équipe", "t.me/Aria_ZHC_Bot")
    assert "pose une question" not in msg.lower()
    assert "mode public" not in msg.lower()
    assert "ariavanguardzhc.com" in msg
