"""Classifies operator orders to ARIA vs general information questions."""

from __future__ import annotations

import re
from enum import Enum


class OperatorMessageKind(str, Enum):
    SELF_DIRECTIVE = "self_directive"
    CURIOSITY_GAP = "curiosity_gap"
    GENERAL_INFO = "general_info"
    OTHER = "other"


class SelfMaintenanceAction(str, Enum):
    UPDATE_X_BANNER = "update_x_banner"
    UPDATE_X_AVATAR = "update_x_avatar"
    CURIOSITY_X_BANNER = "curiosity_x_banner"


_SELF_PRONOUN_RE = re.compile(
    r"\b(ta|ton|tes|toi|tu|te)\b",
    re.IGNORECASE,
)
_BANNER_RE = re.compile(r"banni[eè]re|banner", re.IGNORECASE)
_AVATAR_RE = re.compile(
    r"avatar|photo de profil|profile photo|profil(?!\s*il)",
    re.IGNORECASE,
)
_DIRECTIVE_VERB_RE = re.compile(
    r"\b(tu\s+(?:dois|vas|va|peux|pourrais|devrais|ferais|va[s]?)|"
    r"mets|met|mettre|mettra|change|maj|mise\s+a\s+jour|update|ajoute|"
    r"passe\s+a\s+l['']action|fais[- ]le)\b",
    re.IGNORECASE,
)
_CURIOSITY_BANNER_RE = re.compile(
    r"(?:je\s+vois|quand\s+je\s+vois|les?\s+autres|leur|sur\s+x|compte\s+x|"
    r"belle?\s+banni|sans\s+banni|pas\s+de\s+banni|"
    r"comment\s+(?:tu\s+)?pourrais|tu\s+ne\s+as\s+pas|tu\s+n['']as\s+pas)",
    re.IGNORECASE,
)
_GENERAL_HOWTO_RE = re.compile(
    r"^(?:comment|how\s+to|how\s+do\s+i|pourquoi\s+les\s+gens)\b",
    re.IGNORECASE,
)
_IMPERSONAL_RE = re.compile(
    r"\b(une\s+banni[eè]re\s+(?:twitter|x)|sur\s+(?:twitter|x)\s+(?:en\s+)?g[eé]n[eé]ral|"
    r"les\s+utilisateurs|n['']importe\s+quel\s+compte)\b",
    re.IGNORECASE,
)


def is_operator_self_directive(message: str) -> bool:
    return classify_operator_message(message) == OperatorMessageKind.SELF_DIRECTIVE


def classify_operator_message(message: str) -> OperatorMessageKind:
    text = (message or "").strip()
    if len(text) < 6:
        return OperatorMessageKind.OTHER

    lower = text.lower()
    has_self = bool(_SELF_PRONOUN_RE.search(lower))
    has_banner = bool(_BANNER_RE.search(lower)) and not re.search(
        r"\b(?:ignore|sans|pas de|no|skip|zero)\b.{0,24}\b(?:banni|banner)\b",
        lower,
    )
    has_directive = bool(_DIRECTIVE_VERB_RE.search(lower))

    if _GENERAL_HOWTO_RE.search(text) and _IMPERSONAL_RE.search(lower):
        return OperatorMessageKind.GENERAL_INFO
    if _GENERAL_HOWTO_RE.search(text) and not has_self:
        return OperatorMessageKind.GENERAL_INFO

    if has_banner and has_self and has_directive:
        return OperatorMessageKind.SELF_DIRECTIVE

    if has_banner and has_self and re.search(r"\?", text):
        if re.search(r"\b(tu\s+va[s]?|tu\s+vas|tu\s+peux|tu\s+dois)\b", lower):
            return OperatorMessageKind.SELF_DIRECTIVE

    if has_banner and (_CURIOSITY_BANNER_RE.search(lower) or (has_self and "?" in text)):
        if has_self or re.search(r"\b(aria|zhc|toi)\b", lower):
            return OperatorMessageKind.CURIOSITY_GAP

    if has_self and has_directive and _AVATAR_RE.search(lower):
        return OperatorMessageKind.SELF_DIRECTIVE

    return OperatorMessageKind.OTHER


def parse_self_maintenance_action(message: str) -> SelfMaintenanceAction | None:
    kind = classify_operator_message(message)
    lower = (message or "").lower()

    if _BANNER_RE.search(lower):
        if kind == OperatorMessageKind.CURIOSITY_GAP:
            return SelfMaintenanceAction.CURIOSITY_X_BANNER
        if kind == OperatorMessageKind.SELF_DIRECTIVE:
            return SelfMaintenanceAction.UPDATE_X_BANNER
        if kind in (OperatorMessageKind.SELF_DIRECTIVE, OperatorMessageKind.CURIOSITY_GAP):
            return SelfMaintenanceAction.UPDATE_X_BANNER

    if kind == OperatorMessageKind.SELF_DIRECTIVE and _AVATAR_RE.search(lower):
        return SelfMaintenanceAction.UPDATE_X_AVATAR

    return None