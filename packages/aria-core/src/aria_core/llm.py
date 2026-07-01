from __future__ import annotations

import logging
from typing import Any

import httpx

from aria_core.runtime import settings

logger = logging.getLogger(__name__)

PROVIDER_URLS = {
    "xai": "https://api.x.ai/v1/chat/completions",
    "openai": "https://api.openai.com/v1/chat/completions",
    "grok": "https://api.x.ai/v1/chat/completions",
    "groq": "https://api.groq.com/openai/v1/chat/completions",
    "openrouter": "https://openrouter.ai/api/v1/chat/completions",
}

DEFAULT_MODELS = {
    "xai": "grok-3-mini",
    "grok": "grok-3-mini",
    "openai": "gpt-4o-mini",
    "groq": "llama-3.3-70b-versatile",
    "openrouter": "openrouter/free",
    "ollama": "llama3.2",
}


def is_llm_provider_configured() -> bool:
    provider = settings.llm_provider.lower()
    if provider in ("", "none"):
        return False
    if provider == "ollama":
        return True
    return bool(settings.llm_api_key)


def is_llm_configured() -> bool:
    """True only when operator enabled ARIA LLM AND provider credentials exist."""
    if not settings.aria_llm_enabled:
        return False
    return is_llm_provider_configured()


def _resolve_endpoint() -> tuple[str, str] | None:
    if not is_llm_configured():
        return None
    provider = settings.llm_provider.lower()

    if provider == "ollama":
        base = settings.ollama_base_url.rstrip("/")
        model = settings.llm_model or DEFAULT_MODELS["ollama"]
        return f"{base}/v1/chat/completions", model

    url = PROVIDER_URLS.get(provider)
    if not url:
        return None
    model = settings.llm_model or DEFAULT_MODELS.get(provider, "grok-3-mini")
    return url, model


async def chat_with_context(
    user_message: str,
    system_context: str,
    conversation_history: list[dict[str, str]] | None = None,
    *,
    temperature: float | None = None,
    max_tokens: int = 1200,
) -> str | None:
    """Appel LLM avec mémoire injectée dans le system prompt."""
    resolved = _resolve_endpoint()
    if not resolved:
        return None

    url, model = resolved
    messages: list[dict[str, str]] = [
        {"role": "system", "content": system_context},
    ]

    if conversation_history:
        messages.extend(conversation_history[-12:])

    messages.append({"role": "user", "content": user_message})

    provider = settings.llm_provider.lower()
    headers: dict[str, str] = {"Content-Type": "application/json"}

    if provider == "ollama":
        headers["Authorization"] = "Bearer ollama"
    elif settings.llm_api_key:
        headers["Authorization"] = f"Bearer {settings.llm_api_key}"

    if provider == "openrouter":
        headers["HTTP-Referer"] = "https://github.com/GoldenFarFR/aria-vanguard"
        from aria_core.narrative import llm_provider_title

        headers["X-Title"] = llm_provider_title()

    try:
        timeout = 120.0 if provider == "ollama" else 60.0
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature if temperature is not None else settings.aria_llm_temperature,
            "max_tokens": max_tokens,
        }
        if provider == "ollama":
            num_ctx = int(getattr(settings, "aria_ollama_num_ctx", 0) or 0)
            if num_ctx > 0:
                payload["options"] = {"num_ctx": num_ctx}
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                url,
                headers=headers,
                json=payload,
            )
            if response.status_code != 200:
                logger.warning("LLM error %s: %s", response.status_code, response.text[:300])
                return None
            data: dict[str, Any] = response.json()
            return data["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        logger.error("LLM request failed: %s", exc)
        return None