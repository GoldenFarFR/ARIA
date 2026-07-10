"""AriaBrain._llm_response(image_data_uri=...) — threading + règle anti-hallucination
vision dans le prompt système. Dépendances lourdes (contexte mémoire, historique,
liens publics) mockées pour isoler le comportement vision."""
from __future__ import annotations

import pytest

from aria_core import brain as brain_mod
from aria_core import repertoire_db
from aria_core.locale import LANG_FR


@pytest.fixture(autouse=True)
def _mock_heavy_deps(monkeypatch):
    async def fake_build_llm_context(**kwargs):
        return "contexte factice"

    async def fake_get_messages(**kwargs):
        return []

    async def fake_get_bot_username():
        return "Aria_ZHC_Bot"

    def fake_get_channel_links_text():
        return "liens factices"

    monkeypatch.setattr(brain_mod, "build_llm_context", fake_build_llm_context)
    monkeypatch.setattr(repertoire_db, "get_messages", fake_get_messages)
    monkeypatch.setattr(
        "aria_core.gateway.telegram_bot.get_bot_username", fake_get_bot_username
    )
    monkeypatch.setattr(
        "aria_core.gateway.telegram_bot.get_channel_links_text", fake_get_channel_links_text
    )
    yield


@pytest.mark.asyncio
async def test_image_data_uri_reaches_chat_with_context(monkeypatch):
    captured = {}

    async def fake_chat_with_context(message, system, history=None, **kwargs):
        captured["message"] = message
        captured["system"] = system
        captured["image_data_uri"] = kwargs.get("image_data_uri")
        return "voici ce que je vois"

    monkeypatch.setattr(brain_mod, "chat_with_context", fake_chat_with_context)

    data_uri = "data:image/jpeg;base64,ZmFrZQ=="
    reply = await brain_mod.aria_brain._llm_response(
        "juge cette situation", LANG_FR, public=False, image_data_uri=data_uri
    )

    assert reply == "voici ce que je vois"
    assert captured["image_data_uri"] == data_uri
    assert captured["message"] == "juge cette situation"


@pytest.mark.asyncio
async def test_vision_rule_present_in_system_prompt_when_image(monkeypatch):
    captured = {}

    async def fake_chat_with_context(message, system, history=None, **kwargs):
        captured["system"] = system
        return "ok"

    monkeypatch.setattr(brain_mod, "chat_with_context", fake_chat_with_context)

    await brain_mod.aria_brain._llm_response(
        "juge cette situation",
        LANG_FR,
        public=False,
        image_data_uri="data:image/jpeg;base64,ZmFrZQ==",
    )

    system = captured["system"]
    assert "RÈGLE IMAGE" in system
    assert "ne l'invente jamais" in system


@pytest.mark.asyncio
async def test_no_image_rule_absent_and_param_none(monkeypatch):
    captured = {}

    async def fake_chat_with_context(message, system, history=None, **kwargs):
        captured["system"] = system
        captured["image_data_uri"] = kwargs.get("image_data_uri")
        return "ok"

    monkeypatch.setattr(brain_mod, "chat_with_context", fake_chat_with_context)

    await brain_mod.aria_brain._llm_response("salut", LANG_FR, public=False)

    assert "RÈGLE IMAGE" not in captured["system"]
    assert captured["image_data_uri"] is None
