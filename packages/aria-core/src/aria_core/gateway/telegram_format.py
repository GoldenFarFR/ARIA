"""Plain-text cleanup for Telegram — avoid raw markdown symbols."""

from __future__ import annotations

import re

_HANDLE_TOKEN = "\uE000"
_HANDLE_END = "\uE001"


def _shield_handles(text: str) -> tuple[str, dict[str, str]]:
    """Protect @handles (underscores) before markdown-ish cleanup."""
    from aria_core.identity import official_telegram_bot_at, official_x_at

    tokens = [official_x_at(), official_telegram_bot_at()]
    placeholders: dict[str, str] = {}
    out = text
    for idx, token in enumerate(tokens):
        if not token or token not in out:
            continue
        key = f"{_HANDLE_TOKEN}{idx}{_HANDLE_END}"
        placeholders[key] = token
        out = out.replace(token, key)
    return out, placeholders


def _restore_handles(text: str, placeholders: dict[str, str]) -> str:
    out = text
    for key, token in placeholders.items():
        out = out.replace(key, token)
    return out


def plain_telegram(text: str) -> str:
    if not text:
        return ""
    out, placeholders = _shield_handles(text)
    out = re.sub(r"```[\w]*\n?", "", out)
    out = out.replace("```", "")
    out = re.sub(r"\*\*([^*]+)\*\*", r"\1", out)
    out = re.sub(r"__([^_]+)__", r"\1", out)
    out = re.sub(r"\*([^*]+)\*", r"\1", out)
    out = re.sub(r"_([^_]+)_", r"\1", out)
    out = re.sub(r"`([^`]+)`", r"\1", out)
    out = re.sub(r"^#{1,6}\s+", "", out, flags=re.MULTILINE)
    out = re.sub(r"^[-*]\s+", "", out, flags=re.MULTILINE)
    out = out.replace("→", " : ")
    out = re.sub(r"\n{3,}", "\n\n", out)
    out = _restore_handles(out, placeholders)
    return out.strip()