"""Indicateur coût réponse — gratuit vs payant + tokens consommés."""
from __future__ import annotations

from html import escape
from typing import Any

from aria_core.runtime import settings


def is_cloud_billed_provider() -> bool:
    provider = (settings.llm_provider or "").strip().lower()
    return provider not in ("", "none", "ollama")


def build_cost_meta(
    *,
    total_tokens: int,
    calls: int = 0,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> dict[str, Any]:
    cloud = is_cloud_billed_provider()
    billed = cloud and int(total_tokens) > 0
    return {
        "billed": billed,
        "cloud": cloud,
        "total_tokens": int(total_tokens),
        "input_tokens": int(input_tokens),
        "output_tokens": int(output_tokens),
        "calls": int(calls),
    }


def format_cost_footer(
    meta: dict[str, Any],
    *,
    lang: str = "fr",
    channel: str = "plain",
) -> str:
    """Suffixe fin de message. channel: plain | html | shell."""
    tokens = int(meta.get("total_tokens") or 0)
    billed = bool(meta.get("billed"))
    cloud = bool(meta.get("cloud", is_cloud_billed_provider()))

    if lang == "fr":
        if not cloud and tokens > 0:
            label = "local"
        elif billed:
            label = "payant"
        else:
            label = "gratuit"
    else:
        if not cloud and tokens > 0:
            label = "local"
        elif billed:
            label = "paid"
        else:
            label = "free"

    tok_part = f"{tokens} tok"

    if channel == "html":
        color = "#e67e22" if billed else "#27ae60"
        return (
            f'<br><br><span style="color:{color}">'
            f"{escape(label)} ({escape(tok_part)})"
            f"</span>"
        )
    if channel == "shell":
        if billed:
            return f"\n\n\033[38;5;208m{label} ({tok_part})\033[0m"
        return f"\n\n\033[38;5;46m{label} ({tok_part})\033[0m"

    icon = "🟠" if billed else "🟢"
    return f"\n\n{icon} {label} ({tok_part})"


def append_cost_footer(
    reply: str,
    meta: dict[str, Any],
    *,
    lang: str = "fr",
    channel: str = "plain",
) -> str:
    body = (reply or "").rstrip()
    return body + format_cost_footer(meta, lang=lang, channel=channel)