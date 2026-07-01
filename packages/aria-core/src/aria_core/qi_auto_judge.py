"""Juge QI ARIA — métriques réelles jusqu'à auto-évaluation.

Phase actuelle : règles déterministes (ouvrier Cursor / heartbeat).
Phase shadow : LLM self-judge en test (qi_self_judge_shadow) ; promotion auto si accord ≥90 % / 30j.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from aria_core.capability_gap import count_resolved_gaps
from aria_core.capability_levels import (
    CATEGORY_ORDER,
    MAX_LEVEL,
    complete_level,
    global_index,
    load_progress,
)
from aria_core.memory import append_memory, count_memory_entries

logger = logging.getLogger(__name__)

JUDGE_OUVRIER = "auto_judge_ouvrier"
JUDGE_HEARTBEAT = "auto_judge_heartbeat"


@dataclass(frozen=True)
class JudgeEvidence:
    gem_crush_version: int = 0
    gem_crush_title: str = ""
    resolved_gaps_7d: int = 0
    health_ok: bool = False
    health_commit: str = ""
    memory_entries: int = 0
    telegram_configured: bool = False
    github_write: bool = False
    aria_core_build: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


def _tier(value: int, table: tuple[tuple[int, int], ...]) -> tuple[int, str]:
    for threshold, level in table:
        if value >= threshold:
            return level, f"seuil {threshold} atteint (valeur={value})"
    return 0, f"valeur {value} insuffisante"


def judge_codage(ev: JudgeEvidence) -> tuple[int, str]:
    table = (
        (60, 12),
        (55, 11),
        (50, 10),
        (40, 9),
        (30, 8),
        (25, 7),
        (20, 6),
        (15, 5),
        (10, 4),
        (5, 3),
        (3, 2),
        (1, 1),
    )
    lvl, detail = _tier(ev.gem_crush_version, table)
    if lvl:
        return lvl, f"Codage — Gem Crush v{ev.gem_crush_version} prod ({detail})"
    return 0, "Codage — pas de release Gem Crush détectée"


def judge_autonomie(ev: JudgeEvidence) -> tuple[int, str]:
    lvl = 0
    parts: list[str] = []
    if ev.resolved_gaps_7d >= 1:
        lvl = max(lvl, 1)
        parts.append(f"{ev.resolved_gaps_7d} gap(s) capability résolu(s) (7j)")
    if ev.gem_crush_version >= 15:
        lvl = max(lvl, 3)
        parts.append(f"heartbeat Gem Crush autonome v{ev.gem_crush_version}")
    if ev.gem_crush_version >= 43:
        lvl = max(lvl, 5)
        parts.append("synthèse backlog + releases illimitées")
    if ev.gem_crush_version >= 55 and ev.github_write:
        lvl = max(lvl, 7)
        parts.append("écriture GitHub prod sans ouvrier")
    if ev.gem_crush_version >= 65:
        lvl = max(lvl, 9)
        parts.append("65+ cycles ship autonome")
    if not parts:
        return 0, "Autonomie — initiatives autonomes insuffisantes"
    return lvl, "Autonomie — " + "; ".join(parts)


def judge_fiabilite(ev: JudgeEvidence) -> tuple[int, str]:
    lvl = 0
    parts: list[str] = []
    if ev.health_ok:
        lvl = max(lvl, 2)
        parts.append(f"health OK ({ev.health_commit[:12] or 'live'})")
    if ev.gem_crush_version >= 20:
        lvl = max(lvl, 4)
        parts.append("pipeline Gem Crush stable")
    if ev.gem_crush_version >= 42:
        lvl = max(lvl, 6)
        parts.append("cooldown + lots ≥10 items")
    if not parts:
        return 0, "Fiabilité — health ou historique insuffisant"
    return lvl, "Fiabilité — " + "; ".join(parts)


def judge_intelligence(ev: JudgeEvidence) -> tuple[int, str]:
    lvl = 0
    parts: list[str] = []
    if ev.gem_crush_version >= 8:
        lvl = max(lvl, 2)
        parts.append("roadmap Gem Crush + critic")
    if ev.gem_crush_version >= 30:
        lvl = max(lvl, 4)
        parts.append("priorisation premium / recherche concurrence")
    if ev.gem_crush_version >= 50:
        lvl = max(lvl, 6)
        parts.append("exploration ouverte + backlog SSOT")
    if ev.resolved_gaps_7d >= 2:
        lvl = max(lvl, min(3, 2 + ev.resolved_gaps_7d // 2))
        parts.append("arbitrage self-improve gaps")
    if not parts:
        return 0, "Intelligence — signaux stratégiques faibles"
    return lvl, "Intelligence — " + "; ".join(parts)


def judge_social(ev: JudgeEvidence) -> tuple[int, str]:
    lvl = 0
    parts: list[str] = []
    if ev.telegram_configured:
        lvl = max(lvl, 1)
        parts.append("Telegram opérationnel")
    mem = ev.memory_entries
    if mem >= 30:
        lvl = max(lvl, 2)
        parts.append(f"{mem} entrées mémoire ARIA")
    if mem >= 100:
        lvl = max(lvl, 4)
        parts.append("journal mémoire riche")
    if ev.gem_crush_version >= 25:
        lvl = max(lvl, 3)
        parts.append("notifications ship Gem Crush (building in public)")
    if not parts:
        return 0, "Social — présence publique limitée"
    return lvl, "Social — " + "; ".join(parts)


def judge_business(ev: JudgeEvidence) -> tuple[int, str]:
    # business reste sur métriques revenu (check_auto_completions) — juge ne force pas
    return 0, "Business — métriques revenu uniquement (Stripe)"


JUDGES = {
    "codage": judge_codage,
    "autonomie": judge_autonomie,
    "fiabilite": judge_fiabilite,
    "intelligence": judge_intelligence,
    "social": judge_social,
    "business": judge_business,
}


def earned_level(category: str, ev: JudgeEvidence) -> tuple[int, str]:
    fn = JUDGES.get(category)
    if not fn:
        return 0, "catégorie inconnue"
    lvl, reason = fn(ev)
    return min(MAX_LEVEL, max(0, lvl)), reason


async def collect_judge_evidence() -> JudgeEvidence:
    from aria_core.runtime import settings
    from aria_core.skills.gem_crush_status_skill import _fetch_github_status
    from aria_core.skills.github_skill import github_configured, repo_write_allowed

    ev = JudgeEvidence(
        resolved_gaps_7d=count_resolved_gaps(days=7),
        memory_entries=count_memory_entries(),
        telegram_configured=bool(getattr(settings, "telegram_bot_token", "") or ""),
    )

    owner = getattr(settings, "github_owner", "") or ""
    if github_configured() and owner:
        ev.github_write = repo_write_allowed(owner.strip(), "aria-vanguard")

    try:
        gc = await _fetch_github_status()
        if gc.get("ok"):
            ev.gem_crush_version = int(gc.get("version") or 0)
            ev.gem_crush_title = str(gc.get("title") or "")
    except Exception as exc:
        logger.debug("judge gem crush fetch: %s", exc)

    try:
        from aria_core.health_watch import _probe_health

        ok, detail = await _probe_health()
        ev.health_ok = ok
        if "commit=" in detail:
            ev.health_commit = detail.split("commit=", 1)[-1].strip()
    except Exception as exc:
        logger.debug("judge health: %s", exc)

    return ev


def apply_earned_levels(
    ev: JudgeEvidence,
    *,
    source: str = JUDGE_HEARTBEAT,
    max_steps_per_category: int = 20,
) -> list[dict[str, Any]]:
    """Monte chaque axe jusqu'au niveau mérité (preuve dans history.source)."""
    events: list[dict[str, Any]] = []
    for category in CATEGORY_ORDER:
        target, reason = earned_level(category, ev)
        if target < 1:
            continue
        progress = load_progress()
        current = int(progress["categories"][category]["level"])
        steps = 0
        while current < target and steps < max_steps_per_category:
            result = complete_level(
                category,
                note=f"juge: {reason}"[:300],
                source=source,
            )
            if not result.get("ok"):
                break
            current = int(result["new_level"])
            events.append({**result, "reason": reason, "judge_source": source})
            steps += 1
    return events


async def run_qi_auto_judge(
    *,
    source: str = JUDGE_HEARTBEAT,
    lang: str = "fr",
) -> dict[str, Any]:
    try:
        from aria_core.qi_self_judge_shadow import run_qi_judge_with_shadow

        return await run_qi_judge_with_shadow(source=source, lang=lang)
    except Exception as exc:
        logger.warning("qi shadow judge fallback: %s", exc)

    ev = await collect_judge_evidence()
    events = apply_earned_levels(ev, source=source)
    idx = global_index()

    if events:
        summary = ", ".join(
            f"{e.get('category')}→{e.get('new_level')}" for e in events[:8]
        )
        append_memory("capability", f"[qi-judge/{source}] {summary} | indice {idx}")

    message = ""
    if events and lang == "fr":
        lines = [
            "⚖️ Juge QI (métriques réelles — phase ouvrier/heartbeat)",
            f"Indice global : {idx} / 1000",
            f"Gem Crush prod : v{ev.gem_crush_version}",
            "",
        ]
        for e in events:
            lines.append(
                f"• {e.get('category')} → niveau {e.get('new_level')} "
                f"({e.get('reason', '')[:80]})"
            )
        lines.append("\nPhase suivante : auto-évaluation ARIA quand calibration shadow OK.")
        message = "\n".join(lines)
    elif events:
        message = f"QI judge: index {idx}, {len(events)} level-up(s)"

    return {
        "events": events,
        "evidence": ev,
        "global_index": idx,
        "message": message,
        "notified": False,
    }


def format_judge_report(result: dict[str, Any], *, lang: str = "fr") -> str:
    ev: JudgeEvidence = result.get("evidence") or JudgeEvidence()
    if lang == "fr":
        lines = [
            "⚖️ Rapport juge QI (ouvrier / heartbeat)",
            f"Indice : {result.get('global_index', 0)} / 1000",
            f"Gem Crush : v{ev.gem_crush_version} | gaps 7j : {ev.resolved_gaps_7d}",
            f"Health : {'OK' if ev.health_ok else 'KO'}",
            "",
            "Niveaux mérités (sans appliquer) :",
        ]
        for cat in CATEGORY_ORDER:
            lvl, reason = earned_level(cat, ev)
            cur = load_progress()["categories"][cat]["level"]
            lines.append(f"• {cat} : actuel {cur} → mérité {lvl} — {reason[:70]}")
        return "\n".join(lines)
    return str(result.get("global_index"))