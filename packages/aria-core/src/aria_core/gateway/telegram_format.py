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
    # 18/07 -- real bug found while testing a reply mentioning two snake_case
    # identifiers (e.g. "safety_screen.py ... momentum_entry.py"): the previous version
    # of these two regexes (_([^_]+)_ / __([^_]+)__) had no notion of a
    # word boundary -- the first "_" of "safety_screen" was pairing with the second
    # "_" of "momentum_entry" and ALL the text between them (several sentences) was
    # treated as a single markdown italic span, silently stripping both
    # underscores and leaving "safetyscreen.py"/"momentumentry.py". The lookarounds
    # below require a non-word boundary (space/punctuation/start-end of string)
    # on both sides of the delimiter -- an underscore internal to an identifier
    # (preceded/followed by an alphanumeric character or another underscore) is never
    # consumed as a markdown delimiter anymore.
    out = re.sub(r"(?<![\w_])__([^_\n]+?)__(?![\w_])", r"\1", out)
    out = re.sub(r"\*([^*]+)\*", r"\1", out)
    out = re.sub(r"(?<![\w_])_([^_\n]+?)_(?![\w_])", r"\1", out)
    out = re.sub(r"`([^`]+)`", r"\1", out)
    out = re.sub(r"^#{1,6}\s+", "", out, flags=re.MULTILINE)
    out = re.sub(r"^[-*]\s+", "", out, flags=re.MULTILINE)
    out = out.replace("→", " : ")
    out = re.sub(r"\n{3,}", "\n\n", out)
    out = _restore_handles(out, placeholders)
    return out.strip()