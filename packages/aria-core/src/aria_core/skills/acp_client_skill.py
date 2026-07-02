"""ACP marketplace client skill — stub until full ACP v2 lot is committed."""

from __future__ import annotations

import re

_ACP_RE = re.compile(
    r"\b(?:acp|virtuals|marketplace|offering|job\s+acp)\b",
    re.IGNORECASE,
)


def wants_acp_marketplace(message: str) -> bool:
    return bool(_ACP_RE.search((message or "").strip()))


async def execute_acp_marketplace(message: str, lang: str = "en") -> tuple[str, dict]:
    if lang == "fr":
        return (
            "ACP marketplace — intégration locale en cours (lot non déployé prod).\n"
            "Commandes prévues : `acp status`, browse offerings, traiter jobs.",
            {"acp": "stub"},
        )
    return (
        "ACP marketplace — local integration in progress (not deployed to prod yet).\n"
        "Planned commands: `acp status`, browse offerings, process jobs.",
        {"acp": "stub"},
    )