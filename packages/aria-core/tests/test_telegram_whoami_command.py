"""/whoami — identité/rôle Telegram (#181, 15/07). Handler trouvé écrit mais
jamais câblé via add_handler (reliquat, cf. commit) -- câblé ce soir après
vérification qu'il apporte une vraie valeur distincte (seule voie pour un
visiteur non reconnu de récupérer son propre ID Telegram ; `/status` est
admin-only et ne l'aide pas, `/start` visiteur ne montre pas l'ID non plus)
et correction d'une fuite d'info trouvée au passage (la liste réelle des IDs
admin ne doit JAMAIS être renvoyée à un visiteur non reconnu)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

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


def test_whoami_registered_as_command_handler():
    app = MagicMock()
    telegram_bot._register_handlers(app)

    all_commands: set[str] = set()
    for call in app.add_handler.call_args_list:
        handler = call.args[0]
        commands = getattr(handler, "commands", None)
        if commands:
            all_commands |= set(commands)
    assert "whoami" in all_commands


@pytest.mark.asyncio
async def test_whoami_visitor_never_sees_real_admin_id_list(monkeypatch):
    # Correction #181 (15/07) : régression directe sur la fuite trouvée --
    # un visiteur non reconnu ne doit JAMAIS voir la vraie liste d'IDs admin.
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: False)
    monkeypatch.setattr(telegram_bot.settings, "admin_ids", [999888777])

    update = FakeUpdate("/whoami", user_id=123)
    await telegram_bot._handle_whoami(update, FakeContext())

    reply = update.message.replies[0]
    assert "999888777" not in reply
    assert "visiteur" in reply
    assert "123" in reply  # son propre ID, oui


@pytest.mark.asyncio
async def test_whoami_visitor_gets_own_id_and_no_crash(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: False)
    monkeypatch.setattr(telegram_bot.settings, "admin_ids", [])

    update = FakeUpdate("/whoami", user_id=555)
    await telegram_bot._handle_whoami(update, FakeContext())

    assert len(update.message.replies) == 1
    assert "555" in update.message.replies[0]


@pytest.mark.asyncio
async def test_whoami_admin_gets_full_identity_report(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)
    monkeypatch.setattr(telegram_bot.settings, "admin_ids", [42])
    monkeypatch.setattr(telegram_bot.settings, "debug", False)
    monkeypatch.setattr(
        "aria_core.skills.github_skill.github_configured", lambda: True,
    )
    monkeypatch.setattr(
        "aria_core.skills.github_skill.github_unlimited_access", lambda: True,
    )

    update = FakeUpdate("/whoami", user_id=42)
    await telegram_bot._handle_whoami(update, FakeContext())

    reply = update.message.replies[0]
    assert "OPÉRATEUR" in reply
    assert "42" in reply
    assert "accès illimité" in reply


@pytest.mark.asyncio
async def test_whoami_admin_still_sees_admin_ids_no_new_exposure(monkeypatch):
    # Un admin confirmé voyait déjà settings.admin_ids ailleurs (ex. /status
    # ne l'affiche pas mais l'admin y a accès par construction) -- garder ce
    # côté-là inchangé, seule la branche VISITEUR devait être corrigée.
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)
    monkeypatch.setattr(telegram_bot.settings, "admin_ids", [42, 43])
    monkeypatch.setattr(telegram_bot.settings, "debug", False)
    monkeypatch.setattr("aria_core.skills.github_skill.github_configured", lambda: False)
    monkeypatch.setattr("aria_core.skills.github_skill.github_unlimited_access", lambda: False)

    update = FakeUpdate("/whoami", user_id=42)
    await telegram_bot._handle_whoami(update, FakeContext())

    assert "[42, 43]" in update.message.replies[0]


@pytest.mark.asyncio
async def test_whoami_no_message_or_user_does_not_crash():
    class EmptyUpdate:
        message = None
        effective_user = None

    await telegram_bot._handle_whoami(EmptyUpdate(), FakeContext())  # ne doit pas lever
