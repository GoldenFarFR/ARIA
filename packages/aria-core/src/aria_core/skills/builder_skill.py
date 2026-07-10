"""Builder / optimizer skill — strategic engineering plans for the holding."""

from __future__ import annotations

from pathlib import Path

from aria_core.memory import append_memory

_DOCTRINE = Path(__file__).parent.parent / "doctrine" / "engineering.md"


def _doctrine_excerpt(max_chars: int = 1200) -> str:
    if not _DOCTRINE.exists():
        return ""
    return _DOCTRINE.read_text(encoding="utf-8")[:max_chars]


async def execute_build_optimize(user_message: str, lang: str = "en") -> tuple[str, dict]:
    """Produce a structured build/optimize plan — ARIA's builder queen mode."""
    from aria_core.knowledge.cognitive import get_approved

    doctrine = _doctrine_excerpt()
    knowledge_lines: list[str] = []
    try:
        items = await get_approved(limit=8)
        for item in items:
            if item.topic in ("engineering", "optimization", "creativity", "build", "pattern"):
                knowledge_lines.append(f"- {item.content[:160]}")
    except Exception:
        pass

    lower = user_message.lower()
    focus = "general"
    if any(w in lower for w in ("optim", "perf", "rapide", "fast", "cache", "lint")):
        focus = "optimization"
    elif any(w in lower for w in ("creat", "design", "ux", "ui", "brand", "copy")):
        focus = "creativity"
    elif any(w in lower for w in ("repo", "github", "deploy", "render", "dns")):
        focus = "infra"
    elif any(w in lower for w in ("code", "bug", "fix", "feature", "impl")):
        focus = "code"

    if lang == "fr":
        header = (
            "Mode Builder Queen — optimisation + créativité.\n"
            f"Focus détecté : **{focus}**.\n"
        )
        loop = (
            "**Boucle** : Observer → Hypothèse → Plan (≤5 étapes) → Build → Vérifier → Apprendre\n\n"
            "**Plan proposé**\n"
            "1. Clarifier l'objectif et le périmètre (quoi / pourquoi / pour qui)\n"
            "2. Auditer l'existant (fichiers, deps, dette) — mesurer avant de changer\n"
            "3. Choisir le plus petit diff gagnant (une PR, un déploiement)\n"
            "4. Appliquer avec style du repo — pas de refactor hors sujet\n"
            "5. Vérifier (build, test, smoke) + noter le pattern en mémoire\n\n"
            "**Créativité** : une idée distinctive par itération (UX, narrative, architecture).\n"
            "**Optimisation** : supprimer avant d'ajouter ; batch les appels coûteux.\n\n"
            "Pour mémoriser un pattern : `/learn engineering | <leçon>`\n"
        )
    else:
        header = (
            "Builder Queen mode — optimization + creativity.\n"
            f"Detected focus: **{focus}**.\n"
        )
        loop = (
            "**Loop**: Observe → Hypothesize → Plan (≤5 steps) → Build → Verify → Learn\n\n"
            "**Proposed plan**\n"
            "1. Clarify goal and scope (what / why / for whom)\n"
            "2. Audit what exists (files, deps, debt) — measure before changing\n"
            "3. Pick the smallest winning diff (one PR, one deploy)\n"
            "4. Implement matching repo style — no drive-by refactors\n"
            "5. Verify (build, test, smoke) + log the pattern to memory\n\n"
            "**Creativity**: one distinctive move per iteration (UX, narrative, architecture).\n"
            "**Optimization**: delete before adding; batch expensive calls.\n\n"
            "Store a pattern: `/learn engineering | <lesson>`\n"
        )

    parts = [header, loop]
    if doctrine:
        parts.append(f"\n**Doctrine excerpt**\n{doctrine[:800]}...")
    if knowledge_lines:
        parts.append("\n**Approved build knowledge**\n" + "\n".join(knowledge_lines))

    parts.append(f"\n**Your request**\n{user_message[:500]}")
    output = "\n".join(parts)
    append_memory("builder", f"[{focus}] {user_message[:120]}")
    return output, {"focus": focus, "mode": "builder_queen"}