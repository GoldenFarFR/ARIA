"""Juge QI shadow — ARIA s'auto-évalue en test (sans effet) puis promotion auto."""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from aria_core.capability_levels import CATEGORY_ORDER, HANDCRAFTED_MAX
from aria_core.memory import append_memory
from aria_core.qi_auto_judge import (
    JUDGE_HEARTBEAT,
    JudgeEvidence,
    apply_earned_levels,
    collect_judge_evidence,
    earned_level,
)
from aria_core.qi_judge_calibration import (
    format_calibration_summary,
    is_aria_judge_promoted,
    record_shadow_run,
)

logger = logging.getLogger(__name__)

JUDGE_SHADOW = "auto_judge_shadow"
JUDGE_ARIA = "auto_judge_aria"


def shadow_enabled() -> bool:
    from aria_core.llm import is_llm_configured
    from aria_core.runtime import settings

    if not is_llm_configured():
        return False
    return bool(getattr(settings, "aria_qi_shadow_judge_enabled", True))


def evidence_to_dict(ev: JudgeEvidence) -> dict[str, Any]:
    return {
        "gem_crush_version": ev.gem_crush_version,
        "gem_crush_title": ev.gem_crush_title,
        "resolved_gaps_7d": ev.resolved_gaps_7d,
        "health_ok": ev.health_ok,
        "health_commit": ev.health_commit,
        "memory_entries": ev.memory_entries,
        "telegram_configured": ev.telegram_configured,
        "github_write": ev.github_write,
    }


def official_levels(ev: JudgeEvidence) -> dict[str, tuple[int, str]]:
    return {cat: earned_level(cat, ev) for cat in CATEGORY_ORDER}


def format_evidence_prompt(ev: JudgeEvidence) -> str:
    d = evidence_to_dict(ev)
    lines = [
        "Métriques actuelles (preuves objectives) :",
        f"- Gem Crush prod : v{d['gem_crush_version']} ({ev.gem_crush_title or 'sans titre'})",
        f"- Health : {'OK' if d['health_ok'] else 'KO'} commit={d['health_commit'][:12] or 'n/a'}",
        f"- Gaps capability résolus (7j) : {d['resolved_gaps_7d']}",
        f"- Entrées mémoire ARIA : {d['memory_entries']}",
        f"- Telegram configuré : {d['telegram_configured']}",
        f"- Écriture GitHub prod : {d['github_write']}",
        "",
        "Axes à juger (niveau mérité 0–12, 0 si rien ne le justifie) :",
        "codage, social, intelligence, fiabilite, autonomie, business",
        "",
        "Règles :",
        "- business : toujours 0 (revenu Stripe uniquement, pas de supposition)",
        "- base-toi uniquement sur les métriques ci-dessus",
        "- réponds en JSON strict, sans markdown",
    ]
    return "\n".join(lines)


def _clamp_level(level: int) -> int:
    return min(HANDCRAFTED_MAX, max(0, int(level)))


def parse_shadow_json(raw: str) -> dict[str, tuple[int, str]]:
    text = raw.strip()
    block = re.search(r"\{[\s\S]*\}", text)
    if block:
        text = block.group(0)
    data = json.loads(text)
    out: dict[str, tuple[int, str]] = {}
    for cat in CATEGORY_ORDER:
        entry = data.get(cat)
        if isinstance(entry, dict):
            lvl = _clamp_level(entry.get("level", 0))
            reason = str(entry.get("reason", ""))[:300]
        elif isinstance(entry, int):
            lvl = _clamp_level(entry)
            reason = ""
        else:
            lvl, reason = 0, ""
        if cat == "business":
            lvl = 0
            reason = reason or "business — métriques revenu uniquement (Stripe)"
        out[cat] = (lvl, reason)
    return out


async def request_shadow_verdict(ev: JudgeEvidence) -> dict[str, tuple[int, str]] | None:
    from aria_core.grounding import grounded_llm_identity
    from aria_core.llm import chat_with_context
    from aria_core.narrative import llm_system_block

    system = (
        f"{llm_system_block('fr')}\n"
        f"{grounded_llm_identity('fr')}\n"
        "Tu es le juge QI d'ARIA en phase shadow. "
        "Évalue chaque axe avec prudence épistémique — pas d'inflation."
    )
    user = (
        f"{format_evidence_prompt(ev)}\n\n"
        'Format JSON : {"codage":{"level":N,"reason":"..."}, ...}'
    )
    try:
        raw = await chat_with_context(
            user,
            system,
            temperature=0.1,
            max_tokens=900,
        )
        if not raw:
            return None
        return parse_shadow_json(raw)
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        logger.warning("shadow judge parse failed: %s", exc)
        return None
    except Exception as exc:
        logger.warning("shadow judge llm failed: %s", exc)
        return None


def apply_shadow_levels(
    shadow: dict[str, tuple[int, str]],
    *,
    source: str = JUDGE_ARIA,
    max_steps_per_category: int = 20,
) -> list[dict[str, Any]]:
    """Applique les niveaux shadow (juge ARIA officiel) avec garde-fous."""
    events: list[dict[str, Any]] = []
    from aria_core.capability_levels import complete_level, load_progress

    for category in CATEGORY_ORDER:
        target, reason = shadow.get(category, (0, ""))
        target = _clamp_level(target)
        if category == "business":
            continue
        if target < 1:
            continue
        progress = load_progress()
        current = int(progress["categories"][category]["level"])
        steps = 0
        while current < target and steps < max_steps_per_category:
            result = complete_level(
                category,
                note=f"juge ARIA: {reason}"[:300],
                source=source,
            )
            if not result.get("ok"):
                break
            current = int(result["new_level"])
            events.append({**result, "reason": reason, "judge_source": source})
            steps += 1
    return events


async def run_shadow_calibration(
    ev: JudgeEvidence,
    *,
    official_source: str = JUDGE_HEARTBEAT,
) -> dict[str, Any]:
    """Phase test — verdict LLM parallèle, zéro effet sur le QI affiché."""
    if not shadow_enabled():
        return {"skipped": True, "reason": "shadow_disabled"}

    official = official_levels(ev)
    shadow = await request_shadow_verdict(ev)
    if not shadow:
        return {"skipped": True, "reason": "no_shadow_verdict"}

    run = record_shadow_run(
        official,
        shadow,
        official_source=official_source,
        evidence=evidence_to_dict(ev),
    )
    append_memory(
        "capability",
        f"[qi-shadow] accord {run['agreement_rate']:.0%} "
        f"({run['agreements']}/{run['compared']}) | {format_calibration_summary('fr')[:120]}",
    )
    return {
        "skipped": False,
        "shadow": shadow,
        "official": official,
        "calibration": run,
        "promoted": is_aria_judge_promoted(),
    }


async def run_qi_judge_with_shadow(
    *,
    source: str = JUDGE_HEARTBEAT,
    lang: str = "fr",
) -> dict[str, Any]:
    """Juge officiel + shadow calibration ou promotion ARIA."""
    from aria_core.capability_levels import global_index
    from aria_core.qi_auto_judge import format_judge_report

    ev = await collect_judge_evidence()
    promoted = is_aria_judge_promoted() and shadow_enabled()
    shadow_result: dict[str, Any] = {}

    if promoted:
        shadow = await request_shadow_verdict(ev)
        if shadow:
            events = apply_shadow_levels(shadow, source=JUDGE_ARIA)
            judge_mode = JUDGE_ARIA
        else:
            events = apply_earned_levels(ev, source=source)
            judge_mode = source
    else:
        events = apply_earned_levels(ev, source=source)
        judge_mode = source
        shadow_result = await run_shadow_calibration(ev, official_source=source)

    idx = global_index()
    if events:
        summary = ", ".join(
            f"{e.get('category')}→{e.get('new_level')}" for e in events[:8]
        )
        append_memory("capability", f"[qi-judge/{judge_mode}] {summary} | indice {idx}")

    message = ""
    if lang == "fr":
        mode = "ARIA officielle" if promoted else "ouvrier/heartbeat + shadow test"
        lines = [
            f"⚖️ Juge QI ({mode})",
            f"Indice global : {idx} / 1000",
            f"Gem Crush prod : v{ev.gem_crush_version}",
            "",
        ]
        for e in events:
            lines.append(
                f"• {e.get('category')} → niveau {e.get('new_level')} "
                f"({e.get('reason', '')[:80]})"
            )
        if shadow_result and not shadow_result.get("skipped"):
            cal = shadow_result.get("calibration") or {}
            lines.append(
                f"\nShadow test : accord {cal.get('agreement_rate', 0):.0%} "
                f"— {format_calibration_summary('fr')}"
            )
        elif promoted:
            lines.append("\nJuge ARIA promu (calibration ≥90 % / 30j).")
        message = "\n".join(lines)

    return {
        "events": events,
        "evidence": ev,
        "global_index": idx,
        "message": message,
        "shadow": shadow_result,
        "promoted": promoted,
        "judge_mode": judge_mode,
        "notified": False,
    }


def format_shadow_report(result: dict[str, Any], *, lang: str = "fr") -> str:
    from aria_core.qi_auto_judge import format_judge_report

    base = format_judge_report(result, lang=lang) if "evidence" in result else ""
    shadow = result.get("shadow") or {}
    if shadow.get("skipped"):
        return base
    cal = shadow.get("calibration") or {}
    extra = format_calibration_summary(lang)
    if lang == "fr":
        return f"{base}\n\nShadow : accord {cal.get('agreement_rate', 0):.0%}\n{extra}"
    return f"{base}\n\nShadow agreement {cal.get('agreement_rate', 0):.0%}\n{extra}"