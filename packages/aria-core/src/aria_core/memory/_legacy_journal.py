"""Episodic journal implementation — formerly ``aria_core/memory.py`` (Phase B)."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import yaml

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


_DNA_PATH = _PKG_ROOT / "knowledge" / "dna.yaml"
_PERSONA_BUDGET = 2600


def get_persona_text(*, budget_chars: int = _PERSONA_BUDGET) -> str:
    """ARIA identity + personality for the operator LLM context (private —
    never used on the public side, see narrative.public_llm_system_block which
    has its own independent mechanism). Composed from knowledge/dna.yaml
    (root + personality) since 07/21 -- replaces the old static persona.md,
    merged into the same DNA as values/goals/reflection."""
    if not _DNA_PATH.is_file():
        from aria_core.narrative import memory_identity_fallback

        return memory_identity_fallback()
    try:
        raw = yaml.safe_load(_DNA_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        raw = {}
    dna = raw.get("dna") if isinstance(raw, dict) else None
    if not isinstance(dna, dict):
        from aria_core.narrative import memory_identity_fallback

        return memory_identity_fallback()

    racine = dna.get("racine") or {}
    personnalite = dna.get("personnalite") or {}

    lines: list[str] = ["# ARIA — identité & personnalité"]
    used = len(lines[0])

    def _add(line: str) -> bool:
        nonlocal used
        if used + len(line) + 1 > budget_chars:
            return False
        lines.append(line)
        used += len(line) + 1
        return True

    nom = racine.get("nom") or "ARIA"
    titre = racine.get("titre") or ""
    holding = racine.get("holding") or ""
    _add(f"{nom} — {titre}, opère {holding}.".strip())
    nature = " ".join(str(racine.get("nature") or "").split())
    if nature:
        _add(nature)

    archetype = racine.get("archetype") or {}
    if archetype.get("nom"):
        _add(f"## Archétype : {archetype['nom']}")
        for pilier in archetype.get("piliers") or []:
            if isinstance(pilier, dict) and pilier.get("text"):
                if not _add(f"- {pilier['text']}"):
                    break

    if personnalite.get("jugement_de_machine"):
        _add("## Personnalité")
        _add(" ".join(str(personnalite["jugement_de_machine"]).split()))
        for trait in personnalite.get("caractere_humain_garde") or []:
            if not _add(f"- {trait}"):
                break
        for trait in personnalite.get("avantages_ia_en_trait_de_caractere") or []:
            if not _add(f"- {trait}"):
                break

    voix = personnalite.get("voix") or []
    if voix:
        _add("## Voix")
        for item in voix:
            if isinstance(item, dict) and item.get("comportement"):
                if not _add(f"- **{item.get('trait', '')}** : {item['comportement']}"):
                    break

    mission = racine.get("mission") or []
    if mission:
        _add("## Mission")
        for point in mission:
            if not _add(f"- {point}"):
                break

    return "\n".join(lines)


def get_doctrine_text() -> str:
    doctrine_path = _PKG_ROOT / "doctrine" / "engineering.md"
    if doctrine_path.exists():
        return doctrine_path.read_text(encoding="utf-8")[:2500]
    return ""


def get_launchpad_doctrine_text() -> str:
    from aria_core.knowledge.base_launchpads import registry_markdown

    return registry_markdown()[:3000]


# Backward compat — Phase D implementation in llm_context.py
from aria_core.memory.llm_context import build_llm_context  # noqa: F401