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
    "deepseek": "https://api.deepseek.com/v1/chat/completions",
}

DEFAULT_MODELS = {
    "xai": "grok-4.3",
    "grok": "grok-4.3",
    "openai": "gpt-4o-mini",
    "groq": "llama-3.3-70b-versatile",
    "openrouter": "openrouter/free",
    "ollama": "llama3.2",
    "deepseek": "deepseek-chat",
}


def _resolve_model(provider: str, explicit: str) -> str:
    """SSOT spark_config — jamais de modele banni en primary Virtuals."""
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
    # Providers directs (xai/grok/deepseek/openai/...) : jamais l'ID catalogue Virtuals
    # (resolve_primary_llm_model renvoie des formes "x-ai-grok-4-3" propres à Spark) —
    # une vraie API tierce ne connaît pas ce format. Bug latent trouvé le 16/07 en cablant
    # le relais Spark, RÉELLEMENT exercé le 17/07 en basculant hors de Virtuals (test bout
    # en bout réel sur le VPS -> 400 "Model not found: x-ai-grok-4-3" côté x.ai) : ce
    # ``configured`` lisait ``settings.llm_model`` directement, qui porte TOUJOURS un ID
    # catalogue Virtuals dans ce système (dérivé de ARIA_LLM_MODEL_STANDARD) — jamais un
    # nom de modèle valide pour une vraie API tierce. Supprimé : un provider direct sans
    # modèle explicite utilise toujours son défaut connu, jamais ce réglage générique.
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
        # Ne jamais réutiliser llm_api_key (souvent Groq) — provoque 401 sur compute.virtuals.io
        return _setting_str("virtuals_api_key")
    if p == "grok" or p == "xai":
        return _setting_str("grok_api_key") or _setting_str("llm_api_key")
    if p == "deepseek":
        return _setting_str("deepseek_api_key") or _setting_str("llm_api_key")
    return _setting_str("llm_api_key")


def _route_for_provider(provider: str, model: str) -> LlmRoute | None:
    p = provider.lower()
    if p in ("", "none"):
        return None
    # 17/07 -- bug réel trouvé en basculant hors de Virtuals (expiration crédits Spark
    # 18/07, test bout en bout réel sur le VPS) : ``settings.llm_model`` porte un ID
    # catalogue Virtuals (ex. "x-ai-grok-4-3") -- ne le proposer comme repli QUE pour le
    # provider "virtuals" lui-même, jamais pour un provider direct (xai/grok/deepseek/
    # ollama/...), sinon `_resolve_model` le renvoie tel quel (son 1er check `if model:
    # return model` ne filtre que `BANNED_VIRTUALS_PRIMARY_MODELS`, pas la forme
    # catalogue Virtuals) et la vraie API tierce le rejette (400 "Model not found").
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
    """Route de secours dédiée (`llm_fallback_*`) — ne doit JAMAIS dépendre de
    `llm_api_key` (la clé générique du provider primaire). Bug corrigé (audit 08/07) :
    en passant par `_route_for_provider`, le fallback exigeait `llm_api_key` non-vide
    AVANT de substituer `fb_key`, donc un fallback configuré avec SEULEMENT
    `llm_fallback_api_key` (l'usage prévu) ne se déclenchait jamais silencieusement."""
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
        choice = data["choices"][0]
        reply = choice["message"]["content"].strip()
        finish_reason = choice.get("finish_reason")
        truncated = finish_reason == "length"
        if truncated:
            logger.warning(
                "LLM response truncated (finish_reason=length) provider=%s model=%s "
                "depth=%s max_tokens=%s — la réponse envoyée est incomplète.",
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
    image_data_uri: str | None = None,
) -> str | None:
    """Appel LLM avec mémoire injectée. Spark (virtuals) d'abord, fallback si échec.

    ``image_data_uri`` (optionnel, ``data:image/...;base64,...``) bascule le message
    utilisateur en contenu multimodal (forme chat-completions OpenAI-compatible,
    ``[{"type":"text",...},{"type":"image_url",...}]``) — sinon comportement
    strictement inchangé (chaîne simple, tous les appelants existants intacts).
    Aucune garantie que le modèle/route actif accepte la vision : un modèle qui ne
    la supporte pas répond en général en ignorant l'image, jamais une exception ;
    aucune vérité inventée sur ce point tant que non testé en direct.
    """
    routes = _resolve_routes(model, require_llm_enabled=True)
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
        # Estimation grossière fixe (l'encodage base64 lui-même n'est PAS du texte à
        # compter caractère par caractère) : évite une télémétrie de coût silencieuse.
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