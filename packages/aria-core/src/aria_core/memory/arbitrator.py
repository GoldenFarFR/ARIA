"""Arbitre mémoire ARIA — Phase H (court / moyen / long terme + résolution conflits)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from aria_core.memory.llm_context import sanitize_recall_text
from aria_core.paths import memory_dir

_CONFIG_PATH = Path(__file__).resolve().parents[1] / "knowledge" / "aria_arbitrator.yaml"
_LOG_FILE = "arbitration.jsonl"


@dataclass(frozen=True)
class MemorySnippet:
    layer: str
    tier: str
    priority: int
    content: str
    source_id: str = ""


@dataclass
class ArbitrationResult:
    kept: list[MemorySnippet] = field(default_factory=list)
    suppressed: list[MemorySnippet] = field(default_factory=list)
    conflicts: list[dict[str, str]] = field(default_factory=list)
    tier_counts: dict[str, int] = field(default_factory=dict)


def is_arbitrator_enabled() -> bool:
    from aria_core.runtime import get_settings

    return bool(getattr(get_settings(), "aria_memory_arbitrator", True))


@lru_cache(maxsize=1)
def _load_config() -> dict[str, Any]:
    if not _CONFIG_PATH.is_file():
        return {}
    try:
        raw = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8")) or {}
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _layer_meta() -> dict[str, dict[str, Any]]:
    cfg = _load_config()
    out: dict[str, dict[str, Any]] = {}
    for item in cfg.get("layers") or []:
        if isinstance(item, dict) and item.get("id"):
            out[str(item["id"])] = item
    return out


def _snippet(
    layer: str,
    content: str,
    *,
    source_id: str = "",
) -> MemorySnippet | None:
    text = sanitize_recall_text((content or "").strip())
    if not text or text == "[redacted]":
        return None
    meta = _layer_meta().get(layer, {})
    return MemorySnippet(
        layer=layer,
        tier=str(meta.get("tier") or "medium"),
        priority=int(meta.get("priority") or 0),
        content=text[:500],
        source_id=source_id,
    )


async def _collect_truth_snippets(cap: int) -> list[MemorySnippet]:
    out: list[MemorySnippet] = []
    try:
        from aria_core.truth_ledger.canonical import load_canonical_facts

        for fact in load_canonical_facts()[:cap]:
            answer = (fact.get("answer") or "").strip()
            cid = str(fact.get("id") or "")
            sn = _snippet("truth_ledger", answer, source_id=cid)
            if sn:
                out.append(sn)
    except Exception:
        pass
    return out


async def _collect_cognitive_snippets(cap: int) -> list[MemorySnippet]:
    out: list[MemorySnippet] = []
    try:
        from aria_core.knowledge.cognitive import get_approved

        for item in (await get_approved())[:cap]:
            sn = _snippet("cognitive", item.content, source_id=item.topic)
            if sn:
                out.append(sn)
    except Exception:
        pass
    return out


def _collect_journal_snippets(cap: int) -> list[MemorySnippet]:
    from aria_core.memory._legacy_journal import read_recent_memory

    out: list[MemorySnippet] = []
    for entry in read_recent_memory(limit=cap):
        clean = entry.replace("\n", " ")[:400]
        sn = _snippet("journal", clean)
        if sn:
            out.append(sn)
    return out


def _collect_reflection_snippets(cap: int) -> list[MemorySnippet]:
    from aria_core.memory.reflection import read_explicit_reflections

    out: list[MemorySnippet] = []
    for item in read_explicit_reflections(limit=cap):
        sn = _snippet("reflection", str(item.get("content") or ""), source_id=str(item.get("context") or ""))
        if sn:
            out.append(sn)
    return out


def _collect_directive_snippets(cap: int) -> list[MemorySnippet]:
    from aria_core.directives import get_directives_text

    text = get_directives_text()[:2000]
    if not text.strip():
        return []
    chunks = [c.strip() for c in text.split("\n## ") if c.strip()][:cap]
    out: list[MemorySnippet] = []
    for i, chunk in enumerate(chunks):
        sn = _snippet("directive", chunk, source_id=f"directive-{i}")
        if sn:
            out.append(sn)
    return out


def _collect_values_snippets() -> list[MemorySnippet]:
    from aria_core.memory.values import get_values_text

    text = get_values_text()
    sn = _snippet("values", text)
    return [sn] if sn else []


def _collect_goals_snippets() -> list[MemorySnippet]:
    from aria_core.memory.goals import get_goals_text

    text = get_goals_text()
    sn = _snippet("goals", text)
    return [sn] if sn else []


async def _collect_vector_snippets(query: str, cap: int) -> list[MemorySnippet]:
    from aria_core.memory.vector import is_vector_enabled, search

    if not is_vector_enabled():
        return []
    q = sanitize_recall_text(query.strip())
    if len(q) < 8:
        return []
    out: list[MemorySnippet] = []
    try:
        hits = await search(q, limit=cap)
        for hit in hits:
            meta = hit.get("metadata") or {}
            topic = meta.get("topic") or "memory"
            sn = _snippet("vector", str(hit.get("content") or ""), source_id=str(topic))
            if sn:
                out.append(sn)
    except Exception:
        pass
    return out


def _collect_conversation_snippets(messages: list[dict[str, Any]], cap: int) -> list[MemorySnippet]:
    out: list[MemorySnippet] = []
    for msg in messages[-cap:]:
        role = msg.get("role") or "user"
        content = str(msg.get("content") or "")
        sn = _snippet("conversation", f"{role}: {content}", source_id=role)
        if sn:
            out.append(sn)
    return out


async def collect_memory_snippets(
    *,
    messages: list[dict[str, Any]] | None = None,
    query_hint: str = "",
) -> list[MemorySnippet]:
    cfg = _load_config()
    cap = int(cfg.get("max_snippets_per_layer") or 6)
    snippets: list[MemorySnippet] = []
    snippets.extend(_collect_directive_snippets(cap))
    snippets.extend(await _collect_truth_snippets(cap))
    snippets.extend(await _collect_cognitive_snippets(cap))
    snippets.extend(_collect_values_snippets())
    snippets.extend(_collect_goals_snippets())
    snippets.extend(_collect_reflection_snippets(cap))
    snippets.extend(_collect_journal_snippets(cap))
    snippets.extend(await _collect_vector_snippets(query_hint, cap))
    if messages:
        snippets.extend(_collect_conversation_snippets(messages, cap))
    return snippets


def arbitrate_snippets(snippets: list[MemorySnippet]) -> ArbitrationResult:
    """Supprime les snippets basse priorité en conflit avec le noyau épistémique."""
    from aria_core.knowledge.contradiction import check_contradiction

    result = ArbitrationResult()
    if not snippets:
        return result

    protected_layers = frozenset({"directive", "truth_ledger"})
    sorted_snippets = sorted(snippets, key=lambda s: s.priority, reverse=True)

    for sn in sorted_snippets:
        if sn.layer in protected_layers:
            result.kept.append(sn)
            continue
        conflict, explanation = check_contradiction(sn.content)
        if conflict:
            result.suppressed.append(sn)
            result.conflicts.append({
                "layer": sn.layer,
                "tier": sn.tier,
                "source_id": sn.source_id,
                "reason": explanation,
                "preview": sn.content[:120],
            })
        else:
            result.kept.append(sn)

    for tier in ("short", "medium", "long"):
        result.tier_counts[tier] = sum(1 for s in result.kept if s.tier == tier)
    return result


def _log_path() -> Path:
    return memory_dir() / _LOG_FILE


def log_arbitration(result: ArbitrationResult) -> None:
    cfg = _load_config()
    if not cfg.get("log_decisions", True):
        return
    entry = {
        "at": datetime.now(timezone.utc).isoformat(),
        "kept": len(result.kept),
        "suppressed": len(result.suppressed),
        "tier_counts": result.tier_counts,
        "conflicts": result.conflicts[:8],
    }
    path = _log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def get_arbitration_text(
    result: ArbitrationResult,
    *,
    budget_chars: int | None = None,
    lang: str = "fr",
) -> str:
    cfg = _load_config()
    budget = budget_chars or int(cfg.get("budget_chars") or 1200)
    if not result.kept and not result.conflicts:
        return ""

    title = "Arbitre mémoire ARIA (Phase H)" if lang == "fr" else "ARIA memory arbitrator (Phase H)"
    lines = [f"# {title}"]
    used = len(lines[0])

    hierarchy = _layer_meta()
    if hierarchy:
        order = sorted(hierarchy.values(), key=lambda x: int(x.get("priority") or 0), reverse=True)
        labels = [f"{x.get('id')} ({x.get('tier')})" for x in order[:6]]
        line = "## Hiérarchie : " + " > ".join(labels)
        if used + len(line) + 1 <= budget:
            lines.append(line)
            used += len(line) + 1

    tiers = result.tier_counts
    if any(tiers.values()):
        tier_line = (
            f"## Tiers actifs — court:{tiers.get('short', 0)} "
            f"moyen:{tiers.get('medium', 0)} long:{tiers.get('long', 0)}"
        )
        if used + len(tier_line) + 1 <= budget:
            lines.append(tier_line)
            used += len(tier_line) + 1

    if result.conflicts:
        lines.append("\n## Conflits résolus")
        used += len(lines[-1]) + 1
        for c in result.conflicts[:5]:
            line = (
                f"- **[{c.get('layer')}/{c.get('tier')}]** supprimé — "
                f"{c.get('reason')} : «{c.get('preview', '')[:80]}»"
            )
            if used + len(line) + 1 > budget:
                break
            lines.append(line)
            used += len(line) + 1

    ruling = (cfg.get("ruling_fr") or "").strip()
    if ruling and not result.conflicts and used + len(ruling) + 2 <= budget:
        lines.append(f"\n_{ruling[:200]}_")

    return "\n".join(lines)


async def run_memory_arbitration(
    *,
    messages: list[dict[str, Any]] | None = None,
    query_hint: str = "",
    lang: str = "fr",
) -> ArbitrationResult:
    if not is_arbitrator_enabled():
        return ArbitrationResult()
    snippets = await collect_memory_snippets(messages=messages, query_hint=query_hint)
    result = arbitrate_snippets(snippets)
    log_arbitration(result)
    return result


def suppressed_journal_preview(result: ArbitrationResult) -> set[str]:
    """Extraits journal supprimés — pour annotation dans le contexte."""
    return {s.content[:80] for s in result.suppressed if s.layer == "journal"}


def clear_arbitrator_cache() -> None:
    _load_config.cache_clear()