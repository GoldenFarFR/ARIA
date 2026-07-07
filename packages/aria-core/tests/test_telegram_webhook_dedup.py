"""Garde-fou anti-boucle du webhook Telegram.

Contexte incident (2026-07-07) : en mode webhook, la route répondait 200
seulement APRÈS avoir terminé toute l'analyse /vc (20-40 s). Telegram, ne
recevant pas d'accusé rapide, redélivrait le MÊME update (même update_id) en
boucle → ARIA republiait l'analyse à l'infini. Deux correctifs :
  1. la route répond 200 immédiatement (traitement en tâche de fond) ;
  2. process_webhook_update déduplique par update_id (ce test).

Aucun réseau : Update.de_json et _bot_app.process_update sont mockés.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from aria_core.gateway import telegram_bot


class _FakeApp:
    def __init__(self) -> None:
        self.bot = object()
        self.process_update = AsyncMock()


@pytest.fixture(autouse=True)
def _reset_state():
    telegram_bot._seen_update_ids.clear()
    saved = telegram_bot._bot_app
    telegram_bot._bot_app = _FakeApp()
    yield
    telegram_bot._bot_app = saved
    telegram_bot._seen_update_ids.clear()


@pytest.mark.asyncio
async def test_duplicate_update_id_processed_once():
    """Le même update livré 3 fois ne déclenche qu'UN seul traitement."""
    payload = {"update_id": 777, "message": {"text": "/vc 0xabc test"}}

    with patch("telegram.Update.de_json", return_value=object()):
        await telegram_bot.process_webhook_update(payload)
        await telegram_bot.process_webhook_update(payload)
        await telegram_bot.process_webhook_update(payload)

    assert telegram_bot._bot_app.process_update.await_count == 1


@pytest.mark.asyncio
async def test_distinct_update_ids_each_processed():
    """Des update_id différents sont chacun traités (pas de faux positif)."""
    with patch("telegram.Update.de_json", return_value=object()):
        await telegram_bot.process_webhook_update({"update_id": 1})
        await telegram_bot.process_webhook_update({"update_id": 2})
        await telegram_bot.process_webhook_update({"update_id": 3})

    assert telegram_bot._bot_app.process_update.await_count == 3


@pytest.mark.asyncio
async def test_missing_update_id_not_deduped():
    """Sans update_id, on ne bloque pas (comportement conservateur)."""
    with patch("telegram.Update.de_json", return_value=object()):
        await telegram_bot.process_webhook_update({"message": {"text": "hi"}})
        await telegram_bot.process_webhook_update({"message": {"text": "hi"}})

    assert telegram_bot._bot_app.process_update.await_count == 2


@pytest.mark.asyncio
async def test_seen_cache_is_bounded():
    """Le cache d'update_id ne grossit pas sans limite (anti fuite mémoire)."""
    with patch("telegram.Update.de_json", return_value=object()):
        for uid in range(telegram_bot._SEEN_UPDATE_CAP + 50):
            await telegram_bot.process_webhook_update({"update_id": uid})

    assert len(telegram_bot._seen_update_ids) <= telegram_bot._SEEN_UPDATE_CAP
