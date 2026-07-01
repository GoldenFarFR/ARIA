"""Triage universel mémoire — filtre Groq avant écriture cognitive."""

from __future__ import annotations

from aria_core.knowledge.x_insight_relevance import (
    assess_x_insight_for_memory,
    format_assessment_log,
)


async def assess_content_for_memory(
    content: str,
    *,
    source: str = "manual",
    topic: str = "general",
) -> tuple[bool, str, float]:
    """
    Return (store, reason, confidence).
    Réutilise le triage X (PERTINENT/FAIT/CONSERVER).
    """
    assessment = await assess_x_insight_for_memory(
        f"[{topic}] {content}",
        source=source,
    )
    return assessment.store, format_assessment_log(assessment), assessment.confidence


async def triaged_add_knowledge(
    source: str,
    topic: str,
    content: str,
    *,
    confidence: float = 0.5,
    approved: bool = False,
    skip_triage: bool = False,
):
    """add_knowledge avec triage — skip si calibré opérateur ou déjà approuvé."""
    from aria_core.knowledge.cognitive import add_knowledge

    if not skip_triage:
        store, reason, conf = await assess_content_for_memory(
            content, source=source, topic=topic,
        )
        if not store:
            return None, reason
        confidence = max(confidence, conf)

    item = await add_knowledge(
        source=source,
        topic=topic,
        content=content,
        confidence=confidence,
        approved=approved,
    )
    return item, "stored"