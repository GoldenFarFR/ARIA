"""Epistemic skill — calibrated beliefs from epistemic_core.yaml."""

from __future__ import annotations

from aria_core.knowledge.epistemic import (
    epistemic_direct_answer,
    format_epistemic_reply,
    search_epistemic,
)


async def execute_epistemic_check(user_message: str, lang: str = "en") -> tuple[str, dict]:
    direct, data = epistemic_direct_answer(user_message, lang)
    if direct:
        return direct, data
    matches = search_epistemic(user_message, limit=3)
    if not matches:
        if lang == "fr":
            return (
                "Aucune entrée du noyau épistémique ne correspond. "
                "Reformule ou pose une question sur la holding, DEXPulse, ZHC ou les règles ARIA.",
                {"epistemic_direct": False, "count": 0},
            )
        return (
            "No epistemic core entry matches. "
            "Rephrase or ask about the holding, DEXPulse, ZHC, or ARIA rules.",
            {"epistemic_direct": False, "count": 0},
        )
    parts = []
    for m in matches:
        parts.append(format_epistemic_reply(m, lang))
    return "\n\n---\n\n".join(parts), {
        "epistemic_direct": False,
        "match_ids": [m.claim.get("id") for m in matches],
        "count": len(matches),
        "source": "epistemic_core.yaml",
    }