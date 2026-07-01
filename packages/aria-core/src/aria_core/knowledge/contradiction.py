"""Détecteur de contradictions — claim vs noyau épistémique + FAQ."""

from __future__ import annotations

import re

from aria_core.grounding import faq_direct_answer
from aria_core.knowledge.epistemic import search_epistemic, THRESHOLD_AFFIRM


def _incorrect_dexpulse_holding_claim(text: str) -> bool:
    """True only when reply wrongly presents DEXPulse (or flagship) as the holding."""
    lower = text.lower()
    return bool(
        re.search(
            r"dexpulse\s+(?:est|is)\s+(?:la\s+)?(?:holding|mère|parent|mother)",
            lower,
        )
        or re.search(
            r"(?:holding|mère|parent)\s+(?:est|is)\s+dexpulse",
            lower,
        )
        or re.search(
            r"dexpulse\s+(?:n'est pas|nest pas|is not)\s+(?:une\s+)?filiale",
            lower,
        )
    )


def check_contradiction(claim: str, lang: str = "fr") -> tuple[bool, str]:
    """
    Return (has_conflict, explanation).
    Heuristique : match fort épistémique avec p_true élevé dont le claim contredit le texte.
    """
    lower = claim.lower()
    matches = search_epistemic(claim, limit=2, static_only=True)
    for m in matches:
        if m.score < 8:
            continue
        p = float(m.claim.get("p_true", 0))
        if p < THRESHOLD_AFFIRM:
            continue
        cid = m.claim.get("id", "?")
        if cid == "holding-vs-dexpulse" and _incorrect_dexpulse_holding_claim(lower):
            return True, f"Conflit avec politique [{cid}]"

    faq_reply, faq_data = faq_direct_answer(claim, lang)
    if faq_reply and faq_data.get("faq_direct"):
        if re.search(r"pas la holding|not the holding", faq_reply.lower()):
            if _incorrect_dexpulse_holding_claim(lower):
                return True, "Conflit avec FAQ holding/DEXPulse"

    return False, ""