"""/scan <adresse> — commande Telegram lecture seule (DexScreener + Blockscout).

Aucun appel réseau réel : scan_base_token est entièrement mocké. Vérifie la
validation d'adresse, la restriction admin, et le formatage de la réponse.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from aria_core.gateway import telegram_bot
from aria_core.skills.acp_onchain_scan import TokenScanContext

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


@pytest.mark.asyncio
async def test_scan_rejects_non_admin(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: False)
    monkeypatch.setattr(telegram_bot.settings, "admin_ids", [999])
    scan_mock = AsyncMock()
    monkeypatch.setattr("aria_core.skills.acp_onchain_scan.scan_base_token", scan_mock)

    update = FakeUpdate(f"/scan {ADDR}")
    await telegram_bot._handle_scan(update, FakeContext())

    assert len(update.message.replies) == 1
    assert "restricted" in update.message.replies[0].lower() or "admin" in update.message.replies[0].lower()
    scan_mock.assert_not_called()


@pytest.mark.asyncio
async def test_scan_rejects_invalid_address(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)
    scan_mock = AsyncMock()
    monkeypatch.setattr("aria_core.skills.acp_onchain_scan.scan_base_token", scan_mock)

    update = FakeUpdate("/scan not-an-address")
    await telegram_bot._handle_scan(update, FakeContext())

    assert len(update.message.replies) == 1
    assert "invalide" in update.message.replies[0].lower()
    scan_mock.assert_not_called()


@pytest.mark.asyncio
async def test_scan_rejects_missing_address(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)
    scan_mock = AsyncMock()
    monkeypatch.setattr("aria_core.skills.acp_onchain_scan.scan_base_token", scan_mock)

    update = FakeUpdate("/scan")
    await telegram_bot._handle_scan(update, FakeContext())

    assert len(update.message.replies) == 1
    assert "usage" in update.message.replies[0].lower()
    scan_mock.assert_not_called()


@pytest.mark.asyncio
async def test_scan_valid_address_calls_scan_and_formats_reply(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)

    fake_ctx = TokenScanContext(
        contract=ADDR,
        valid_address=True,
        pairs_found=1,
        security_score=42,
        lite_verdict="CAUTION",
        data_source="dexscreener",
        risk_flags=["Liquidité modérée — size prudente recommandée.", "Fonction mint détectée."],
    )
    scan_mock = AsyncMock(return_value=fake_ctx)
    monkeypatch.setattr("aria_core.skills.acp_onchain_scan.scan_base_token", scan_mock)

    update = FakeUpdate(f"/scan {ADDR}")
    await telegram_bot._handle_scan(update, FakeContext())

    scan_mock.assert_awaited_once_with(ADDR)
    assert len(update.message.replies) == 1
    reply = update.message.replies[0]
    assert "42" in reply
    assert "CAUTION" in reply
    assert "mint" in reply.lower()


@pytest.mark.asyncio
async def test_scan_valid_address_via_context_args(monkeypatch):
    """/scan via CommandHandler args (context.args) plutôt que le texte brut."""
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)

    fake_ctx = TokenScanContext(
        contract=ADDR,
        valid_address=True,
        security_score=70,
        lite_verdict="SAFE",
        data_source="dexscreener",
        risk_flags=[],
    )
    scan_mock = AsyncMock(return_value=fake_ctx)
    monkeypatch.setattr("aria_core.skills.acp_onchain_scan.scan_base_token", scan_mock)

    update = FakeUpdate("/scan")
    await telegram_bot._handle_scan(update, FakeContext(args=[ADDR]))

    scan_mock.assert_awaited_once_with(ADDR)
    assert "SAFE" in update.message.replies[0]
    assert "Aucun flag" in update.message.replies[0]
