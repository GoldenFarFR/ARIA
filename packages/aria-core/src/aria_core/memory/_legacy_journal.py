"""Implémentation journal épisodique — ex-``aria_core/memory.py`` (Phase B)."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from aria_core.paths import memory_dir

_PKG_ROOT = Path(__file__).resolve().parent.parent
MEMORY_DIR = memory_dir()


def _ensure_dir() -> None:
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)


def append_memory(category: str, content: str) -> str:
    _ensure_dir()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    filepath = MEMORY_DIR / f"{category}_{today}.md"
    timestamp = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
    entry = f"\n## [{timestamp}]\n{content}\n"

    if filepath.exists():
        with open(filepath, "a", encoding="utf-8") as f:
            f.write(entry)
    else:
        header = f"# ARIA memory — {category} — {today}\n"
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(header + entry)

    return str(filepath)


def read_recent_memory(category: str | None = None, limit: int = 10) -> list[str]:
    _ensure_dir()
    pattern = f"{category}_*.md" if category else "*.md"
    files = sorted(MEMORY_DIR.glob(pattern), reverse=True)[:5]
    entries: list[str] = []

    for filepath in files:
        with open(filepath, encoding="utf-8") as f:
            content = f.read()
        sections = [s.strip() for s in content.split("## [") if s.strip()]
        entries.extend(sections[-limit:])

    return entries[-limit:]


def count_memory_entries() -> int:
    _ensure_dir()
    count = 0
    for filepath in MEMORY_DIR.glob("*.md"):
        with open(filepath, encoding="utf-8") as f:
            count += f.read().count("## [")
    return count


def get_journal_summary() -> str:
    entries = read_recent_memory(limit=5)
    if not entries:
        return "Aucune mémoire enregistrée pour l'instant."
    return "\n---\n".join(entries)


def get_persona_text() -> str:
    persona_path = _PKG_ROOT / "persona.md"
    if persona_path.exists():
        return persona_path.read_text(encoding="utf-8")[:3000]
    from aria_core.narrative import memory_identity_fallback

    return memory_identity_fallback()


def get_doctrine_text() -> str:
    doctrine_path = _PKG_ROOT / "doctrine" / "engineering.md"
    if doctrine_path.exists():
        return doctrine_path.read_text(encoding="utf-8")[:2500]
    return ""


def get_launchpad_doctrine_text() -> str:
    from aria_core.knowledge.base_launchpads import registry_markdown

    return registry_markdown()[:3000]


# Rétrocompat — implémentation Phase D dans llm_context.py
from aria_core.memory.llm_context import build_llm_context  # noqa: F401