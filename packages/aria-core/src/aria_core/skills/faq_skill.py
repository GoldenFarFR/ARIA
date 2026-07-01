"""FAQ skill — structured answers from ARIA-owned content."""

from __future__ import annotations

from aria_core.content.service import format_faq_reply, search_faq


async def execute_faq_lookup(user_message: str, lang: str = "en") -> tuple[str, dict]:
    matches = search_faq(user_message, limit=3)
    reply = format_faq_reply(matches, lang)
    return reply, {
        "matches": [m.get("id") for m in matches],
        "count": len(matches),
        "source": "faq.yaml",
    }