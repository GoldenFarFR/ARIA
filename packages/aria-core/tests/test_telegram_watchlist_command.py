"""/watchlist [n] — checklist des contrats qu'ARIA suit de près (pool screené classé).

Aucun réseau : top_candidates est mocké. Vérifie restriction admin, pool vide,
bornes n, et le formatage (classement, jamais un ordre).
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from aria_core.gateway import telegram_bot
from aria_core.skills.candidate_ranking import RankedCandidate

A = "0x" + "a" * 40
B = "0x" + "b" * 40


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
async def test_watchlist_rejects_non_admin(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: False)
    monkeypatch.setattr(telegram_bot.settings, "admin_ids", [999])
    top_mock = AsyncMock()
    monkeypatch.setattr("aria_core.skills.candidate_ranking.top_candidates", top_mock)

    update = FakeUpdate("/watchlist")
    await telegram_bot._handle_watchlist(update, FakeContext())

    assert len(update.message.replies) == 1
    top_mock.assert_not_called()


@pytest.mark.asyncio
async def test_watchlist_empty_pool(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)
    monkeypatch.setattr("aria_core.skills.candidate_ranking.top_candidates", AsyncMock(return_value=[]))

    update = FakeUpdate("/watchlist")
    await telegram_bot._handle_watchlist(update, FakeContext())

    assert "vide" in update.message.replies[0].lower()


@pytest.mark.asyncio
async def test_watchlist_lists_ranked_candidates(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)
    ranked = [
        RankedCandidate(contract=A, symbol="ZHC", rank_score=88.5, security_score=80,
                         liquidity_usd=120_000.0, top_holder_pct=8.0, verdict="SAFE"),
        RankedCandidate(contract=B, symbol="BLD", rank_score=61.2, security_score=55,
                         liquidity_usd=45_000.0, top_holder_pct=22.0, verdict="CAUTION"),
    ]
    top_mock = AsyncMock(return_value=ranked)
    monkeypatch.setattr("aria_core.skills.candidate_ranking.top_candidates", top_mock)

    update = FakeUpdate("/watchlist")
    await telegram_bot._handle_watchlist(update, FakeContext())

    top_mock.assert_awaited_once_with(10)
    reply = update.message.replies[0]
    assert "ZHC" in reply and "BLD" in reply
    assert A in reply and B in reply
    assert "88" in reply and "SAFE" in reply
    assert "jamais un ordre" in reply.lower()


@pytest.mark.asyncio
async def test_watchlist_custom_n_bounded(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)
    top_mock = AsyncMock(return_value=[])
    monkeypatch.setattr("aria_core.skills.candidate_ranking.top_candidates", top_mock)

    update = FakeUpdate("/watchlist 500")
    await telegram_bot._handle_watchlist(update, FakeContext())

    top_mock.assert_awaited_once_with(30)  # borné au plafond


@pytest.mark.asyncio
async def test_watchlist_non_numeric_arg_defaults_to_ten(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)
    top_mock = AsyncMock(return_value=[])
    monkeypatch.setattr("aria_core.skills.candidate_ranking.top_candidates", top_mock)

    update = FakeUpdate("/watchlist abc")
    await telegram_bot._handle_watchlist(update, FakeContext())

    top_mock.assert_awaited_once_with(10)
