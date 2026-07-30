"""/add <adresse1> [adresse2 ...] [chaîne] — commande Telegram (30/07,
injection manuelle d'un contrat repéré par l'opérateur dans la file de
découverte momentum). N'appelle jamais discover_momentum_candidates
directement -- vérifie seulement que manual_candidates.add_manual_candidate
est appelé avec les bonnes adresses/chaîne, et le formatage de la réponse.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from aria_core.gateway import telegram_bot

ADDR_A = "0x" + "a" * 40
ADDR_B = "0x" + "b" * 40


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


class FakeContext:
    def __init__(self, args: list[str] | None = None):
        self.args = args or []


@pytest.mark.asyncio
async def test_add_rejects_non_admin(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: False)
    monkeypatch.setattr(telegram_bot.settings, "admin_ids", [999])
    add_mock = AsyncMock()
    monkeypatch.setattr("aria_core.manual_candidates.add_manual_candidate", add_mock)

    update = FakeUpdate(f"/add {ADDR_A}")
    await telegram_bot._handle_add(update, FakeContext())

    assert len(update.message.replies) == 1
    add_mock.assert_not_called()


@pytest.mark.asyncio
async def test_add_rejects_missing_body(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)
    add_mock = AsyncMock()
    monkeypatch.setattr("aria_core.manual_candidates.add_manual_candidate", add_mock)

    update = FakeUpdate("/add")
    await telegram_bot._handle_add(update, FakeContext())

    assert len(update.message.replies) == 1
    assert "usage" in update.message.replies[0].lower()
    add_mock.assert_not_called()


@pytest.mark.asyncio
async def test_add_rejects_no_valid_address(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)
    add_mock = AsyncMock()
    monkeypatch.setattr("aria_core.manual_candidates.add_manual_candidate", add_mock)

    update = FakeUpdate("/add pasunetoken solana")
    await telegram_bot._handle_add(update, FakeContext())

    assert len(update.message.replies) == 1
    assert "aucune adresse valide" in update.message.replies[0].lower()
    add_mock.assert_not_called()


@pytest.mark.asyncio
async def test_add_single_address_defaults_to_base(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)
    add_mock = AsyncMock(return_value=True)
    monkeypatch.setattr("aria_core.manual_candidates.add_manual_candidate", add_mock)

    update = FakeUpdate(f"/add {ADDR_A}")
    await telegram_bot._handle_add(update, FakeContext())

    add_mock.assert_awaited_once_with(ADDR_A, "base")
    assert "1/1" in update.message.replies[0]
    assert "base" in update.message.replies[0]


@pytest.mark.asyncio
async def test_add_multiple_addresses_with_trailing_chain(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)
    add_mock = AsyncMock(return_value=True)
    monkeypatch.setattr("aria_core.manual_candidates.add_manual_candidate", add_mock)

    update = FakeUpdate(f"/add {ADDR_A} {ADDR_B} solana")
    await telegram_bot._handle_add(update, FakeContext())

    assert add_mock.await_count == 2
    add_mock.assert_any_await(ADDR_A, "solana")
    add_mock.assert_any_await(ADDR_B, "solana")
    assert "2/2" in update.message.replies[0]
    assert "solana" in update.message.replies[0]


@pytest.mark.asyncio
async def test_add_via_context_args(monkeypatch):
    """/add via CommandHandler args (context.args) plutôt que le texte brut."""
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)
    add_mock = AsyncMock(return_value=True)
    monkeypatch.setattr("aria_core.manual_candidates.add_manual_candidate", add_mock)

    update = FakeUpdate("/add")
    await telegram_bot._handle_add(update, FakeContext(args=[ADDR_A]))

    add_mock.assert_awaited_once_with(ADDR_A, "base")
    assert "1/1" in update.message.replies[0]


@pytest.mark.asyncio
async def test_add_reports_partial_success(monkeypatch):
    """Une adresse déjà en file (add_manual_candidate renvoie False sur
    contrat/chaîne vide) ne bloque pas les autres — le compte reflète le
    résultat réel de chaque appel."""
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)
    add_mock = AsyncMock(side_effect=[True, False])
    monkeypatch.setattr("aria_core.manual_candidates.add_manual_candidate", add_mock)

    update = FakeUpdate(f"/add {ADDR_A} {ADDR_B}")
    await telegram_bot._handle_add(update, FakeContext())

    assert "1/2" in update.message.replies[0]
