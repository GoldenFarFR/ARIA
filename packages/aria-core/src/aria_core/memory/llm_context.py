"""Contexte LLM unifié — Phase D (journal + cognitive + vector opt-in)."""
from __future__ import annotations

import re
from typing import Any

from aria_core.memory._legacy_journal import (
    get_doctrine_text,
    get_journal_summary,
    get_launchpad_doctrine_text,
    get_persona_text,
)

_VECTOR_RECALL_LIMIT = 5
_VECTOR_RECALL_BUDGET = 1200
_CONTEXT_MAX_CHARS = 8000

_REDACT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:sk|rnd|ghp|gho|ghu|ghs|ghr|xai)-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._-]{12,}\b", re.I),
    re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    re.compile(r"\b(?:api[_-]?key|secret|password|token)\s*[:=]\s*\S+\b", re.I),
)


def sanitize_recall_text(text: str) -> str:
    """Retire secrets / PII évidents avant injection LLM."""
    out = (text or "").strip()
    if not out:
        return ""
    for pattern in _REDACT_PATTERNS:
        out = pattern.sub("[redacted]", out)
    return out


def _last_user_message(messages: list[dict[str, Any]]) -> str:
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = (msg.get("content") or "").strip()
            if content:
                return content[:500]
    return ""


async def _fetch_recent_messages(
    *,
    public: bool,
    visitor_id: str | None,
) -> list[dict[str, Any]]:
    try:
        from aria_core import repertoire_db

        return await repertoire_db.get_messages(
            limit=8,
            visitor_id=visitor_id if public else None,
        )
    except Exception:
        return []


async def fetch_vector_recall(
    query: str,
    *,
    limit: int = _VECTOR_RECALL_LIMIT,
    budget_chars: int = _VECTOR_RECALL_BUDGET,
) -> str:
    """Rappel sémantique Chroma — chaîne vide si flag off ou query trop courte."""
    from aria_core.memory.vector import is_vector_enabled, search

    if not is_vector_enabled():
        return ""
    q = sanitize_recall_text((query or "").strip())
    if len(q) < 8:
        return ""
    hits = await search(q, limit=limit)
    if not hits:
        return ""
    lines: list[str] = []
    used = 0
    for hit in hits:
        raw = hit.get("content") or ""
        content = sanitize_recall_text(raw)
        if not content or content.strip() == "[redacted]":
            continue
        meta = hit.get("metadata") or {}
        entry_type = meta.get("entry_type") or "memory"
        topic = meta.get("topic") or ""
        line = f"- [{entry_type}/{topic}] {content[:300]}"
        if used + len(line) + 1 > budget_chars:
            break
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines)


def _query_hint_for_vector(
    messages: list[dict[str, Any]],
    *,
    public: bool,
) -> str:
    hint = _last_user_message(messages)
    if hint:
        return hint
    if public:
        return ""
    journal = get_journal_summary()
    if journal and "Aucune mémoire" not in journal:
        return journal[:300]
    return ""


async def build_llm_context(
    *,
    public: bool = False,
    visitor_id: str | None = None,
    query_hint: str | None = None,
) -> str:
    from aria_core.directives import get_directives_text
    from aria_core.identity import x_identity_prompt
    from aria_core.narrative import public_llm_system_block

    messages = await _fetch_recent_messages(public=public, visitor_id=visitor_id)
    vector_query = (query_hint or "").strip() or _query_hint_for_vector(messages, public=public)

    if public:
        parts = ["# Identité ARIA (mode public)", public_llm_system_block("en")]
    else:
        parts = [
            "# Identité ARIA",
            x_identity_prompt(),
            get_persona_text()[:2000],
        ]
        from aria_core.memory.collegue import get_collegue_text

        collegue_block = get_collegue_text()
        if collegue_block:
            parts.append("\n# Mémoire collègue (COLLEGUE.md — SSOT opérateur operateur)")
            parts.append(collegue_block)
        from aria_core.memory.arbitrator import get_arbitration_text, run_memory_arbitration

        arbitration = await run_memory_arbitration(messages=messages, query_hint=vector_query)
        arbitration_block = get_arbitration_text(arbitration)
        if arbitration_block:
            parts.append(f"\n{arbitration_block}")
        from aria_core.memory.values import get_values_text

        values_block = get_values_text()
        if values_block:
            parts.append(f"\n{values_block}")
        from aria_core.memory.goals import get_goals_text

        goals_block = get_goals_text()
        if goals_block:
            parts.append(f"\n{goals_block}")
        from aria_core.memory.reflection import get_reflections_text

        reflection_block = get_reflections_text()
        if reflection_block:
            parts.append(f"\n{reflection_block}")
        recall = await fetch_vector_recall(vector_query)
        if arbitration.conflicts:
            suppressed_vector = {s.content[:80] for s in arbitration.suppressed if s.layer == "vector"}
            if suppressed_vector and recall:
                recall_lines = [ln for ln in recall.splitlines() if not any(p in ln for p in suppressed_vector)]
                recall = "\n".join(recall_lines)
        if recall:
            parts.append("\n# Rappel sémantique (mémoire vectorielle — opt-in)")
            parts.append(recall)
        directives = get_directives_text()
        if directives:
            parts.append("\n# Directives opérateur (priorité haute)")
            parts.append(directives)
        parts.extend([
            "\n# Journal récent (mémoire épisodique)",
            get_journal_summary(),
        ])
        try:
            from aria_core.knowledge.cognitive import get_approved

            knowledge = await get_approved()
            if knowledge:
                parts.append("\n# Connaissances approuvées (mémoire sémantique)")
                for item in knowledge[:15]:
                    parts.append(f"- [{item.topic}] {item.content[:200]}")
        except Exception:
            pass

    doctrine = get_doctrine_text()
    if doctrine:
        parts.append("\n# Doctrine engineering (optimization queen)")
        parts.append(doctrine[:2000])
    launchpads = get_launchpad_doctrine_text()
    if launchpads:
        parts.append("\n# Doctrine BASE launchpads (know by heart)")
        parts.append(launchpads[:1500])
    if not public:
        try:
            from aria_core.training_portfolio import portfolio_summary

            parts.append("\n# Portefeuille d'entraînement (fictif — SSOT business sim)")
            parts.append(portfolio_summary()[:1200])
        except Exception:
            pass
        try:
            from aria_core.skills.holding_site_skill import _read_initiative

            initiative = _read_initiative()
            if initiative:
                parts.append("\n# Initiative site holding (priorité autonome #1)")
                parts.append(initiative[:1000])
        except Exception:
            pass
        try:
            from aria_core.skills.entrepreneur_skill import _read_initiative as _ent_init

            ent = _ent_init()
            if ent:
                parts.append("\n# Initiative entrepreneuse (objectif 50 $/mois réel)")
                parts.append(ent[:1000])
        except Exception:
            pass
    if messages:
        parts.append("\n# Conversations récentes")
        for msg in messages:
            role = "User" if msg["role"] == "user" else "ARIA"
            parts.append(f"{role}: {msg['content'][:150]}")

    return _assemble_context(parts, max_chars=_CONTEXT_MAX_CHARS)


def _assemble_context(parts: list[str], *, max_chars: int) -> str:
    """Assemble le contexte en préservant les sections mémoire prioritaires."""
    full = "\n".join(parts)
    if len(full) <= max_chars:
        return full
    priority_markers = (
        "# Rappel sémantique (mémoire vectorielle — opt-in)",
        "Arbitre mémoire ARIA",
        "# Réflexion opérationnelle ARIA (Phase G)",
        "# Connaissances approuvées (mémoire sémantique)",
        "# Journal récent (mémoire épisodique)",
    )
    preserved: list[str] = []
    for marker in priority_markers:
        idx = full.find(marker)
        if idx == -1:
            continue
        line_start = full.rfind("\n", 0, idx) + 1
        if line_start > 0 and full[line_start:idx].strip().startswith("#"):
            idx = line_start
        next_idx = len(full)
        for other in priority_markers:
            if other == marker:
                continue
            pos = full.find(other, idx + len(marker))
            if pos != -1:
                next_idx = min(next_idx, pos)
        preserved.append(full[idx:next_idx].strip())
    head_budget = max(2000, max_chars // 2)
    head = full[:head_budget].rstrip()
    reserved = "\n\n".join(preserved)
    if len(head) + len(reserved) + 2 <= max_chars:
        return f"{head}\n\n{reserved}"[:max_chars]
    if reserved and len(reserved) <= max_chars:
        return reserved[:max_chars]
    return full[:max_chars]