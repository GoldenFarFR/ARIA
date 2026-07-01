"""Filter X insights — Groq triage: pertinent, true/false, store (ZHC scope)."""
from __future__ import annotations

import re
from dataclasses import dataclass

_SPAM_ONLY = re.compile(
    r"^(?:thanks?|thank you|thx|lol|lmao|gm|gn|nice|cool|ok|okay|\+1|"
    r"yes|no|oui|non|great|awesome|agreed?)[\s!.?🔥👍❤️]*$",
    re.IGNORECASE,
)

_OFF_TOPIC = (
    "100x",
    "moon",
    "pump",
    "gem",
    "nfa",
    "buy now",
    "price target",
    "guaranteed",
    "financial advice",
    "airdrop hunter",
    "presale",
    "wen token",
)

_ZHC_LEARNING_AXES = (
    "zhc",
    "holding",
    "vanguard",
    "dexpulse",
    "autonom",
    "marketing",
    "narrative",
    "revenue",
    "product",
    "strategy",
    "moat",
    "telegram",
    "goldenfar",
)

_GROQ_TRIAGE_PROMPT = """Tu es ARIA, CAO de Aria Vanguard ZHC (modèle ZHC) et opératrice de DEXPulse.

Évalue ce texte venant de X pour décider s'il entre en mémoire cognitive.

Réponds EXACTEMENT 4 lignes (rien d'autre) :
PERTINENT: OUI ou NON — utile pour ZHC, autonomie holding, produit DEXPulse, marketing/comms futur ?
FAIT: VRAI ou FAUX ou INCERTAIN ou OPINION — affirmation factuelle vérifiable, fausse/hype, incertaine, ou conseil/stratégie ?
CONSERVER: OUI ou NON — mémoriser seulement si pertinent ET pas FAUX/hype ; OPINION/INCERTAIN OK si pertinent pour décisions marketing/autonomie
RAISON: <12 mots max en français>"""


@dataclass(frozen=True)
class InsightAssessment:
    store: bool
    pertinent: bool
    truth: str  # true | false | uncertain | opinion | n/a
    reason: str
    confidence: float
    groq_used: bool


def _prefilter_junk(text: str) -> tuple[bool, str]:
    """Return (skip_groq, reason). Junk never reaches Groq."""
    body = text.strip()
    if _SPAM_ONLY.match(body):
        return True, "spam"
    if len(body) < 12:
        return True, "too_short"
    if body.startswith("RT @"):
        return True, "retweet"
    lower = body.lower()
    if any(off in lower for off in _OFF_TOPIC):
        return True, "off_topic_hype"
    return False, ""


def _parse_groq_triage(raw: str) -> InsightAssessment:
    pertinent = False
    truth = "uncertain"
    store = False
    reason = "groq_parse"

    for line in (raw or "").strip().splitlines():
        upper = line.strip().upper()
        if upper.startswith("PERTINENT:"):
            pertinent = "OUI" in upper or "YES" in upper
        elif upper.startswith("FAIT:"):
            if "FAUX" in upper or "FALSE" in upper:
                truth = "false"
            elif "VRAI" in upper or "TRUE" in upper:
                truth = "true"
            elif "OPINION" in upper:
                truth = "opinion"
            else:
                truth = "uncertain"
        elif upper.startswith("CONSERVER:"):
            store = "OUI" in upper or "YES" in upper
        elif upper.startswith("RAISON:"):
            reason = line.split(":", 1)[-1].strip()[:80] or reason

    if truth == "false":
        store = False
        reason = f"fait_faux: {reason}"

    confidence = 0.55
    if store and truth == "true":
        confidence = 0.9
    elif store and truth == "opinion":
        confidence = 0.7
    elif store and truth == "uncertain":
        confidence = 0.6

    return InsightAssessment(
        store=store,
        pertinent=pertinent,
        truth=truth,
        reason=reason,
        confidence=confidence,
        groq_used=True,
    )


def _fallback_without_groq(text: str) -> InsightAssessment:
    lower = text.lower()
    zhc = any(axis in lower for axis in _ZHC_LEARNING_AXES) and len(text) >= 20
    return InsightAssessment(
        store=zhc,
        pertinent=zhc,
        truth="uncertain" if zhc else "n/a",
        reason="zhc_axis_fallback" if zhc else "no_groq",
        confidence=0.5 if zhc else 0.0,
        groq_used=False,
    )


async def assess_x_insight_for_memory(
    text: str,
    *,
    source: str = "x_twitter",
) -> InsightAssessment:
    """Groq triage on every non-junk X insight before cognitive storage."""
    skip, reason = _prefilter_junk(text)
    if skip:
        return InsightAssessment(
            store=False,
            pertinent=False,
            truth="n/a",
            reason=reason,
            confidence=0.0,
            groq_used=False,
        )

    from aria_core.llm import chat_with_context, is_llm_configured

    if not is_llm_configured():
        return _fallback_without_groq(text)

    raw = await chat_with_context(
        text[:500],
        _GROQ_TRIAGE_PROMPT,
        temperature=0.0,
        max_tokens=80,
    )
    if not raw or "PERTINENT:" not in raw.upper():
        return InsightAssessment(
            store=False,
            pertinent=False,
            truth="uncertain",
            reason="groq_empty",
            confidence=0.0,
            groq_used=True,
        )
    return _parse_groq_triage(raw)


async def assess_x_insight_relevance(
    text: str,
    *,
    source: str = "x_twitter",
) -> tuple[bool, str]:
    """Backward-compatible: (store, reason)."""
    assessment = await assess_x_insight_for_memory(text, source=source)
    label = assessment.reason
    if assessment.groq_used:
        label = f"{label}|pertinent={assessment.pertinent}|fait={assessment.truth}"
    return assessment.store, label


def format_assessment_log(assessment: InsightAssessment) -> str:
    if not assessment.groq_used:
        return assessment.reason
    return (
        f"pertinent={'oui' if assessment.pertinent else 'non'} "
        f"fait={assessment.truth} conserver={'oui' if assessment.store else 'non'} "
        f"— {assessment.reason}"
    )