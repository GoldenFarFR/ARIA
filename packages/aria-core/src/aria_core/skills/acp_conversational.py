"""Détection questions ACP conversationnelles (revenus, état) — tolère typos Sylvain."""
from __future__ import annotations

import re

# Typos courants : ganger, largent, tu a / t'a
_CONVERSATIONAL_ACP_RE = re.compile(
    r"(?i)(?:"
    r"comment\s+(?:se\s+)?passe|"
    r"ça\s+va|ca\s+va|"
    r"(?:gagn\w*|ganger)\s+(?:de\s+)?l['\s]?argent|"
    r"de\s+l['\s]?argent|"
    r"\blargent\b|"
    r"tu\s+(?:as|a|es)\s+(?:gagn\w+|ganger|fait)|"
    r"t['\s]?as\s+(?:gagn\w+|ganger|fait)|"
    r"combien\s+(?:as[- ]tu|tu\s+as|de\s+)|"
    r"rapporte(?:r)?\s+(?:de\s+)?l['\s]?argent|"
    r"tu\s+(?:as|a)\s+gagn"
    r")",
)


def is_conversational_acp_question(message: str) -> bool:
    """Question humaine sur revenus / console ACP (pas commande technique)."""
    text = (message or "").strip()
    if not text:
        return False
    if not re.search(r"(?i)\bacp\b", text):
        return False
    return bool(_CONVERSATIONAL_ACP_RE.search(text))