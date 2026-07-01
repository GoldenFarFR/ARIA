"""ARIA ZHC — identity and account setup."""

from __future__ import annotations

import re

from aria_core.runtime import settings

from aria_core.holding import DEFAULT_ARIA_TITLE, holding_name
from aria_core.narrative import x_bio as narrative_x_bio

ARIA_DISPLAY_NAME = "ARIA ZHC"
ARIA_TITLE = DEFAULT_ARIA_TITLE
ARIA_HANDLE = "Aria_ZHC"  # @Aria_ZHC on X
TELEGRAM_BOT_HANDLE = "Aria_ZHC_Bot"  # @Aria_ZHC_Bot on Telegram

# Variantes incorrectes du handle X (pas le bot Telegram)
_WRONG_X_HANDLE_PATTERNS = (
    r"@AriaZHC\b",
    r"@ariaZHC\b",
    r"@ARIAZHC\b",
    r"\bAriaZHC\b",
    r"\bariaZHC\b",
    r"x\.com/AriaZHC\b",
    r"twitter\.com/AriaZHC\b",
)

ARIA_EMAIL_SUGGESTED = "aria.zhc@yourdomain.com"
ARIA_BIO = narrative_x_bio()
ARIA_OPERATOR_NOTE = "Operated by human principal"

def _setup_steps() -> list[str]:
    from aria_core.narrative import setup_steps

    return setup_steps()


SETUP_STEPS = _setup_steps()


def official_x_handle() -> str:
    return (settings.aria_x_handle or ARIA_HANDLE).strip()


def official_telegram_bot_username() -> str:
    return (settings.telegram_bot_username or TELEGRAM_BOT_HANDLE).strip().lstrip("@")


def official_x_at() -> str:
    return f"@{official_x_handle()}"


def official_telegram_bot_at() -> str:
    return f"@{official_telegram_bot_username()}"


def official_x_url() -> str:
    return f"https://x.com/{official_x_handle()}"


def official_telegram_bot_url() -> str:
    return f"https://t.me/{official_telegram_bot_username()}"


def fix_handle_in_text(text: str) -> str:
    """Corrige les variantes incorrectes du handle X (ne touche pas @Aria_ZHC_Bot)."""
    handle = official_x_handle()
    result = text
    for pattern in _WRONG_X_HANDLE_PATTERNS:
        if "x.com" in pattern or "twitter.com" in pattern:
            result = re.sub(pattern, official_x_url(), result, flags=re.IGNORECASE)
        elif "@" in pattern:
            result = re.sub(pattern, f"@{handle}", result, flags=re.IGNORECASE)
        else:
            result = re.sub(pattern, handle, result, flags=re.IGNORECASE)
    return result


def x_identity_prompt() -> str:
    from aria_core.narrative import structure_block

    h = official_x_handle()
    bot = official_telegram_bot_username()
    return (
        f"HANDLE X OFFICIEL : @{h} — URL : {official_x_url()}\n"
        f"BOT TELEGRAM OFFICIEL : @{bot} — URL : {official_telegram_bot_url()}\n"
        f"INTERDIT sur X : AriaZHC, ariaZHC, @AriaZHC (sans underscore)\n"
        f"Sur Telegram, dirige les gens vers @{bot} — pas @{h}.\n"
        f"{structure_block('fr')}"
    )