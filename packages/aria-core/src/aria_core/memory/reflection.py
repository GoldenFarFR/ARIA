"""Réflexion opérationnelle ARIA — Phase G (journal + synthèse → injection LLM)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from aria_core.memory.llm_context import sanitize_recall_text
from aria_core.paths import memory_dir

_REFLECTION_PATH = Path(__file__).resolve().parents[1] / "knowledge" / "aria_reflection.yaml"
_LOG_FILE = "reflections.jsonl"
_REFLECTION_BUDGET = 1400


@lru_cache(maxsize=1)
def _load_config() -> dict[str, Any]:
    if not _REFLECTION_PATH.is_file():
        return {}
    try:
        raw = yaml.safe_load(_REFLECTION_PATH.read_text(encoding="utf-8")) or {}
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _log_path() -> Path:
    return memory_dir() / _LOG_FILE


def append_reflection(
    content: str,
    *,
    context: str = "session",
    outcome: str = "note",
) -> dict[str, str]:
    """Enregistre une réflexion explicite (opérateur ou heartbeat)."""
    text = sanitize_recall_text((content or "").strip())
    if not text:
        return {}
    entry = {
        "at": datetime.now(timezone.utc).isoformat(),
        "context": (context or "session")[:80],
        "outcome": (outcome or "note")[:40],
        "content": text[:600],
    }
    path = _log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def read_explicit_reflections(*, limit: int | None = None) -> list[dict[str, Any]]:
    cfg = _load_config()
    cap = limit or int(cfg.get("max_explicit", 8))
    path = _log_path()
    if not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    out: list[dict[str, Any]] = []
    for line in reversed(lines):
        if len(out) >= cap:
            break
        try:
            item = json.loads(line)
            if isinstance(item, dict) and item.get("content"):
                out.append(item)
        except Exception:
            continue
    return list(reversed(out))


def _synthesize_from_journal() -> list[str]:
    from aria_core.memory._legacy_journal import read_recent_memory

    cfg = _load_config()
    categories = cfg.get("journal_categories") or []
    cap = int(cfg.get("max_synthesized", 6))
    lines: list[str] = []
    for cat in categories:
        if len(lines) >= cap:
            break
        if not isinstance(cat, str):
            continue
        for entry in read_recent_memory(cat, limit=1):
            clean = sanitize_recall_text(entry.replace("\n", " ")[:220])
            if clean and clean != "[redacted]":
                lines.append(f"[{cat}] {clean}")
    return lines[:cap]


def get_reflections_text(*, budget_chars: int = _REFLECTION_BUDGET, lang: str = "fr") -> str:
    """Bloc markdown réflexions pour le contexte LLM (opérateur)."""
    explicit = read_explicit_reflections()
    synthesized = _synthesize_from_journal()
    if not explicit and not synthesized:
        return ""

    title = "Réflexion opérationnelle ARIA (Phase G)" if lang == "fr" else "ARIA reflection (Phase G)"
    lines = [f"# {title}"]
    used = len(lines[0])

    if explicit:
        lines.append("\n## Réflexions enregistrées")
        used += len(lines[-1]) + 1
        for item in explicit:
            ctx = item.get("context") or "session"
            text = sanitize_recall_text(str(item.get("content") or ""))
            line = f"- **[{ctx}]** {text}"
            if used + len(line) + 1 > budget_chars:
                break
            lines.append(line)
            used += len(line) + 1

    if synthesized and used < budget_chars:
        lines.append("\n## Synthèse récente")
        used += len(lines[-1]) + 1
        for sline in synthesized:
            if used + len(sline) + 1 > budget_chars:
                break
            lines.append(f"- {sline}")
            used += len(sline) + 1

    cfg = _load_config()
    prompts = cfg.get("prompts") or []
    if prompts and used < budget_chars:
        p = prompts[0] if isinstance(prompts[0], dict) else {}
        hint = (p.get("text") or "").strip()
        if hint:
            line = f"\n_Piste : {hint[:200]}_"
            if used + len(line) <= budget_chars:
                lines.append(line)

    return "\n".join(lines)


def reflections_count() -> int:
    return len(read_explicit_reflections())


def clear_reflection_cache() -> None:
    _load_config.cache_clear()