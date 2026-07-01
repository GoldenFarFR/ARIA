"""Promotion QI — propose palier suivant quand criteres self-improve remplis."""

from __future__ import annotations

import logging
from typing import Any

from aria_core.capability_gap import count_resolved_gaps
from aria_core.capability_levels import CATEGORY_ORDER, check_auto_completions, full_status
from aria_core.memory import append_memory
from aria_core.qi_auto_judge import JUDGE_HEARTBEAT, run_qi_auto_judge

logger = logging.getLogger(__name__)

RESOLVED_GAPS_FOR_AUTONOMIE = 2


async def run_qi_promotion_check(*, lang: str = "fr") -> dict[str, Any]:
    """
    Verifie auto-completions metriques + gaps resolus.
    Notifie Telegram si niveau suivant suggere.
    """
    auto_events = check_auto_completions()
    judge = await run_qi_auto_judge(source=JUDGE_HEARTBEAT, lang=lang)
    judge_events = judge.get("events") or []
    auto_events = list(auto_events) + [
        {k: e.get(k) for k in ("category", "new_level", "global_index")}
        for e in judge_events
    ]
    resolved = count_resolved_gaps(days=7)
    status = full_status(lang)
    lines: list[str] = []

    if judge_events:
        if lang == "fr":
            lines.append(
                f"Juge auto : {len(judge_events)} palier(s) — indice {judge.get('global_index')}"
            )
        else:
            lines.append(f"Auto-judge: {len(judge_events)} level(s)")

    if auto_events:
        for ev in auto_events:
            if lang == "fr":
                lines.append(
                    f"Auto-level {ev.get('category')} -> {ev.get('new_level')} "
                    f"(indice {ev.get('global_index')})"
                )
            else:
                lines.append(
                    f"Auto-level {ev.get('category')} -> {ev.get('new_level')}"
                )

    ready_cats = [
        cat for cat in CATEGORY_ORDER
        if status["categories"][cat].get("auto_ready") and status["categories"][cat].get("next_level")
    ]
    if ready_cats:
        if lang == "fr":
            lines.append(
                "Metriques atteintes — valide : "
                + ", ".join(f"/level up {c}" for c in ready_cats)
            )
        else:
            lines.append(
                "Metrics met — validate: "
                + ", ".join(f"/level up {c}" for c in ready_cats)
            )

    autonomie = status["categories"].get("autonomie", {})
    if (
        resolved >= RESOLVED_GAPS_FOR_AUTONOMIE
        and autonomie.get("next_level")
        and int(autonomie.get("completed_level", 0)) < 8
    ):
        if lang == "fr":
            lines.append(
                f"Self-improve : {resolved} gap(s) resolu(s) cette semaine — "
                f"autonomie prete pour niveau {autonomie['next_level']} "
                f"(commande /level up autonomie)"
            )
        else:
            lines.append(
                f"Self-improve: {resolved} gap(s) resolved — "
                f"consider /level up autonomie"
            )

    if judge.get("message") and judge_events:
        lines.append(judge["message"])

    if not lines:
        return {
            "notified": False,
            "resolved_gaps": resolved,
            "auto_events": len(auto_events),
            "judge_events": len(judge_events),
        }

    if lang == "fr":
        header = "ARIA — promotion QI (self-improve)\n\n"
    else:
        header = "ARIA — QI promotion (self-improve)\n\n"
    msg = header + "\n".join(lines)
    append_memory("capability", f"[qi-promote] {msg[:300]}")

    notified = False
    try:
        from aria_core.gateway.telegram_bot import notify_admin

        notified = await notify_admin(msg.strip())
    except Exception as exc:
        logger.warning("qi_promote notify failed: %s", exc)

    return {
        "notified": notified,
        "resolved_gaps": resolved,
        "auto_events": auto_events,
        "judge_events": judge_events,
        "ready_categories": ready_cats,
        "message": msg,
    }