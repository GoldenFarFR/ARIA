"""« exchanges »/« échanges » -- commande Telegram admin listant le registre
d'échanges JUNO (format_exchanges_list était écrite mais jamais branchée à
aucune commande -- seuls mark_published/record_reply, qui MODIFIENT une
entrée, étaient câblés ; il n'y avait aucun moyen de LIRE le registre)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from aria_core.exchanges import AgentExchange, ExchangeStatus
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


def _exchange(exchange_id: str) -> AgentExchange:
    now = datetime.now(timezone.utc)
    return AgentExchange(
        id=exchange_id,
        target_agent="JUNO@ZHC",
        channel="x_telegram_manual",
        status=ExchangeStatus.AWAITING_REPLY,
        message_body="hello JUNO",
        message_json="{}",
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_exchanges_command_lists_registry(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)

    async def fake_get_all(limit: int = 20):
        return [_exchange("ex1")]

    monkeypatch.setattr(telegram_bot, "get_all_exchanges", fake_get_all)

    update = FakeUpdate("exchanges")
    await telegram_bot._handle_message(update, context=None)

    assert len(update.message.replies) == 1
    reply = update.message.replies[0]
    assert "#ex1" in reply
    assert "JUNO@ZHC" in reply


@pytest.mark.asyncio
async def test_echanges_french_variant_also_works(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)

    async def fake_get_all(limit: int = 20):
        return []

    monkeypatch.setattr(telegram_bot, "get_all_exchanges", fake_get_all)

    update = FakeUpdate("échanges")
    await telegram_bot._handle_message(update, context=None)

    assert len(update.message.replies) == 1
    assert "no agent exchanges" in update.message.replies[0].lower()


@pytest.mark.asyncio
async def test_exchanges_command_ignored_for_non_admin(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: False)

    async def fake_public(_update, _text):
        return None

    monkeypatch.setattr(telegram_bot, "_handle_public_message", fake_public)

    get_all_mock_called = False

    async def fake_get_all(limit: int = 20):
        nonlocal get_all_mock_called
        get_all_mock_called = True
        return []

    monkeypatch.setattr(telegram_bot, "get_all_exchanges", fake_get_all)

    update = FakeUpdate("exchanges", user_id=999)
    await telegram_bot._handle_message(update, context=None)

    assert update.message.replies == []
    assert get_all_mock_called is False
