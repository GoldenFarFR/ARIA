"""Sujet ("topic") Telegram dédié au suivi paper-trading (#197, 15/07) -- en plus du
DM admin habituel, jamais à la place. Aucun appel réseau réel : ``telegram_bot._bot_app``
est mocké, ``send_message`` est exercé directement pour vérifier le threading de
``message_thread_id``."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from aria_core import heartbeat
from aria_core.gateway import telegram_bot
from aria_core.runtime import settings


@pytest.fixture(autouse=True)
def _reset_topic_config(monkeypatch):
    """Isole chaque test -- sans ça, la config du topic (mutable sur l'objet settings
    partagé) fuirait d'un test à l'autre."""
    monkeypatch.setattr(settings, "aria_trading_topic_chat_id", None, raising=False)
    monkeypatch.setattr(settings, "aria_trading_topic_thread_id", None, raising=False)


class FakeBot:
    def __init__(self):
        self.calls = []

    async def send_message(self, *, chat_id, text, message_thread_id=None):
        self.calls.append({"chat_id": chat_id, "text": text, "message_thread_id": message_thread_id})


class FakeApp:
    def __init__(self):
        self.bot = FakeBot()


# ── send_message : threading de message_thread_id ──────────────────────────


@pytest.mark.asyncio
async def test_send_message_threads_message_thread_id(monkeypatch):
    monkeypatch.setattr(telegram_bot, "_bot_app", FakeApp())
    monkeypatch.setattr(settings, "telegram_bot_token", "x", raising=False)

    ok = await telegram_bot.send_message("hello", chat_id=-100123, message_thread_id=67)

    assert ok is True
    assert telegram_bot._bot_app.bot.calls == [
        {"chat_id": -100123, "text": "hello", "message_thread_id": 67}
    ]


@pytest.mark.asyncio
async def test_send_message_default_thread_id_none_no_regression(monkeypatch):
    """Appel sans message_thread_id (les 20+ appelants existants) -- comportement
    identique à avant ce chantier : None passé tel quel, poste à la racine du chat."""
    monkeypatch.setattr(telegram_bot, "_bot_app", FakeApp())
    monkeypatch.setattr(settings, "telegram_bot_token", "x", raising=False)

    await telegram_bot.send_message("hello", chat_id=-100123)

    assert telegram_bot._bot_app.bot.calls[0]["message_thread_id"] is None


# ── _notify_telegram_trading : DM + topic, dégradation douce ───────────────


@pytest.mark.asyncio
async def test_no_topic_config_dm_only_no_regression(monkeypatch):
    """Sans configuration (défaut) -- identique à _notify_telegram seul, aucun appel
    supplémentaire tenté."""
    dm_calls = []

    async def fake_notify_telegram(self, text):
        dm_calls.append(text)

    monkeypatch.setattr(heartbeat.AriaHeartbeat, "_notify_telegram", fake_notify_telegram)
    send_mock = AsyncMock(return_value=True)
    monkeypatch.setattr("aria_core.gateway.telegram_bot.send_message", send_mock)

    await heartbeat.aria_heartbeat._notify_telegram_trading("achat fictif XYZ")

    assert dm_calls == ["achat fictif XYZ"]
    send_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_both_configured_sends_dm_and_topic(monkeypatch):
    dm_calls = []

    async def fake_notify_telegram(self, text):
        dm_calls.append(text)

    monkeypatch.setattr(heartbeat.AriaHeartbeat, "_notify_telegram", fake_notify_telegram)
    monkeypatch.setattr(settings, "aria_trading_topic_chat_id", -1003949048605, raising=False)
    monkeypatch.setattr(settings, "aria_trading_topic_thread_id", 67, raising=False)
    send_mock = AsyncMock(return_value=True)
    monkeypatch.setattr("aria_core.gateway.telegram_bot.send_message", send_mock)

    await heartbeat.aria_heartbeat._notify_telegram_trading("achat fictif XYZ")

    assert dm_calls == ["achat fictif XYZ"]  # le DM admin reste envoyé, en plus
    send_mock.assert_awaited_once_with(
        "achat fictif XYZ", chat_id=-1003949048605, message_thread_id=67,
    )


@pytest.mark.asyncio
async def test_only_chat_id_configured_no_topic_send(monkeypatch):
    """Les DEUX variables doivent être présentes -- une seule renseignée = pas d'envoi
    topic (config incomplète, jamais un demi-comportement)."""
    monkeypatch.setattr(heartbeat.AriaHeartbeat, "_notify_telegram", AsyncMock())
    monkeypatch.setattr(settings, "aria_trading_topic_chat_id", -1003949048605, raising=False)
    monkeypatch.setattr(settings, "aria_trading_topic_thread_id", None, raising=False)
    send_mock = AsyncMock(return_value=True)
    monkeypatch.setattr("aria_core.gateway.telegram_bot.send_message", send_mock)

    await heartbeat.aria_heartbeat._notify_telegram_trading("test")

    send_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_only_thread_id_configured_no_topic_send(monkeypatch):
    monkeypatch.setattr(heartbeat.AriaHeartbeat, "_notify_telegram", AsyncMock())
    monkeypatch.setattr(settings, "aria_trading_topic_chat_id", None, raising=False)
    monkeypatch.setattr(settings, "aria_trading_topic_thread_id", 67, raising=False)
    send_mock = AsyncMock(return_value=True)
    monkeypatch.setattr("aria_core.gateway.telegram_bot.send_message", send_mock)

    await heartbeat.aria_heartbeat._notify_telegram_trading("test")

    send_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_topic_send_exception_never_raises(monkeypatch):
    """Un échec réseau sur le topic (ex. bot pas admin du sujet) ne doit jamais faire
    planter le cycle -- même doctrine que _notify_telegram (jamais bloquant)."""
    dm_calls = []

    async def fake_notify_telegram(self, text):
        dm_calls.append(text)

    monkeypatch.setattr(heartbeat.AriaHeartbeat, "_notify_telegram", fake_notify_telegram)
    monkeypatch.setattr(settings, "aria_trading_topic_chat_id", -1003949048605, raising=False)
    monkeypatch.setattr(settings, "aria_trading_topic_thread_id", 67, raising=False)

    async def _broken(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("aria_core.gateway.telegram_bot.send_message", _broken)

    # Ne doit lever aucune exception.
    await heartbeat.aria_heartbeat._notify_telegram_trading("test")
    assert dm_calls == ["test"]  # le DM, lui, est bien parti avant l'échec du topic


# ── câblage paper_trade_cycle -> notifier trading-aware ────────────────────


@pytest.mark.asyncio
async def test_paper_trade_cycle_uses_trading_notifier(monkeypatch):
    """paper_trade_cycle doit passer _notify_telegram_trading (pas _notify_telegram nu)
    -- seule tâche heartbeat concernée par le double-envoi topic."""
    captured = {}

    async def fake_run_paper_cycle(*, notifier=None, **kwargs):
        captured["notifier"] = notifier
        return {"opened": [], "closed": []}

    monkeypatch.setattr("aria_core.paper_trader.run_paper_cycle", fake_run_paper_cycle)

    await heartbeat.aria_heartbeat._run_task("paper_trade_cycle")

    assert captured["notifier"] == heartbeat.aria_heartbeat._notify_telegram_trading
