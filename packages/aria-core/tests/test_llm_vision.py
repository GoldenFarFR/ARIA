"""chat_with_context avec image_data_uri — bascule en contenu multimodal, sinon
comportement texte strictement inchangé (tous les appelants existants intacts)."""
from __future__ import annotations

import pytest

from aria_core.runtime import get_settings


def _configure_virtuals(settings) -> None:
    settings.aria_llm_enabled = True
    settings.llm_provider = "virtuals"
    settings.virtuals_api_key = "spark-key"
    settings.aria_llm_temperature = 0.2


@pytest.mark.asyncio
async def test_no_image_keeps_plain_string_content(monkeypatch):
    from aria_core import llm as llm_mod

    _configure_virtuals(get_settings())
    captured = {}

    async def fake_post(route, **kwargs):
        captured["messages"] = kwargs["messages"]
        return "ok"

    monkeypatch.setattr(llm_mod, "_post_chat", fake_post)
    out = await llm_mod.chat_with_context("hello", "sys", max_tokens=50)
    assert out == "ok"
    user_msg = captured["messages"][-1]
    assert user_msg["role"] == "user"
    assert user_msg["content"] == "hello"  # chaîne simple, pas une liste


@pytest.mark.asyncio
async def test_image_builds_multimodal_content(monkeypatch):
    from aria_core import llm as llm_mod

    _configure_virtuals(get_settings())
    captured = {}

    async def fake_post(route, **kwargs):
        captured["messages"] = kwargs["messages"]
        return "ok"

    monkeypatch.setattr(llm_mod, "_post_chat", fake_post)
    data_uri = "data:image/jpeg;base64,ZmFrZQ=="
    out = await llm_mod.chat_with_context(
        "que vois-tu ?", "sys", max_tokens=50, image_data_uri=data_uri
    )
    assert out == "ok"
    user_msg = captured["messages"][-1]
    assert user_msg["role"] == "user"
    content = user_msg["content"]
    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": "que vois-tu ?"}
    assert content[1] == {"type": "image_url", "image_url": {"url": data_uri}}


@pytest.mark.asyncio
async def test_image_bumps_prompt_token_estimate(monkeypatch):
    from aria_core import llm as llm_mod

    _configure_virtuals(get_settings())
    estimates: list[int] = []

    async def fake_post(route, **kwargs):
        estimates.append(kwargs["prompt_est"])
        return "ok"

    monkeypatch.setattr(llm_mod, "_post_chat", fake_post)
    await llm_mod.chat_with_context("hi", "sys", max_tokens=50)
    await llm_mod.chat_with_context(
        "hi", "sys", max_tokens=50, image_data_uri="data:image/jpeg;base64,ZmFrZQ=="
    )
    assert estimates[1] > estimates[0]
