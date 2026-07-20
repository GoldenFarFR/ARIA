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

    async def send_message(self, *, chat_id, text, message_thread_id=None, link_preview_options=None):
        self.calls.append({
            "chat_id": chat_id, "text": text, "message_thread_id": message_thread_id,
            "link_preview_options": link_preview_options,
        })


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
        {"chat_id": -100123, "text": "hello", "message_thread_id": 67, "link_preview_options": None}
    ]


@pytest.mark.asyncio
async def test_send_message_disable_preview_sets_link_preview_options(monkeypatch):
    """17/07 -- la carte d'aperçu Telegram peut être périmée (cache plateforme) sur un
    lien DexScreener posté après un token qui vient de prendre +1000 %+ ; le lien
    cliquable reste correct, seule la carte est désactivée."""
    monkeypatch.setattr(telegram_bot, "_bot_app", FakeApp())
    monkeypatch.setattr(settings, "telegram_bot_token", "x", raising=False)

    await telegram_bot.send_message("hello", chat_id=-100123, disable_preview=True)

    opts = telegram_bot._bot_app.bot.calls[0]["link_preview_options"]
    assert opts is not None
    assert opts.is_disabled is True


@pytest.mark.asyncio
async def test_send_message_default_thread_id_none_no_regression(monkeypatch):
    """Appel sans message_thread_id (les 20+ appelants existants) -- comportement
    identique à avant ce chantier : None passé tel quel, poste à la racine du chat."""
    monkeypatch.setattr(telegram_bot, "_bot_app", FakeApp())
    monkeypatch.setattr(settings, "telegram_bot_token", "x", raising=False)

    await telegram_bot.send_message("hello", chat_id=-100123)

    assert telegram_bot._bot_app.bot.calls[0]["message_thread_id"] is None


# ── send_trading_notification (telegram_bot.py) : DM + topic, dégradation douce ───
#
# 20/07 -- ``_notify_telegram_trading`` (Heartbeat) délègue maintenant à
# ``telegram_bot.send_trading_notification`` (fonction libre, pas une méthode liée) --
# bug réel trouvé : ``momentum_websocket.py`` n'avait aucun moyen de réutiliser une
# méthode liée à l'instance ``Heartbeat``, donc n'envoyait JAMAIS de notifier (position
# MAGIC achetée en silence, seule sa vente -- gérée par le heartbeat -- a été notifiée).
# Ces tests mockent désormais directement ``telegram_bot.send_message`` (le seul point
# d'entrée réseau réel), plus la méthode ``_notify_telegram`` désormais inutilisée par
# ce chemin.


@pytest.mark.asyncio
async def test_trading_dm_disables_preview(monkeypatch):
    """17/07 -- le DM admin des alertes de trading désactive aussi la carte d'aperçu
    (pas seulement l'envoi topic), même raison : lien DexScreener potentiellement
    accompagné d'une carte périmée."""
    send_mock = AsyncMock(return_value=True)
    monkeypatch.setattr("aria_core.gateway.telegram_bot.send_message", send_mock)

    await heartbeat.aria_heartbeat._notify_telegram_trading("achat fictif XYZ")

    send_mock.assert_awaited_once_with("achat fictif XYZ", disable_preview=True)


@pytest.mark.asyncio
async def test_no_topic_config_dm_only_no_regression(monkeypatch):
    """Sans configuration (défaut) -- un seul envoi (le DM), aucun appel topic tenté."""
    send_mock = AsyncMock(return_value=True)
    monkeypatch.setattr("aria_core.gateway.telegram_bot.send_message", send_mock)

    await heartbeat.aria_heartbeat._notify_telegram_trading("achat fictif XYZ")

    send_mock.assert_awaited_once_with("achat fictif XYZ", disable_preview=True)


@pytest.mark.asyncio
async def test_both_configured_sends_dm_and_topic(monkeypatch):
    monkeypatch.setattr(settings, "aria_trading_topic_chat_id", -1003949048605, raising=False)
    monkeypatch.setattr(settings, "aria_trading_topic_thread_id", 67, raising=False)
    send_mock = AsyncMock(return_value=True)
    monkeypatch.setattr("aria_core.gateway.telegram_bot.send_message", send_mock)

    await heartbeat.aria_heartbeat._notify_telegram_trading("achat fictif XYZ")

    assert send_mock.await_count == 2  # le DM admin reste envoyé, en plus du topic
    send_mock.assert_any_await("achat fictif XYZ", disable_preview=True)
    send_mock.assert_any_await(
        "achat fictif XYZ", chat_id=-1003949048605, message_thread_id=67, disable_preview=True,
    )


@pytest.mark.asyncio
async def test_only_chat_id_configured_no_topic_send(monkeypatch):
    """Les DEUX variables doivent être présentes -- une seule renseignée = pas d'envoi
    topic (config incomplète, jamais un demi-comportement)."""
    monkeypatch.setattr(settings, "aria_trading_topic_chat_id", -1003949048605, raising=False)
    monkeypatch.setattr(settings, "aria_trading_topic_thread_id", None, raising=False)
    send_mock = AsyncMock(return_value=True)
    monkeypatch.setattr("aria_core.gateway.telegram_bot.send_message", send_mock)

    await heartbeat.aria_heartbeat._notify_telegram_trading("test")

    send_mock.assert_awaited_once_with("test", disable_preview=True)  # DM seul


@pytest.mark.asyncio
async def test_only_thread_id_configured_no_topic_send(monkeypatch):
    monkeypatch.setattr(settings, "aria_trading_topic_chat_id", None, raising=False)
    monkeypatch.setattr(settings, "aria_trading_topic_thread_id", 67, raising=False)
    send_mock = AsyncMock(return_value=True)
    monkeypatch.setattr("aria_core.gateway.telegram_bot.send_message", send_mock)

    await heartbeat.aria_heartbeat._notify_telegram_trading("test")

    send_mock.assert_awaited_once_with("test", disable_preview=True)  # DM seul


@pytest.mark.asyncio
async def test_topic_send_exception_never_raises(monkeypatch):
    """Un échec réseau sur le topic (ex. bot pas admin du sujet) ne doit jamais faire
    planter le cycle -- même doctrine que _notify_telegram (jamais bloquant)."""
    monkeypatch.setattr(settings, "aria_trading_topic_chat_id", -1003949048605, raising=False)
    monkeypatch.setattr(settings, "aria_trading_topic_thread_id", 67, raising=False)

    async def _broken(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("aria_core.gateway.telegram_bot.send_message", _broken)

    # Ne doit lever aucune exception, ni sur le DM ni sur le topic.
    await heartbeat.aria_heartbeat._notify_telegram_trading("test")


@pytest.mark.asyncio
async def test_dm_exception_never_raises_and_topic_still_attempted(monkeypatch):
    """20/07 -- non-régression du vrai bug trouvé en corrigeant ce fichier : le premier
    envoi (DM) doit être protégé exactement comme le second (topic), jamais une
    exception qui remonte casser tout le cycle appelant (un vrai run_paper_cycle en
    conditions réelles, pas seulement le test)."""
    monkeypatch.setattr(settings, "aria_trading_topic_chat_id", -1003949048605, raising=False)
    monkeypatch.setattr(settings, "aria_trading_topic_thread_id", 67, raising=False)
    calls = []

    async def _flaky(text, *, chat_id=None, message_thread_id=None, disable_preview=False):
        calls.append(chat_id)
        if chat_id is None:
            raise RuntimeError("DM boom")
        return True

    monkeypatch.setattr("aria_core.gateway.telegram_bot.send_message", _flaky)

    # Ne doit lever aucune exception malgré l'échec du DM.
    await heartbeat.aria_heartbeat._notify_telegram_trading("test")
    assert calls == [None, -1003949048605]  # le topic est bien tenté malgré l'échec du DM


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
