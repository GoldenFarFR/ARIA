"""06/08 -- operator request ("un court message qui dit XXX$ dépensé"): the
LLM cost line must appear on a Telegram reply ONLY when (1) the turn actually
had a known cost AND (2) the sender is the owner -- never a mere admin,
never the public. Same posture and same test shape as the fallback notice
(test_telegram_fallback_notice.py)."""
from __future__ import annotations

import pytest

from aria_core.brain import aria_brain
from aria_core.gateway import telegram_bot
from aria_core.models import ChatResponse


class FakeMessage:
    def __init__(self, text: str):
        self.text = text
        self.replies: list[str] = []

    async def reply_text(self, text: str, **kwargs) -> None:
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


def _fake_process(*, cost_usd: float | None, unknown: bool = False):
    async def _process(self, text, lang="fr", public_mode=None):
        data = {}
        if cost_usd is not None or unknown:
            data["llm_turn_cost_usd"] = cost_usd or 0.0
            data["llm_turn_cost_unknown"] = unknown
        return ChatResponse(reply="analyse terminée", skill_used=None, actions_taken=[], data=data)

    return _process


@pytest.mark.asyncio
async def test_owner_sees_cost_line(monkeypatch, tmp_path):
    from aria_core.testing import configure_test_runtime

    configure_test_runtime(data_dir=tmp_path)
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)
    monkeypatch.setattr(telegram_bot, "is_owner", lambda uid: uid == 7)
    monkeypatch.setattr(type(aria_brain), "process", _fake_process(cost_usd=0.0123))

    update = FakeUpdate("comment on gère VaultX ?", user_id=7)
    await telegram_bot._handle_message(update, context=None)

    assert len(update.message.replies) == 1
    reply = update.message.replies[0]
    assert "analyse terminée" in reply
    assert "0.01230$" in reply
    assert "cumul mois" in reply


@pytest.mark.asyncio
async def test_non_owner_admin_never_sees_cost_line(monkeypatch, tmp_path):
    from aria_core.testing import configure_test_runtime

    configure_test_runtime(data_dir=tmp_path)
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)
    monkeypatch.setattr(telegram_bot, "is_owner", lambda uid: uid == 7)
    monkeypatch.setattr(type(aria_brain), "process", _fake_process(cost_usd=0.0123))

    update = FakeUpdate("comment on gère VaultX ?", user_id=999)
    await telegram_bot._handle_message(update, context=None)

    assert len(update.message.replies) == 1
    assert update.message.replies[0] == "analyse terminée"


@pytest.mark.asyncio
async def test_owner_sees_no_line_when_no_llm_call_happened(monkeypatch, tmp_path):
    from aria_core.testing import configure_test_runtime

    configure_test_runtime(data_dir=tmp_path)
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)
    monkeypatch.setattr(telegram_bot, "is_owner", lambda uid: uid == 7)
    monkeypatch.setattr(type(aria_brain), "process", _fake_process(cost_usd=None))

    update = FakeUpdate("comment on gère VaultX ?", user_id=7)
    await telegram_bot._handle_message(update, context=None)

    assert len(update.message.replies) == 1
    assert update.message.replies[0] == "analyse terminée"


@pytest.mark.asyncio
async def test_owner_sees_unknown_cost_note_without_a_dollar_figure(monkeypatch, tmp_path):
    from aria_core.testing import configure_test_runtime

    configure_test_runtime(data_dir=tmp_path)
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)
    monkeypatch.setattr(telegram_bot, "is_owner", lambda uid: uid == 7)
    monkeypatch.setattr(type(aria_brain), "process", _fake_process(cost_usd=0.0, unknown=True))

    update = FakeUpdate("comment on gère VaultX ?", user_id=7)
    await telegram_bot._handle_message(update, context=None)

    reply = update.message.replies[0]
    assert "prix inconnu" in reply
