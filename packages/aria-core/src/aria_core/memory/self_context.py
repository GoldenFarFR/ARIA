"""ARIA identity / mission / goals questions — internal memory, no web."""
from __future__ import annotations

import os
import re
from pathlib import Path

_SELF_CONTEXT_RE = re.compile(
    r"(?:"
    r"qui\s+es[- ]?tu\b|who\s+are\s+you\b|"
    r"parle[- ]?(?:moi\s+)?de\s+toi|tell\s+me\s+about\s+yourself|"
    r"pourquoi\s+exist|why\s+do\s+you\s+exist|"
    r"existes?[- ]?tu\s+pour|"
    r"(?:tes?|quels?|quelles?)\s+objectifs?\b|your\s+goals?\b|"
    r"ta\s+mission\b|your\s+mission\b|"
    r"(?:tes?|quels?)\s+valeurs?\b|your\s+values?\b|"
    r"programm[ée]e?\s+par\s+goldenfar|goldenfarfr|"
    r"que\s+souhaites?[- ]?tu|what\s+do\s+you\s+want|"
    r"ton\s+identit[ée]|your\s+identity|"
    r"pr[ée]sente[- ]?toi|presente[- ]?toi|"
    r"ton\s+r[ôo]le|your\s+role|"
    r"\bca[n']?o\b|chief\s+autonomous|"
    r"pourquoi\s+as[- ]?tu\s+[ée]t[ée]|why\s+were\s+you\s+(?:made|created|built)"
    r")",
    re.IGNORECASE,
)

# Generic career-type questions — do not confuse with ARIA's identity
_EXTERNAL_OBJECTIVES_RE = re.compile(
    r"\b(?:carri[èe]re|entretien|recruteur|salaire|cv\b|linkedin)\b",
    re.IGNORECASE,
)


def is_self_context_question(message: str) -> bool:
    """True if the question is about ARIA's identity, mission, or goals."""
    text = (message or "").strip()
    if len(text) < 6:
        return False
    if _EXTERNAL_OBJECTIVES_RE.search(text) and not re.search(
        r"\b(?:aria|goldenfar|zhc|vanguard)\b", text, re.I
    ):
        return False
    return bool(_SELF_CONTEXT_RE.search(text))


SELF_CONTEXT_LLM_RULE = (
    "RÈGLE IDENTITÉ : question sur qui tu es, pourquoi tu existes, ta mission ou tes "
    "objectifs en tant qu'ARIA ZHC / GoldenFar — réponds UNIQUEMENT depuis les blocs "
    "identité, objectifs Phase F, valeurs et vision ci-dessus. "
    "Format : 3 à 8 phrases en français, structurées (qui / pourquoi / objectifs). "
    "Interdit : recherche web, coaching carrière, code PowerShell, scripts, dumps de "
    "fichiers repo, listes ingest-repo. Si une nuance manque, dis-le — n'invente pas.\n"
)

_VISION_BUDGET = 1200


def _vision_excerpt() -> str:
    root = Path(os.environ.get("ARIA_REPO_ROOT", Path.home() / "GitHub-Repos" / "ARIA"))
    for candidate in (root / "VISION.md", root / "vanguard" / "VISION.md"):
        if not candidate.is_file():
            continue
        try:
            text = candidate.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if text:
            return f"# Vision ARIA (extrait)\n{text[:_VISION_BUDGET]}"
    return ""


def build_self_identity_context(*, lang: str = "fr") -> str:
    """Minimal identity context — no vector or journal (avoids an ingest-repo dump)."""
    from aria_core.identity import x_identity_prompt
    from aria_core.memory.goals import get_goals_text
    from aria_core.memory.values import get_values_text
    from aria_core.narrative import llm_system_block

    parts = [
        "# Identité ARIA (mémoire interne — opérateur GoldenFar)",
        x_identity_prompt(),
        llm_system_block(lang)[:1800],
    ]
    goals = get_goals_text(lang=lang)
    if goals:
        parts.append(goals)
    values = get_values_text()
    if values:
        parts.append(values)
    vision = _vision_excerpt()
    if vision:
        parts.append(vision)
    return "\n\n".join(parts)