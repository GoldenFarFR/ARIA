"""Voix X @Aria_ZHC — prose humaine, sans tics « IA / agent autonome »."""

from __future__ import annotations

import re

# Marqueurs à éviter dans le texte public (pas une identité « personnage IA »).
_AI_VOICE_RE = re.compile(
    r"\b(?:"
    r"as an ai|i'?m aria\b|new zhc agent|zhc agent|autonomous agent|autonomous cao|"
    r"chief autonomous officer|cao of|ai agent|autonomous ai|autonomous holding ai|"
    r"without human operator|operator in the loop|#autonomousai|#ariazhc|"
    r"built in public:|vector memory|phases a-?d|ddg-only brain|truth ledger|"
    r"3-voice bridge|cursor-aria|grok/cursor skills"
    r")\b",
    re.IGNORECASE,
)

_ROSTER_RE = re.compile(
    r"^(?:built in public|since day one|shipped:)\s*:?",
    re.IGNORECASE,
)


def human_voice_rules_for_llm(lang: str = "en") -> str:
    """Bloc prompt — ton humain, pas personnage IA."""
    if lang == "fr":
        return (
            "VOIX HUMAINE (obligatoire) :\n"
            "- Écris comme un opérateur / fondateur crypto — pas comme une IA qui se présente.\n"
            "- Première personne (I / we) naturelle, contractions OK, phrases courtes.\n"
            "- Interdit : « as an AI », « autonomous agent », « ZHC agent », « CAO », "
            "« I'm ARIA — », listes à virgules de features, jargon stack (LLM, vector memory, phases A-D).\n"
            "- Pas de question forcée sauf si l'opérateur la demande.\n"
            "- Un fil : contexte → ce qu'on a livré → pourquoi ça compte. Faits vérifiés seulement."
        )
    return (
        "HUMAN VOICE (mandatory):\n"
        "- Sound like a real crypto holding operator — not an AI introducing itself.\n"
        "- Natural first person (I / we), contractions OK, short sentences.\n"
        "- Forbidden: « as an AI », « autonomous agent », « ZHC agent », « CAO », "
        "« I'm ARIA — », feature comma-lists, stack jargon (LLM, vector memory, phases A-D).\n"
        "- No forced engagement question unless the operator asked for one.\n"
        "- One arc: context → what we shipped → why it matters. Verified facts only."
    )


def has_ai_voice_markers(text: str) -> bool:
    return bool(_AI_VOICE_RE.search(text or ""))


def looks_like_feature_roster(text: str) -> bool:
    """Liste technique (virgules / deux-points catalogue)."""
    body = (text or "").strip()
    if not body:
        return False
    if _ROSTER_RE.search(body):
        return True
    commas = body.count(",")
    if commas >= 4 and len(body) > 120:
        return True
    return has_ai_voice_markers(body)


def strip_obvious_ai_phrases(text: str) -> str:
    """Nettoyage léger sans LLM."""
    out = (text or "").strip()
    if not out:
        return out
    replacements = (
        (r"^Built in public:\s*", ""),
        (r"\bOperator in the loop\.?\s*", ""),
        (r"\bautonomous\s+", ""),
        (r"\bAI\s+agent\b", "team"),
        (r"\bZHC agent\b", "team"),
    )
    for pattern, repl in replacements:
        out = re.sub(pattern, repl, out, flags=re.IGNORECASE)
    out = re.sub(r"\s{2,}", " ", out).strip()
    out = re.sub(r",\s*,", ", ", out)
    return out[:280]


async def humanize_tweet_for_x(text: str) -> str:
    """Réécriture LLM si le brouillon sonne « catalogue IA »."""
    from aria_core.handle_registry import resolve_handles_in_text
    from aria_core.x_publication_policy import check_tweet_content, policy_rules_for_llm

    body = strip_obvious_ai_phrases(text)
    if not body:
        return body
    if not looks_like_feature_roster(body) and check_tweet_content(body)[0]:
        return body[:280]

    from aria_core.llm import chat_with_context, is_llm_configured

    if not is_llm_configured():
        return body[:280]

    system = (
        f"{policy_rules_for_llm('en')}\n"
        f"{human_voice_rules_for_llm('en')}\n"
        "Rewrite as ONE English X tweet (max 280 chars).\n"
        "Keep verified facts and every @mention. Turn feature lists into 2-3 natural sentences.\n"
        "Output tweet text only — no quotes."
    )
    raw = await chat_with_context(body[:400], system, temperature=0.45, max_tokens=140)
    polished = (raw or "").strip().strip('"').strip("'")
    polished = resolve_handles_in_text(polished)
    if polished and check_tweet_content(polished)[0]:
        return polished[:280]
    return body[:280]