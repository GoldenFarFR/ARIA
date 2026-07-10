"""/feuvert — scorecard argent réel, admin-only. Aucun réseau : compute_readiness_scorecard
est mockée (déjà testée en profondeur dans test_real_money_readiness.py)."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from aria_core.gateway import telegram_bot
from aria_core.skills.real_money_readiness import ReadinessCheck


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
async def test_readiness_rejects_non_admin(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: False)
    monkeypatch.setattr(telegram_bot.settings, "admin_ids", [999])
    scorecard_mock = AsyncMock()
    monkeypatch.setattr(
        "aria_core.skills.real_money_readiness.compute_readiness_scorecard", scorecard_mock,
    )

    update = FakeUpdate("/feuvert")
    await telegram_bot._handle_readiness(update, FakeContext())

    assert len(update.message.replies) == 1
    scorecard_mock.assert_not_called()


@pytest.mark.asyncio
async def test_readiness_admin_gets_full_report(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)
    scorecard = {
        "checks": [ReadinessCheck(id="sample_size", label="Échantillon", status="fail", detail="0/80")],
        "all_ok": False,
        "verdict": "NON — 0/8 cases cochées, argent réel toujours hors de portée.",
    }
    monkeypatch.setattr(
        "aria_core.skills.real_money_readiness.compute_readiness_scorecard",
        AsyncMock(return_value=scorecard),
    )

    update = FakeUpdate("/feuvert")
    await telegram_bot._handle_readiness(update, FakeContext())

    reply = update.message.replies[0]
    assert "NON" in reply
    assert "Échantillon" in reply
    assert "0/80" in reply
