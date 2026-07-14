"""Lecture directe d'une page web (13/07) -- câblage gateway/telegram_bot.py.
Vérifie : décline systématiquement pour un visiteur public (même posture que
vision_enabled()/photos), décline explicitement pour l'admin si le gate est
désactivé, répond via answer_from_page si activé. Même patron que
test_telegram_generic_error_handler.py (FakeMessage/FakeUser/FakeUpdate)."""
from __future__ import annotations

import pytest

from aria_core.gateway import telegram_bot


class FakeMessage:
    def __init__(self, text: str):
        self.text = text
        self.replies: list[str] = []
        self.chat_actions: list[str] = []

    async def reply_text(self, text: str) -> None:
        self.replies.append(text)

    async def reply_chat_action(self, action: str) -> None:
        self.chat_actions.append(action)


class FakeUser:
    def __init__(self, user_id: int):
        self.id = user_id


class FakeUpdate:
    def __init__(self, text: str, user_id: int):
        self.message = FakeMessage(text)
        self.effective_user = FakeUser(user_id)
        self.callback_query = None


# ── visiteur public ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_public_visitor_pasting_url_always_declines(monkeypatch):
    """Décline systématiquement, MÊME SI le gate ARIA_WEB_FETCH_ENABLED est activé --
    cette capacité reste admin-only, même posture que vision_enabled()/photos."""
    monkeypatch.setenv("ARIA_WEB_FETCH_ENABLED", "1")

    async def fail_if_called(*_a, **_k):
        raise AssertionError("answer_from_page ne doit jamais être appelé pour un visiteur public")

    monkeypatch.setattr("aria_core.knowledge.web_verify.answer_from_page", fail_if_called)

    update = FakeUpdate("regarde https://withluma.app c'est quoi ?", user_id=1)
    await telegram_bot._handle_public_message(update, update.message.text)

    assert len(update.message.replies) == 1
    assert "en dehors de l'équipe" in update.message.replies[0]


@pytest.mark.asyncio
async def test_public_visitor_without_url_not_intercepted(monkeypatch):
    """Un message public SANS URL ne doit jamais être intercepté par ce nouveau
    chemin -- passe par le pipeline normal (aria_brain.process), inchangé."""
    from aria_core.brain import aria_brain

    called = {"n": 0}

    async def fake_process(self, text, lang="fr", public_mode=None, visitor_id=None):
        called["n"] += 1

        class _R:
            reply = "réponse normale"

        return _R()

    monkeypatch.setattr(type(aria_brain), "process", fake_process)
    monkeypatch.setattr(
        telegram_bot, "check_rate_limit", lambda *_a, **_k: True,
    )

    update = FakeUpdate("bonjour, comment ça va ?", user_id=2)
    await telegram_bot._handle_public_message(update, update.message.text)

    assert called["n"] == 1
    assert update.message.replies == ["réponse normale"]


# ── admin ────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_admin_pasting_url_declines_explicitly_when_gate_off(monkeypatch):
    monkeypatch.delenv("ARIA_WEB_FETCH_ENABLED", raising=False)
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)

    async def fail_if_called(*_a, **_k):
        raise AssertionError("answer_from_page ne doit jamais être appelé si le gate est désactivé")

    monkeypatch.setattr("aria_core.knowledge.web_verify.answer_from_page", fail_if_called)

    update = FakeUpdate("regarde https://withluma.app c'est quoi ?", user_id=7)
    await telegram_bot._handle_message(update, context=None)

    assert len(update.message.replies) == 1
    # _reply() passe par _format_tg (strip markdown Telegram) -- les "_" du nom de
    # variable d'env sont interprétés comme de l'italique et disparaissent, comme
    # pour tout texte envoyé via ce canal (pas spécifique à ce message).
    assert "pas encore activée" in update.message.replies[0]
    assert "désactivé" in update.message.replies[0]


@pytest.mark.asyncio
async def test_admin_pasting_url_answers_when_gate_on(monkeypatch):
    monkeypatch.setenv("ARIA_WEB_FETCH_ENABLED", "1")
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)

    async def fake_answer(url, question, lang="fr", **_kw):
        assert url == "https://withluma.app"
        assert "withluma.app" in question
        return "Luma est un outil de gestion de tâches.", {"web_fetch": "ok", "source_url": url}

    monkeypatch.setattr("aria_core.knowledge.web_verify.answer_from_page", fake_answer)

    update = FakeUpdate("regarde https://withluma.app c'est quoi ?", user_id=7)
    await telegram_bot._handle_message(update, context=None)

    assert update.message.replies == ["Luma est un outil de gestion de tâches."]
    assert "typing" in update.message.chat_actions


@pytest.mark.asyncio
async def test_admin_pasting_url_honest_decline_when_unavailable(monkeypatch):
    monkeypatch.setenv("ARIA_WEB_FETCH_ENABLED", "1")
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)

    async def fake_answer(*_a, **_kw):
        return None, {"web_fetch": "unavailable"}

    monkeypatch.setattr("aria_core.knowledge.web_verify.answer_from_page", fake_answer)

    update = FakeUpdate("regarde https://blocked.example c'est quoi ?", user_id=7)
    await telegram_bot._handle_message(update, context=None)

    assert len(update.message.replies) == 1
    assert "je n'ai pas réussi" in update.message.replies[0].lower()
    # Jamais un contenu inventé en remplacement.
    assert "withluma" not in update.message.replies[0].lower()


@pytest.mark.asyncio
async def test_admin_message_without_url_not_intercepted(monkeypatch):
    """Sans URL dans le message, ce nouveau chemin ne doit jamais s'activer --
    laisse passer vers le pipeline admin normal, inchangé."""
    monkeypatch.setenv("ARIA_WEB_FETCH_ENABLED", "1")
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)

    async def fail_if_called(*_a, **_kw):
        raise AssertionError("answer_from_page ne doit jamais être appelé sans URL détectée")

    monkeypatch.setattr("aria_core.knowledge.web_verify.answer_from_page", fail_if_called)

    from aria_core.brain import aria_brain

    async def fake_process(self, text, lang="fr", public_mode=None):
        class _R:
            reply = "réponse normale"
            data = {}

        return _R()

    monkeypatch.setattr(type(aria_brain), "process", fake_process)

    update = FakeUpdate("comment vas-tu aujourd'hui ?", user_id=7)
    await telegram_bot._handle_message(update, context=None)

    assert update.message.replies == ["réponse normale"]
