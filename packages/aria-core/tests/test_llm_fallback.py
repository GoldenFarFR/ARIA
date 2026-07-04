import pytest

from aria_core.llm import LlmRoute, _http_ok, _resolve_routes, is_llm_configured
from aria_core.runtime import get_settings


def test_http_ok_accepts_virtuals_201():
    assert _http_ok(200) is True
    assert _http_ok(201) is True
    assert _http_ok(401) is False


def test_virtuals_with_groq_fallback_chain():
    settings = get_settings()
    settings.aria_llm_enabled = True
    settings.llm_provider = "virtuals"
    settings.virtuals_api_key = "spark-key"
    settings.llm_model = "deepseek-deepseek-v4-pro"
    settings.llm_fallback_provider = "groq"
    settings.llm_fallback_api_key = "gsk-fallback"
    settings.llm_fallback_model = "llama-3.3-70b-versatile"

    routes = _resolve_routes()
    assert len(routes) == 2
    assert routes[0] == LlmRoute(
        "virtuals",
        "https://compute.virtuals.io/v1/chat/completions",
        "deepseek-deepseek-v4-pro",
        "spark-key",
    )
    assert routes[1].provider == "groq"
    assert routes[1].auth_key == "gsk-fallback"
    assert is_llm_configured() is True


def test_virtuals_never_uses_groq_llm_api_key():
    settings = get_settings()
    settings.aria_llm_enabled = True
    settings.llm_provider = "virtuals"
    settings.virtuals_api_key = ""
    settings.llm_api_key = "gsk-should-not-be-used"
    settings.llm_model = "deepseek-deepseek-v4-pro"
    settings.llm_fallback_provider = ""
    settings.llm_fallback_api_key = ""
    settings.llm_fallback_model = ""

    routes = _resolve_routes()
    assert routes == []


def test_groq_only_no_duplicate_fallback():
    settings = get_settings()
    settings.aria_llm_enabled = True
    settings.llm_provider = "groq"
    settings.llm_api_key = "gsk-main"
    settings.llm_fallback_provider = "groq"
    settings.llm_fallback_api_key = "gsk-fallback"

    routes = _resolve_routes()
    assert len(routes) == 1
    assert routes[0].provider == "groq"


@pytest.mark.asyncio
async def test_chat_falls_back_to_groq(monkeypatch):
    from aria_core import llm as llm_mod

    settings = get_settings()
    settings.aria_llm_enabled = True
    settings.llm_provider = "virtuals"
    settings.virtuals_api_key = "spark-key"
    settings.llm_fallback_provider = "groq"
    settings.llm_fallback_api_key = "gsk-fallback"
    settings.llm_fallback_model = "llama-3.3-70b-versatile"
    settings.aria_llm_temperature = 0.2

    calls: list[str] = []

    async def fake_post(route, **kwargs):
        calls.append(route.provider)
        if route.provider == "virtuals":
            return None
        return "fallback-ok"

    monkeypatch.setattr(llm_mod, "_post_chat", fake_post)
    out = await llm_mod.chat_with_context("hi", "sys", max_tokens=50)
    assert out == "fallback-ok"
    assert calls == ["virtuals", "groq"]