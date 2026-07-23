"""Response cost indicator — free vs paid + tokens consumed."""
from __future__ import annotations

import re
from html import escape
from typing import Any

from aria_core.runtime import settings

_COST_META_RE = re.compile(
    r"(?:"
    r"pourquoi\s+(?:orange|payant|grok|l['']?api|api\s+cloud|tokens?|co[uû]t)"
    r"|why\s+(?:orange|paid|grok|the\s+api|api\s+cloud|tokens?|cost)"
    r"|c['']?est\s+quoi\s+(?:orange|payant|le\s+tok)"
    r"|(?:orange|payant|🟠).*(?:pourquoi|signifie|veut\s+dire)"
    r"|(?:pourquoi|why).*(?:orange|payant|🟠|grok\s+api)"
    r")",
    re.IGNORECASE,
)


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
    """End-of-message suffix. channel: plain | html | shell."""
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


def is_cost_meta_question(message: str) -> bool:
    """Question about the 🟢/🟠 indicator — template reply, no LLM."""
    text = (message or "").strip()
    if len(text) < 8:
        return False
    return bool(_COST_META_RE.search(text))


def cost_meta_reply(lang: str = "fr") -> str:
    """Explains orange/free — 0 API call."""
    from aria_core.llm_economy import provider_display_name

    prov = provider_display_name()
    if lang == "fr":
        return (
            f"🟠 orange = j'ai appelé le LLM cloud ({prov}) pour cette réponse "
            f"(input + output facturés).\n"
            f"🟢 vert = pas d'appel cloud (template, skill direct, ou ack).\n"
            f"Cette explication est gratuite — pas de nouvel appel Grok."
        )
    return (
        f"🟠 orange = I called the cloud LLM ({prov}) for that reply "
        f"(input + output billed).\n"
        f"🟢 green = no cloud call (template, direct skill, or ack).\n"
        f"This explanation is free — no new Grok call."
    )


def append_cost_footer(
    reply: str,
    meta: dict[str, Any],
    *,
    lang: str = "fr",
    channel: str = "plain",
) -> str:
    body = (reply or "").rstrip()
    return body + format_cost_footer(meta, lang=lang, channel=channel)