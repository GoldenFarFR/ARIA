"""/directive — surface de contrôle Telegram du canal ARIA -> Claude Code (pilote).

DB + marqueur de coupe-circuit isolés, gate ON pour tester le dépôt. Vérifie la
restriction admin, le dépôt/refus par périmètre, le coupe-circuit, la lecture.
"""
from __future__ import annotations

import pytest

from aria_core import aria_directives as ad
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


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: True)
    monkeypatch.setattr(ad, "DB_PATH", str(tmp_path / "directives.db"))
    monkeypatch.setattr(ad, "data_dir", lambda: tmp_path)
    monkeypatch.setenv("ARIA_DIRECTIVE_CHANNEL_ENABLED", "1")
    yield


@pytest.mark.asyncio
async def test_directive_rejects_non_admin(monkeypatch):
    monkeypatch.setattr(telegram_bot, "is_admin", lambda _uid: False)
    monkeypatch.setattr(telegram_bot.settings, "admin_ids", [999])
    update = FakeUpdate("/directive list")
    await telegram_bot._handle_directive(update, FakeContext())
    reply = update.message.replies[0].lower()
    assert "restricted" in reply or "admin" in reply


@pytest.mark.asyncio
async def test_directive_propose_in_perimeter_enters_queue():
    update = FakeUpdate("/directive propose docs mettre a jour un doc")
    await telegram_bot._handle_directive(update, FakeContext())
    assert "déposée" in update.message.replies[0]
    assert len(await ad.list_directives(status="pending")) == 1


@pytest.mark.asyncio
async def test_directive_propose_out_of_perimeter_refused():
    update = FakeUpdate("/directive propose wallet signer une transaction")
    await telegram_bot._handle_directive(update, FakeContext())
    assert "Refusée" in update.message.replies[0]
    assert await ad.list_directives() == []


@pytest.mark.asyncio
async def test_directive_halt_then_resume():
    up_halt = FakeUpdate("/directive halt raison test")
    await telegram_bot._handle_directive(up_halt, FakeContext())
    assert "FIGÉ" in up_halt.message.replies[0]
    assert ad.is_halted() is True

    up_resume = FakeUpdate("/directive resume")
    await telegram_bot._handle_directive(up_resume, FakeContext())
    assert ad.is_halted() is False


@pytest.mark.asyncio
async def test_directive_list_shows_queue_and_gate_state():
    await ad.propose_directive("backlog", "une tache")
    update = FakeUpdate("/directive list")
    await telegram_bot._handle_directive(update, FakeContext())
    reply = update.message.replies[0]
    assert "une tache" in reply
    assert "backlog" in reply


@pytest.mark.asyncio
async def test_directive_log_shows_events():
    await ad.propose_directive("docs", "doc A")
    update = FakeUpdate("/directive log")
    await telegram_bot._handle_directive(update, FakeContext())
    assert "proposed" in update.message.replies[0]
