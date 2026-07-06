"""/these, /issue, /theses — commandes Telegram de la boucle mémoire d'investissement.

Aucun accès DB réel : investment_memory est mocké. Vérifie la restriction admin,
la validation des arguments, et le formatage des réponses.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from aria_core import investment_memory
from aria_core.gateway import telegram_bot

ADDR = "0x" + "a" * 40


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


@pytest.fixture(autouse=True)
def _admin(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)


# ----------------------------- /these -----------------------------


@pytest.mark.asyncio
async def test_thesis_rejects_non_admin(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: False)
    monkeypatch.setattr(telegram_bot.settings, "admin_ids", [999])
    record = AsyncMock()
    monkeypatch.setattr(investment_memory, "record_thesis", record)

    update = FakeUpdate(f"/these {ADDR} WATCH some thesis")
    await telegram_bot._handle_thesis(update, FakeContext())

    record.assert_not_called()
    assert "restricted" in update.message.replies[0].lower() or "admin" in update.message.replies[0].lower()


@pytest.mark.asyncio
async def test_thesis_missing_parts_shows_usage(monkeypatch):
    record = AsyncMock()
    monkeypatch.setattr(investment_memory, "record_thesis", record)

    update = FakeUpdate(f"/these {ADDR} WATCH")  # pas de texte de thèse
    await telegram_bot._handle_thesis(update, FakeContext())

    record.assert_not_called()
    assert "usage" in update.message.replies[0].lower()


@pytest.mark.asyncio
async def test_thesis_invalid_address(monkeypatch):
    record = AsyncMock()
    monkeypatch.setattr(investment_memory, "record_thesis", record)

    update = FakeUpdate("/these not-an-addr WATCH holders solides")
    await telegram_bot._handle_thesis(update, FakeContext())

    record.assert_not_called()
    assert "invalide" in update.message.replies[0].lower()


@pytest.mark.asyncio
async def test_thesis_invalid_decision(monkeypatch):
    record = AsyncMock()
    monkeypatch.setattr(investment_memory, "record_thesis", record)

    update = FakeUpdate(f"/these {ADDR} MOON holders solides")
    await telegram_bot._handle_thesis(update, FakeContext())

    record.assert_not_called()
    assert "décision invalide" in update.message.replies[0].lower()


@pytest.mark.asyncio
async def test_thesis_records_and_confirms(monkeypatch):
    record = AsyncMock(return_value=7)
    monkeypatch.setattr(investment_memory, "record_thesis", record)

    update = FakeUpdate(f"/these {ADDR} watch holders solides, liquidité faible")
    await telegram_bot._handle_thesis(update, FakeContext())

    record.assert_awaited_once_with(
        token_address=ADDR, thesis="holders solides, liquidité faible", decision="WATCH"
    )
    reply = update.message.replies[0]
    assert "#7" in reply
    assert "WATCH" in reply


# ----------------------------- /issue -----------------------------


@pytest.mark.asyncio
async def test_issue_missing_args_shows_usage(monkeypatch):
    close = AsyncMock()
    monkeypatch.setattr(investment_memory, "close_thesis", close)

    update = FakeUpdate("/issue 3")  # pas de résultat
    await telegram_bot._handle_issue(update, FakeContext())

    close.assert_not_called()
    assert "usage" in update.message.replies[0].lower()


@pytest.mark.asyncio
async def test_issue_splits_outcome_and_lesson(monkeypatch):
    close = AsyncMock(return_value={"id": 3, "decision": "BUY", "token_address": ADDR})
    monkeypatch.setattr(investment_memory, "close_thesis", close)

    update = FakeUpdate("/issue 3 +18% en 2 semaines | catalyseur listing sous-estimé")
    await telegram_bot._handle_issue(update, FakeContext())

    close.assert_awaited_once_with(
        3, outcome="+18% en 2 semaines", lesson="catalyseur listing sous-estimé"
    )
    reply = update.message.replies[0]
    assert "#3" in reply
    assert "Leçon" in reply


@pytest.mark.asyncio
async def test_issue_without_separator_uses_whole_as_outcome(monkeypatch):
    close = AsyncMock(return_value={"id": 5, "decision": "SELL", "token_address": ADDR})
    monkeypatch.setattr(investment_memory, "close_thesis", close)

    update = FakeUpdate("/issue 5 sorti à breakeven")
    await telegram_bot._handle_issue(update, FakeContext())

    close.assert_awaited_once_with(5, outcome="sorti à breakeven", lesson="")


@pytest.mark.asyncio
async def test_issue_unknown_thesis_reports_not_found(monkeypatch):
    close = AsyncMock(return_value=None)
    monkeypatch.setattr(investment_memory, "close_thesis", close)

    update = FakeUpdate("/issue 99 whatever")
    await telegram_bot._handle_issue(update, FakeContext())

    assert "introuvable" in update.message.replies[0].lower()


# ----------------------------- /theses -----------------------------


@pytest.mark.asyncio
async def test_theses_empty(monkeypatch):
    monkeypatch.setattr(investment_memory, "list_open_theses", AsyncMock(return_value=[]))

    update = FakeUpdate("/theses")
    await telegram_bot._handle_theses(update, FakeContext())

    assert "aucune thèse ouverte" in update.message.replies[0].lower()


@pytest.mark.asyncio
async def test_theses_lists_open(monkeypatch):
    monkeypatch.setattr(
        investment_memory,
        "list_open_theses",
        AsyncMock(
            return_value=[
                {"id": 2, "decision": "BUY", "token_address": ADDR, "thesis": "liquidité en hausse"},
                {"id": 1, "decision": "WATCH", "token_address": ADDR, "thesis": "holders solides"},
            ]
        ),
    )

    update = FakeUpdate("/theses")
    await telegram_bot._handle_theses(update, FakeContext())

    reply = update.message.replies[0]
    assert "#2" in reply
    assert "#1" in reply
    assert "liquidité en hausse" in reply
