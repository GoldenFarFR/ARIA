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
    # 18/07 -- bug réel trouvé en testant une réponse mentionnant deux identifiants
    # snake_case (ex. "safety_screen.py ... momentum_entry.py") : la version précédente
    # de ces deux regex (_([^_]+)_ / __([^_]+)__) ne connaissait aucune notion de
    # frontière de mot -- le premier "_" de "safety_screen" s'appariait avec le second
    # "_" de "momentum_entry" et TOUT le texte entre les deux (plusieurs phrases) était
    # traité comme un unique span d'italique markdown, effaçant silencieusement les deux
    # underscores et laissant "safetyscreen.py"/"momentumentry.py". Les lookaround
    # ci-dessous exigent une frontière non-mot (espace/ponctuation/début-fin de chaîne)
    # de part et d'autre du délimiteur -- un underscore interne à un identifiant
    # (précédé/suivi d'un caractère alphanumérique ou d'un autre underscore) n'est plus
    # jamais consommé comme délimiteur markdown.
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