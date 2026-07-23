from __future__ import annotations

import logging
import time
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
    "deepseek": "https://api.deepseek.com/v1/chat/completions",
    # 17/07 -- Google's OFFICIAL OpenAI-compatible endpoint (ai.google.dev/gemini-api/
    # docs/openai), verified at the source before wiring -- same Bearer token format as
    # the other direct providers, no custom parser needed.
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
    # 17/07 -- verified at the source (docs.mistral.ai/api): natively OpenAI-compatible,
    # same Bearer header.
    "mistral": "https://api.mistral.ai/v1/chat/completions",
    # 17/07 -- Anthropic direct, native Messages API endpoint (NOT /chat/completions,
    # NOT OpenAI-compatible) -- see the dedicated branch in _post_chat/_headers_for_route.
    "anthropic": "https://api.anthropic.com/v1/messages",
}

DEFAULT_MODELS = {
    "xai": "grok-4.3",
    "grok": "grok-4.3",
    # 17/07 -- gpt-4o-mini (April 2025) replaced by gpt-5-mini (August 2025, explicitly
    # positioned by OpenAI for "cost-sensitive, low-latency, high-volume workloads"
    # -- exactly our use case, verified by research before updating).
    "openai": "gpt-5-mini",
    "groq": "llama-3.3-70b-versatile",
    "openrouter": "openrouter/free",
    "ollama": "llama3.2",
    "deepseek": "deepseek-chat",
    # 17/07 -- Flash confirmed "Free of charge" on the official pricing page verified
    # tonight (unlike Gemini 3.1 Pro Preview, explicitly "Not available" for free).
    "gemini": "gemini-3.5-flash",
    # 17/07 -- dated ID (not the "-latest" alias) for stable, reproducible behavior over
    # time, same doctrine as the rest of this file -- verified for real via OpenRouter/
    # Mistral docs (released March 2026, hybrid configurable reasoning).
    "mistral": "mistral-small-2603",
    # 17/07 -- dated ID verified live against /v1/models (api.anthropic.com) --
    # "claude-sonnet-5" itself has no dated suffix on Anthropic's side (canonical alias).
    "anthropic": "claude-haiku-4-5-20251001",
}


def _resolve_model(provider: str, explicit: str) -> str:
    """SSOT spark_config — never a banned model as Virtuals primary."""
    from aria_core.spark_config import (
        BANNED_VIRTUALS_PRIMARY_MODELS,
        DEFAULT_FALLBACK_MODEL,
        DEFAULT_MODEL_STANDARD,
        resolve_primary_llm_model,
    )

    model = (explicit or "").strip()
    if model in BANNED_VIRTUALS_PRIMARY_MODELS:
        model = ""
    p = provider.lower()
    if model:
        return model
    if p == "virtuals":
        return resolve_primary_llm_model()
    if p == "groq":
        return _setting_str("llm_fallback_model") or DEFAULT_FALLBACK_MODEL
    # Direct providers (xai/grok/deepseek/openai/...): never the Virtuals catalog ID
    # (resolve_primary_llm_model returns Spark-specific forms like "x-ai-grok-4-3") —
    # a real third-party API doesn't know this format. Latent bug found on 16/07 while
    # wiring the Spark relay, ACTUALLY triggered on 17/07 when switching off Virtuals
    # (real end-to-end test on the VPS -> 400 "Model not found: x-ai-grok-4-3" from
    # x.ai): this ``configured`` used to read ``settings.llm_model`` directly, which
    # ALWAYS carries a Virtuals catalog ID in this system (derived from
    # ARIA_LLM_MODEL_STANDARD) — never a valid model name for a real third-party API.
    # Removed: a direct provider with no explicit model always uses its own known
    # default, never this generic setting.
    return DEFAULT_MODELS.get(p, DEFAULT_MODEL_STANDARD)


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
        # Never reuse llm_api_key (often Groq) — causes a 401 on compute.virtuals.io
        return _setting_str("virtuals_api_key")
    if p == "grok" or p == "xai":
        return _setting_str("grok_api_key") or _setting_str("llm_api_key")
    if p == "deepseek":
        return _setting_str("deepseek_api_key") or _setting_str("llm_api_key")
    if p == "gemini":
        return _setting_str("gemini_api_key") or _setting_str("llm_api_key")
    if p == "mistral":
        return _setting_str("mistral_api_key") or _setting_str("llm_api_key")
    if p == "openai":
        return _setting_str("openai_api_key") or _setting_str("llm_api_key")
    if p == "openrouter":
        return _setting_str("openrouter_api_key") or _setting_str("llm_api_key")
    if p == "anthropic":
        return _setting_str("anthropic_api_key")
    return _setting_str("llm_api_key")


def _route_for_provider(provider: str, model: str) -> LlmRoute | None:
    p = provider.lower()
    if p in ("", "none"):
        return None
    # 17/07 -- real bug found while switching off Virtuals (Spark credits expired
    # 18/07, real end-to-end test on the VPS): ``settings.llm_model`` carries a
    # Virtuals catalog ID (e.g. "x-ai-grok-4-3") -- only offer it as a fallback for
    # the "virtuals" provider itself, never for a direct provider (xai/grok/deepseek/
    # ollama/...), otherwise `_resolve_model` returns it as-is (its 1st check `if model:
    # return model` only filters `BANNED_VIRTUALS_PRIMARY_MODELS`, not the Virtuals
    # catalog form) and the real third-party API rejects it (400 "Model not found").
    llm_model_fallback = settings.llm_model if p == "virtuals" else ""
    if p == "ollama":
        base = settings.ollama_base_url.rstrip("/")
        resolved_model = _resolve_model(p, model or llm_model_fallback) or DEFAULT_MODELS["ollama"]
        return LlmRoute(p, f"{base}/v1/chat/completions", resolved_model, "ollama")
    url = PROVIDER_URLS.get(p)
    if not url:
        return None
    auth_key = _auth_key_for_provider(p)
    if p != "ollama" and not auth_key:
        return None
    resolved_model = _resolve_model(p, model or llm_model_fallback) or DEFAULT_MODELS.get(
        p, "grok-3-mini"
    )
    return LlmRoute(p, url, resolved_model, auth_key)


def _fallback_route(primary_model: str) -> LlmRoute | None:
    """Dedicated fallback route (`llm_fallback_*`) — must NEVER depend on
    `llm_api_key` (the primary provider's generic key). Bug fixed (08/07 audit):
    going through `_route_for_provider`, the fallback required a non-empty
    `llm_api_key` BEFORE substituting `fb_key`, so a fallback configured with
    ONLY `llm_fallback_api_key` (the intended usage) would silently never fire."""
    fb_provider = _setting_str("llm_fallback_provider")
    fb_key = _setting_str("llm_fallback_api_key")
    if not fb_provider or not fb_key:
        return None
    p = fb_provider.lower()
    url = PROVIDER_URLS.get(p)
    if not url:
        return None
    fb_model = (
        _resolve_model(p, _setting_str("llm_fallback_model"))
        or primary_model
        or DEFAULT_MODELS.get(p, "grok-3-mini")
    )
    return LlmRoute(p, url, fb_model, fb_key)


def _resolve_routes(
    model: str | None = None,
    *,
    provider: str | None = None,
    fallback_provider: str | None = None,
    fallback_model: str | None = None,
    require_llm_enabled: bool = False,
) -> list[LlmRoute]:
    """``provider``/``fallback_provider``+``fallback_model`` (17/07): explicit PER-CALL
    routing, for specific callers that need a given model independently of the
    global ``LLM_PROVIDER`` (e.g. the momentum tie-breaker on Haiku via OpenRouter,
    while the rest of ARIA stays on Grok/Spark) -- behavior of all other callers
    strictly unchanged when these parameters are absent (same two routes as
    before: primary global provider, then ``llm_fallback_*``).

    Route order when both are provided: explicit provider -> explicit fallback ->
    existing global fallback (``llm_fallback_*``, e.g. Groq) as a last safety net,
    only if its provider differs from the two previous ones -- never a duplicate."""
    if require_llm_enabled and not settings.aria_llm_enabled:
        return []
    effective_model = (model or "").strip()

    # 18/07 -- circuit breaker (llm_circuit_breaker.py): only applies when the caller
    # hasn't already set its own provider (e.g. the momentum tie-breaker on Haiku via
    # OpenRouter stays unchanged, armed or not -- it never depends on Grok). Only
    # affects the DEFAULT routing (no explicit provider passed by the caller).
    breaker_override = None
    if provider is None:
        from aria_core.llm_circuit_breaker import get_override

        breaker_override = get_override()

    if breaker_override:
        primary = breaker_override["provider"]
        if not effective_model:
            effective_model = breaker_override.get("model", "")
    else:
        primary = (provider or settings.llm_provider).lower()

    routes: list[LlmRoute] = []
    primary_route = _route_for_provider(primary, effective_model)
    if primary_route:
        routes.append(primary_route)

    if fallback_provider:
        explicit_fb = _route_for_provider(fallback_provider.lower(), (fallback_model or "").strip())
        # Deduplication by (provider, model), NOT provider alone: the targeted case
        # (Sonnet 5 primary -> Haiku fallback, both "openrouter") deliberately has the
        # SAME provider with a different model -- deduplicating by provider alone would
        # have wrongly dropped this explicit fallback.
        if explicit_fb and all(
            (r.provider, r.model) != (explicit_fb.provider, explicit_fb.model) for r in routes
        ):
            routes.append(explicit_fb)
    elif breaker_override and breaker_override.get("fallback_model"):
        # The circuit breaker carries its own designated fallback (Haiku via OpenRouter,
        # same provider as the primary) -- no need for the caller to specify it.
        breaker_fb = _route_for_provider(breaker_override["provider"], breaker_override["fallback_model"])
        if breaker_fb and all(
            (r.provider, r.model) != (breaker_fb.provider, breaker_fb.model) for r in routes
        ):
            routes.append(breaker_fb)

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
    if route.provider == "anthropic":
        # Native Messages API -- NO Bearer. Format verified live on 17/07 against
        # api.anthropic.com (GET /v1/models 200, POST /v1/messages 400 billing -- so
        # correctly authenticated, just 0 credit on the account at this stage).
        headers["x-api-key"] = route.auth_key
        headers["anthropic-version"] = "2023-06-01"
        return headers
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

    if route.provider == "anthropic":
        return await _post_chat_anthropic(
            route,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            prompt_est=prompt_est,
            depth=depth,
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
    if route.provider == "mistral":
        # 17/07 -- Mistral Small 4 is hybrid (configurable reasoning, verified at the
        # source docs.mistral.ai/api). Forced to "none" by default: without this, same
        # trap as observed tonight with Gemini 3.5 Flash (token budget entirely consumed
        # by invisible reasoning, empty reply). Unlike Gemini, Mistral exposes a real
        # documented lever to avoid it -- applied systematically, not just for this
        # specific use case.
        payload["reasoning_effort"] = "none"

    async with httpx.AsyncClient(timeout=timeout) as client:
        # 17/07 -- explicit operator request: arbitrate Grok vs Gemini (and any future
        # provider) on REALLY measured latency, not a guess. Times only the network
        # round-trip (not the downstream JSON parsing, negligible).
        _t0 = time.monotonic()
        response = await client.post(
            route.url,
            headers=_headers_for_route(route),
            json=payload,
        )
        latency_ms = (time.monotonic() - _t0) * 1000.0
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
                latency_ms=latency_ms,
            )
            return None
        data: dict[str, Any] = response.json()
        choice = data["choices"][0]
        reply = choice["message"]["content"].strip()
        finish_reason = choice.get("finish_reason")
        truncated = finish_reason == "length"
        if truncated:
            logger.warning(
                "LLM response truncated (finish_reason=length) provider=%s model=%s "
                "depth=%s max_tokens=%s — the reply sent is incomplete.",
                route.provider,
                route.model,
                depth,
                max_tokens,
            )
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
            truncated=truncated,
            latency_ms=latency_ms,
        )
        return reply


async def _post_chat_anthropic(
    route: LlmRoute,
    *,
    messages: list[dict[str, Any]],
    temperature: float,
    max_tokens: int,
    prompt_est: int,
    depth: str | None,
) -> str | None:
    """Native Anthropic Messages API (17/07) -- schema totally different from the other
    providers in this file (all OpenAI-compatible): ``system`` is a top-level field
    (not a "system" role message), the response returns typed ``content`` blocks
    (not ``choices[0].message.content``). Format verified live against api.anthropic.com
    on 17/07 (401->200 on /v1/models, 400 billing-only on /v1/messages -- so correctly
    authenticated). Vision (``image_data_uri``) not handled here: no current caller
    exercises it on this provider -- OpenAI-style multimodal content would be passed
    through as-is and most likely rejected (different image schema on Anthropic's
    side), never silently ignored."""
    from aria_core.llm_usage import (
        estimate_tokens_from_text,
        parse_usage_from_response,
        record_llm_usage,
    )

    system_text = ""
    anthropic_messages: list[dict[str, Any]] = []
    for m in messages:
        if m.get("role") == "system":
            content = m.get("content", "")
            system_text = content if isinstance(content, str) else ""
            continue
        anthropic_messages.append(m)

    payload: dict[str, Any] = {
        "model": route.model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": anthropic_messages,
    }
    if system_text:
        payload["system"] = system_text

    async with httpx.AsyncClient(timeout=90.0) as client:
        _t0 = time.monotonic()
        response = await client.post(
            route.url,
            headers=_headers_for_route(route),
            json=payload,
        )
        latency_ms = (time.monotonic() - _t0) * 1000.0
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
                latency_ms=latency_ms,
            )
            return None
        data: dict[str, Any] = response.json()
        blocks = data.get("content") or []
        reply = "".join(
            b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text"
        ).strip()
        stop_reason = data.get("stop_reason")
        truncated = stop_reason == "max_tokens"
        if truncated:
            logger.warning(
                "LLM response truncated (stop_reason=max_tokens) provider=%s model=%s "
                "depth=%s max_tokens=%s — the reply sent is incomplete.",
                route.provider,
                route.model,
                depth,
                max_tokens,
            )
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
            truncated=truncated,
            latency_ms=latency_ms,
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
    provider: str | None = None,
    fallback_provider: str | None = None,
    fallback_model: str | None = None,
    depth: str | None = None,
    image_data_uri: str | None = None,
) -> str | None:
    """LLM call with injected memory. Spark (virtuals) first, fallback on failure.

    ``image_data_uri`` (optional, ``data:image/...;base64,...``) switches the user
    message to multimodal content (OpenAI-compatible chat-completions shape,
    ``[{"type":"text",...},{"type":"image_url",...}]``) — otherwise behavior is
    strictly unchanged (plain string, all existing callers untouched). No guarantee
    that the active model/route accepts vision: a model that doesn't support it
    generally replies while ignoring the image, never an exception; no invented
    truth on this point until tested live.

    ``provider``/``fallback_provider``+``fallback_model`` (17/07): explicit per-call
    routing, for specific callers (e.g. momentum tie-breaker on Haiku via
    OpenRouter) independently of the global ``LLM_PROVIDER`` -- see ``_resolve_routes``.
    Absent -> behavior strictly unchanged for all other callers.
    """
    routes = _resolve_routes(
        model,
        provider=provider,
        fallback_provider=fallback_provider,
        fallback_model=fallback_model,
        require_llm_enabled=True,
    )
    if not routes:
        return None

    user_content: object = user_message
    if image_data_uri:
        user_content = [
            {"type": "text", "text": user_message},
            {"type": "image_url", "image_url": {"url": image_data_uri}},
        ]

    messages: list[dict[str, object]] = [{"role": "system", "content": system_context}]
    if conversation_history:
        messages.extend(conversation_history[-12:])
    messages.append({"role": "user", "content": user_content})

    from aria_core.llm_usage import estimate_tokens_from_text

    prompt_est = estimate_tokens_from_text(
        system_context,
        user_message,
        *(m.get("content", "") for m in (conversation_history or [])),
    )
    if image_data_uri:
        # Fixed rough estimate (the base64 encoding itself is NOT text to count
        # character by character): avoids silently wrong cost telemetry.
        prompt_est += 800
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
                    from aria_core.llm_usage import mark_fallback_used

                    mark_fallback_used(route.provider)
                return reply
        except Exception as exc:
            logger.error("LLM request failed provider=%s: %s", route.provider, exc)
    return None