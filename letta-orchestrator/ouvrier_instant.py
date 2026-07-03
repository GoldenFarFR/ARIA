"""Réponses instinctives ouvrier — salutations / small talk sans LLM ni preflight."""
from __future__ import annotations

import re

from aria_config import COMPLEX_HINTS, MOYEN_HINTS, SIMPLE_HINTS

_WELLBEING_RE = re.compile(
    r"(?i)\b(forme|ça va|ca va|comment vas|tu vas bien|how are you|la forme)\b"
)
_GREETING_RE = re.compile(
    r"^\s*(bonjour|salut|coucou|hello|hi|hey|bonsoir|gm|gn)\b",
    re.IGNORECASE,
)
_SOCIAL_RE = re.compile(
    r"^\s*(merci|thanks|thx|bravo|super|génial|genial|excellent|cool|nice)\b",
    re.IGNORECASE,
)
_OPS_RE = re.compile(
    r"(?i)\b(worker|pending|handoff|download|inbox|acp|deploy|commit|fix|implément|"
    r"telegram|notif|preuve|ping|vault|render|github|pytest|fichier|code|offre|workflow|"
    r"regarde|vérifie|verifie|vas[- ]?y|lance|continue)\b"
)
_CONTINUATION_RE = re.compile(
    r"(?i)^\s*(?:d'?\s*accord|dac+ord|ok|oui|go|vas[- ]?y|regarde|vérifie|verifie|check|fais[- ]?le)\b"
)
_OPINION_RE = re.compile(
    r"(?i)(?:tu en pense|ton avis|qu'en pense|tu penses quoi|en penses?-tu)"
)


def is_simple_exchange(message: str) -> bool:
    """Échange court — pas de raisonnement visible ni bootstrap."""
    text = (message or "").strip()
    if not text or len(text) > 120:
        return False
    if _CONTINUATION_RE.search(text) or _OPINION_RE.search(text):
        return False
    if _OPS_RE.search(text):
        return False
    low = text.lower()
    if any(h in low for h in COMPLEX_HINTS):
        return False
    if any(h in low for h in MOYEN_HINTS):
        return False
    if _GREETING_RE.search(text):
        return True
    if _SOCIAL_RE.search(text) and len(text) < 80:
        return True
    if _WELLBEING_RE.search(text) and len(text) < 80 and "?" in text:
        return True
    if any(h in low for h in SIMPLE_HINTS) and len(text) < 80:
        return True
    if (
        len(text) < 18
        and "?" not in text
        and not any(c in text for c in ("{", "}", "/", "\\"))
        and not _CONTINUATION_RE.search(text)
    ):
        return True
    return False


def instant_reply(message: str) -> str:
    """Réponse humaine courte — opérateur Sylvain."""
    text = (message or "").strip()
    low = text.lower()
    wellbeing = bool(_WELLBEING_RE.search(text))

    if _GREETING_RE.search(text):
        if re.search(r"(?i)\bquoi de neuf|what'?s new\b", text):
            return "Pas grand-chose de spécial — dis-moi si tu veux que je vérifie ou corrige quelque chose."
        if wellbeing or "?" in text:
            return "Salut Sylvain ! Ça va bien de mon côté — et toi ?"
        if low.startswith("gm"):
            return "GM Sylvain !"
        if low.startswith("gn"):
            return "GN Sylvain — bonne nuit."
        return "Salut Sylvain !"

    if _SOCIAL_RE.search(text):
        if re.match(r"^\s*(merci|thanks|thx)\b", low):
            return "Avec plaisir !"
        return "Merci Sylvain !"

    if wellbeing:
        return "Ça va bien — merci. Et toi ?"

    return "OK."