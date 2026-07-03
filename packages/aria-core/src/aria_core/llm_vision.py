"""Vision LLM — analyse d'images (identité avatar, cohérence visage)."""

from __future__ import annotations

import base64
import logging
from typing import Any

import httpx

from aria_core.llm import PROVIDER_URLS, is_llm_configured
from aria_core.runtime import settings

logger = logging.getLogger(__name__)

VISION_MODELS = {
    "xai": "grok-2-vision-1212",
    "grok": "grok-2-vision-1212",
    "openai": "gpt-4o-mini",
    "groq": "meta-llama/llama-4-scout-17b-16e-instruct",
    "openrouter": "openai/gpt-4o-mini",
}


def _vision_model() -> str | None:
    if not is_llm_configured():
        return None
    provider = settings.llm_provider.lower()
    if provider == "ollama":
        return settings.llm_model or "llama3.2-vision"
    return settings.llm_model or VISION_MODELS.get(provider)


async def vision_analyze(image_jpeg: bytes, instruction: str, *, max_tokens: int = 500) -> str | None:
    """Analyse une image JPEG via le provider LLM configuré."""
    model = _vision_model()
    if not model:
        return None

    provider = settings.llm_provider.lower()
    if provider in ("", "none"):
        return None

    if provider == "ollama":
        url = f"{settings.ollama_base_url.rstrip('/')}/v1/chat/completions"
    else:
        url = PROVIDER_URLS.get(provider)
    if not url:
        return None

    b64 = base64.b64encode(image_jpeg).decode("ascii")
    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": instruction},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                },
            ],
        }
    ]

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
        from aria_core.llm_usage import (
            estimate_tokens_from_text,
            parse_usage_from_response,
            record_llm_usage,
        )

        prompt_est = estimate_tokens_from_text(instruction) + 256
        async with httpx.AsyncClient(timeout=90.0) as client:
            response = await client.post(
                url,
                headers=headers,
                json={
                    "model": model,
                    "messages": messages,
                    "temperature": 0.2,
                    "max_tokens": max_tokens,
                },
            )
            if response.status_code != 200:
                logger.warning("Vision LLM %s: %s", response.status_code, response.text[:300])
                record_llm_usage(
                    provider=provider,
                    model=model,
                    input_tokens=prompt_est,
                    output_tokens=0,
                    ok=False,
                    status_code=response.status_code,
                    kind="vision",
                    estimated=True,
                )
                return None
            data = response.json()
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
                provider=provider,
                model=model,
                input_tokens=usage["input_tokens"],
                output_tokens=usage["output_tokens"],
                ok=True,
                kind="vision",
                estimated=estimated,
            )
            return reply
    except Exception as exc:
        logger.warning("Vision LLM failed: %s", exc)
        return None