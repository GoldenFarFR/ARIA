from __future__ import annotations

import logging
from dataclasses import dataclass
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
    "virtuals": "https://compute.virtuals.io/v1/chat/completions",
}

DEFAULT_MODELS = {
    "xai": "grok-4.3",
    "grok": "grok-4.3",
    "openai": "gpt-4o-mini",
    "groq": "llama-3.3-70b-versatile",
    "openrouter": "openrouter/free",
    "virtuals": "deepseek-deepseek-v4-pro",
    "ollama": "llama3.2",
}


@dataclass(frozen=True)
class LlmRoute:
    provider: str
    url: str
    model: str
    auth_key: str


def _setting_str(name: str, default: str = "") -> str:
    return (getattr(settings, name, None) or default).strip()


def _auth_key_for_provider(provider: str) -> str:
    p = provider.lower()
    if p == "virtuals":
        # Ne jamais réutiliser llm_api_key (souvent Groq) — provoque 401 sur compute.virtuals.io
        return _setting_str("virtuals_api_key")
    if p == "grok" or p == "xai":
        return _setting_str("grok_api_key") or _setting_str("llm_api_key")
    return _setting_str("llm_api_key")


def _route_for_provider(provider: str, model: str) -> LlmRoute | None:
    p = provider.lower()
    if p in ("", "none"):
        return None
    if p == "ollama":
        base = settings.ollama_base_url.rstrip("/")
        resolved_model = model or settings.llm_model or DEFAULT_MODELS["ollama"]
        return LlmRoute(p, f"{base}/v1/chat/completions", resolved_model, "ollama")
    url = PROVIDER_URLS.get(p)
    if not url:
        return None
    auth_key = _auth_key_for_provider(p)
    if p != "ollama" and not auth_key:
        return None
    resolved_model = model or settings.llm_model or DEFAULT_MODELS.get(p, "grok-3-mini")
    return LlmRoute(p, url, resolved_model, auth_key)


def _fallback_route(primary_model: str) -> LlmRoute | None:
    fb_provider = _setting_str("llm_fallback_provider")
    fb_key = _setting_str("llm_fallback_api_key")
    if not fb_provider or not fb_key:
        return None
    fb_model = _setting_str("llm_fallback_model") or DEFAULT_MODELS.get(fb_provider.lower(), "")
    route = _route_for_provider(fb_provider, fb_model or primary_model)
    if not route:
        return None
    return LlmRoute(route.provider, route.url, route.model, fb_key)


def _resolve_routes(model: str | None = None, *, require_llm_enabled: bool = False) -> list[LlmRoute]:
    if require_llm_enabled and not settings.aria_llm_enabled:
        return []
    effective_model = (model or "").strip()
    primary = settings.llm_provider.lower()
    routes: list[LlmRoute] = []
    primary_route = _route_for_provider(primary, effective_model)
    if primary_route:
        routes.append(primary_route)
    fallback = _fallback_route(effective_model)
    if fallback and all(r.provider != fallback.provider for r in routes):
        routes.append(fallback)
    return routes


def is_llm_provider_configured() -> bool:
    return bool(_resolve_routes())


def is_llm_configured() -> bool:
    if not settings.aria_llm_enabled:
        return False
    return bool(_resolve_routes(require_llm_enabled=True))


def _resolve_endpoint() -> tuple[str, str] | None:
    routes = _resolve_routes()
    if not routes:
        return None
    return routes[0].url, routes[0].model


def _http_ok(status_code: int) -> bool:
    """OpenAI-compatible APIs may return 200 or 201 (Virtuals Spark)."""
    return 200 <= int(status_code) < 300


def _headers_for_route(route: LlmRoute) -> dict[str, str]:
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if route.provider == "ollama":
        headers["Authorization"] = "Bearer ollama"
    elif route.auth_key:
        headers["Authorization"] = f"Bearer {route.auth_key}"
    if route.provider == "openrouter":
        headers["HTTP-Referer"] = "https://github.com/GoldenFarFR/aria-vanguard"
        from aria_core.narrative import llm_provider_title

        headers["X-Title"] = llm_provider_title()
    return headers


async def _post_chat(
    route: LlmRoute,
    *,
    messages: list[dict[str, str]],
    temperature: float,
    max_tokens: int,
    prompt_est: int,
    depth: str | None,
) -> str | None:
    from aria_core.llm_usage import (
        estimate_tokens_from_text,
        parse_usage_from_response,
        record_llm_usage,
    )

    timeout = 120.0 if route.provider == "ollama" else 90.0
    payload: dict[str, Any] = {
        "model": route.model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if route.provider == "ollama":
        num_ctx = int(getattr(settings, "aria_ollama_num_ctx", 0) or 0)
        if num_ctx > 0:
            payload["options"] = {"num_ctx": num_ctx}

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            route.url,
            headers=_headers_for_route(route),
            json=payload,
        )
        if not _http_ok(response.status_code):
            logger.warning(
                "LLM error provider=%s model=%s status=%s: %s",
                route.provider,
                route.model,
                response.status_code,
                response.text[:300],
            )
            record_llm_usage(
                provider=route.provider,
                model=route.model,
                input_tokens=prompt_est,
                output_tokens=0,
                ok=False,
                status_code=response.status_code,
                kind="chat",
                estimated=True,
                depth=depth,
            )
            return None
        data: dict[str, Any] = response.json()
        reply = data["choices"][0]["message"]["content"].strip()
        usage = parse_usage_from_response(data)
        estimated = usage["total_tokens"] <= 0
        if estimated:
            usage = {
                "input_tokens": prompt_est,
                "output_tokens": estimate_tokens_from_text(reply),
                "total_tokens": prompt_est + estimate_tokens_from_text(reply),
            }
        record_llm_usage(
            provider=route.provider,
            model=route.model,
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
            ok=True,
            kind="chat",
            estimated=estimated,
            depth=depth,
        )
        return reply


async def chat_with_context(
    user_message: str,
    system_context: str,
    conversation_history: list[dict[str, str]] | None = None,
    *,
    temperature: float | None = None,
    max_tokens: int = 400,
    model: str | None = None,
    depth: str | None = None,
) -> str | None:
    """Appel LLM avec mémoire injectée. Spark (virtuals) d'abord, fallback si échec."""
    routes = _resolve_routes(model, require_llm_enabled=True)
    if not routes:
        return None

    messages: list[dict[str, str]] = [{"role": "system", "content": system_context}]
    if conversation_history:
        messages.extend(conversation_history[-12:])
    messages.append({"role": "user", "content": user_message})

    from aria_core.llm_usage import estimate_tokens_from_text

    prompt_est = estimate_tokens_from_text(
        system_context,
        user_message,
        *(m.get("content", "") for m in (conversation_history or [])),
    )
    temp = temperature if temperature is not None else settings.aria_llm_temperature

    for idx, route in enumerate(routes):
        try:
            reply = await _post_chat(
                route,
                messages=messages,
                temperature=temp,
                max_tokens=max_tokens,
                prompt_est=prompt_est,
                depth=depth,
            )
            if reply is not None:
                if idx > 0:
                    logger.info(
                        "LLM fallback ok provider=%s model=%s (primary failed)",
                        route.provider,
                        route.model,
                    )
                return reply
        except Exception as exc:
            logger.error("LLM request failed provider=%s: %s", route.provider, exc)
    return None