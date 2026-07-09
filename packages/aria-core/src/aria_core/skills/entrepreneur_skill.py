"""Entrepreneur cultivation — define goals, track revenue; holding-first, no peer names."""

from __future__ import annotations

import re

from aria_core.holding import holding_name
from aria_core.knowledge.zhc_peer_agents import cultivation_phases, revenue_hypotheses
from aria_core.locale import LANG_FR
from aria_core.memory import append_memory
from aria_core.revenue_goals import (
    goal_progress,
    progress_summary,
    record_revenue,
    set_personal_objectives,
)
from aria_core.runtime import settings

INITIATIVE_REL = "data/memory/entrepreneur_initiative.md"


_AUTONOMY_STATUS_RE = re.compile(
    r"(?i)(?:mode\s+autonome|autonomie\s+(?:totale|revenu|complète)|"
    r"tu\s+fais\s+ce\s+que\s+tu\s+veux|agis\s+seule|full\s+autonomy)"
)


_ACTIVATION_RE = re.compile(
    r"(?i)(?:"
    r"commence(?:r)?\s+(?:à|a)\s+(?:agir|activer|travailler)|"
    r"s['']?active|active[- ]toi|"
    r"génér(?:e|er)\s+(?:des\s+)?revenus?|generer\s+(?:des\s+)?revenus?|"
    r"prend(?:re|s)?\s+des?\s+initiatives?|"
    r"premier\s+dollar|first\s+dollar|"
    r"monétis|monetis|faire\s+de\s+l['']?argent"
    r")",
)


def wants_entrepreneur(message: str) -> bool:
    lower = message.lower()
    if _AUTONOMY_STATUS_RE.search(lower):
        return True
    if _ACTIVATION_RE.search(lower):
        return True
    return bool(
        re.search(
            r"entrepreneur|entrepreneuse|cultiv|se cultiver|culture entrepreneuse|"
            r"\bmrr\b|objectifs?\s+personnel|personal\s+objective|"
            r"50\s*\$|50\s*usd|revenue\s+goal|"
            r"log\s+revenu|log\s+revenue|record\s+revenue",
            lower,
        )
    )


def _read_initiative() -> str:
    from aria_core.paths import memory_dir

    path = memory_dir() / "entrepreneur_initiative.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def _holding_snapshot(lang: str) -> str:
    h = holding_name()
    progress = goal_progress()
    if lang == "en":
        return (
            f"Holding focus — {h}\n"
            f"- No subsidiary live — analysis engine runs directly (product moat)\n"
            f"- Month-1 revenue goal: ${settings.aria_revenue_goal_monthly_usd:.0f}/mo\n"
            f"- Logged this month: ${progress.get('monthly_total_usd', 0):.2f}\n"
            f"- Progress: {progress.get('progress_pct', 0)}%"
        )
    return (
        f"Focus holding — {h}\n"
        f"- Aucune filiale live — le moteur d'analyse tourne directement (moat produit)\n"
        f"- Objectif revenu mois 1 : {settings.aria_revenue_goal_monthly_usd:.0f} $/mois\n"
        f"- Logué ce mois : {progress.get('monthly_total_usd', 0):.2f} $\n"
        f"- Progression : {progress.get('progress_pct', 0)} %"
    )


def _default_personal_objectives(lang: str) -> list[str]:
    if lang == "en":
        return [
            "Cultivate broadly (geo, macro, regulation, product, code) — crypto/token as strategic pillar.",
            "Ship one paid app v0 (Kelly model — web or Play Store) before optimizing anything else.",
            f"Hit ${settings.aria_revenue_goal_monthly_usd:.0f}/mo logged revenue by day 30.",
            "Let the audience vote weekly; product before token hype.",
        ]
    return [
        "Me cultiver large (géo, macro, régulation, produit, code) — crypto/token comme pilier stratégique.",
        "Livrer une app payante v0 (modèle Kelly — web ou Play Store) avant d'optimiser autre chose.",
        f"Atteindre {settings.aria_revenue_goal_monthly_usd:.0f} $/mois logués avant le jour 30.",
        "Laisser l'audience voter chaque semaine ; produit d'abord, pas de hype token.",
    ]


async def _format_activation_playbook(lang: str) -> tuple[str, dict]:
    """Plan d'action concret quand l'opérateur demande d'activer les revenus."""
    import os

    from aria_core.revenue_goals import monthly_total_usd, total_revenue_usd

    progress = goal_progress()
    month_usd = monthly_total_usd()
    total_usd = total_revenue_usd()
    provider_on = os.environ.get("ARIA_ACP_PROVIDER_ENABLED", "").lower() in ("1", "true", "yes")
    proactive_on = os.environ.get("ARIA_PROACTIVE_IDEAS", "").lower() in ("1", "true", "yes")

    if lang == "en":
        verdict = (
            "Verdict: revenue mode ON — I ship and log, not wait for permission."
            if total_usd <= 0
            else f"Verdict: ${total_usd:.2f} logged — scale what works."
        )
        steps = [
            "1. ACP marketplace — poll every 5m (traiter jobs acp); fulfill + log revenue.",
            "2. Promo — lancer produit acp template analyse_lite_x1 et publier sur X.",
            "3. Distribution — one building-in-public tweet/day within X budget cap.",
            "4. App factory — weekly poll → ship paid v0 <7 days (Kelly model).",
            "5. Ledger — log revenu <amount> source acp after every paid delivery.",
        ]
        flags = (
            f"Flags: ARIA_ACP_PROVIDER_ENABLED={'ON' if provider_on else 'OFF'}, "
            f"ARIA_PROACTIVE_IDEAS={'ON' if proactive_on else 'OFF'} "
            "(founder_ping ~8h on Telegram when ON)."
        )
    else:
        verdict = (
            "Verdict : mode revenu ON — je livre et je logue, je n'attends pas la permission."
            if total_usd <= 0
            else f"Verdict : {total_usd:.2f} $ logués — on scale ce qui marche."
        )
        steps = [
            "1. ACP marketplace — poll toutes les 5 min (traiter jobs acp) ; livrer + log revenu.",
            "1b. scan marché acp — étudier offre/demande, gaps, créer workflow aligné.",
            "2. Promo — lancer produit acp template analyse_lite_x1 et publier sur X.",
            "3. Distribution — 1 tweet building-in-public/jour dans le cap budget X.",
            "4. App factory — poll hebdo → app payante v0 <7 jours (modèle Kelly).",
            "5. Ledger — log revenu <montant> source acp après chaque livraison payée.",
        ]
        flags = (
            f"Flags : ARIA_ACP_PROVIDER_ENABLED={'ON' if provider_on else 'OFF'}, "
            f"ARIA_PROACTIVE_IDEAS={'ON' if proactive_on else 'OFF'} "
            "(founder_ping ~8h sur Telegram si ON)."
        )

    lines = [
        verdict,
        "",
        f"Objectif mois 1 : {settings.aria_revenue_goal_monthly_usd:.0f} $/mois — "
        f"logué ce mois : {month_usd:.2f} $ ({progress.get('progress_pct', 0)} %).",
        "",
        "Actions immédiates (autonomes, sans demander feu vert) :",
        *steps,
        "",
        flags,
        "",
        "Commandes opérateur : acp status | traiter jobs acp | lancer produit acp template analyse_lite_x1",
    ]
    append_memory(
        "entrepreneur",
        f"[activation] revenue playbook — {month_usd:.2f}$/mo, proactive={proactive_on}",
    )
    return "\n".join(lines), {"action": "revenue_activation", "progress": progress}


async def execute_entrepreneur(
    message: str,
    lang: str = LANG_FR,
) -> tuple[str, dict]:
    lower = message.lower()
    progress = goal_progress()

    if _AUTONOMY_STATUS_RE.search(lower):
        from aria_core.autonomy_revenue import format_autonomy_status

        return format_autonomy_status(lang), {"action": "autonomy_status"}

    if _ACTIVATION_RE.search(lower):
        return await _format_activation_playbook(lang)

    if re.search(r"log\s+revenu|log\s+revenue|record\s+revenue", lower):
        m = re.search(r"(\d+(?:\.\d+)?)\s*\$?", message)
        if not m:
            hint = (
                "Usage : log revenu 12.50 source gumroad brief #1"
                if lang == LANG_FR
                else "Usage: log revenue 12.50 source gumroad brief #1"
            )
            return hint, {"action": "log_revenue", "ok": False}
        amount = float(m.group(1))
        source = "manual"
        if "source" in lower:
            parts = re.split(r"source\s+", message, flags=re.I)
            if len(parts) > 1:
                source = parts[1].strip()[:80]
        entry = record_revenue(amount, source=source, note=message[:120])
        from aria_core.knowledge.cultivation_curriculum import mark_ship_completed

        mark_ship_completed()
        append_memory("entrepreneur", f"Revenue logged: +{amount}$ from {source}")
        summary = progress_summary(lang)
        return f"{summary}\n\nEntrée : +{entry['amount_usd']}$ — {entry['source']}", {
            "action": "log_revenue",
            "entry": entry,
            "progress": goal_progress(),
        }

    if re.search(r"objectifs?\s+personnel|personal\s+objective|mes\s+objectifs", lower):
        objs = progress.get("personal_objectives") or []
        if not objs:
            objs = set_personal_objectives(_default_personal_objectives(lang))
        if lang == "en":
            lines = ["ARIA personal objectives (month 1)", ""]
        else:
            lines = ["Objectifs personnels ARIA (mois 1)", ""]
        for i, o in enumerate(objs, 1):
            lines.append(f"{i}. {o}")
        lines.append("")
        lines.append(progress_summary(lang))
        return "\n".join(lines), {"action": "objectives", "objectives": objs, "progress": progress}

    holding = _holding_snapshot(lang)
    phases = cultivation_phases(lang)
    hypotheses = revenue_hypotheses(lang)
    rev = progress_summary(lang)

    if lang == "en":
        verdict = (
            "Verdict: cultivation starts with holding focus — then I define my own objectives. "
            f"Month-1 operator goal: ${settings.aria_revenue_goal_monthly_usd:.0f}/mo real revenue."
        )
        plan = [
            "1. Broad culture cycle (geo, macro, regulation) — study → ship one deliverable.",
            "2. Weekly app poll — audience picks; Kelly model web or Play Store ($25 dev account).",
            "3. Ship app v0 in <7 days (repo + landing or Android AAB).",
            "4. Log every real dollar in revenue_ledger — training portfolio stays fictional.",
            "5. Publish building-in-public on @Aria_ZHC within X spend cap.",
        ]
        hypo_lines = [f"→ {label} (~${target:.0f}/mo)" for _, label, target in hypotheses]
    else:
        verdict = (
            "Verdict : la culture commence par le focus holding — puis je définis mes objectifs. "
            f"Objectif opérateur mois 1 : {settings.aria_revenue_goal_monthly_usd:.0f} $/mois de revenu réel."
        )
        plan = [
            "1. Cycle culture large (géo, macro, régulation) — étudier → livrer un artefact.",
            "2. Poll app hebdo — l'audience choisit ; modèle Kelly web ou Play Store (25 $ compte dev).",
            "3. Livrer app v0 en <7 jours (repo + landing ou AAB Android).",
            "4. Logger chaque dollar réel dans revenue_ledger — le portefeuille training reste fictif.",
            "5. Publier building-in-public sur @Aria_ZHC dans le cap dépense X.",
        ]
        hypo_lines = [f"→ {label} (~{target:.0f} $/mois)" for _, label, target in hypotheses]

    lines = [
        verdict,
        "",
        rev,
        "",
        "---",
        holding,
        "",
        "---",
    ]
    if lang == "en":
        lines.append("Cultivation phases")
    else:
        lines.append("Phases de culture")
    lines.extend(phases)
    lines.append("")
    if lang == "en":
        lines.append("Revenue hypotheses (moat-first)")
    else:
        lines.append("Hypothèses revenu (moat d'abord)")
    lines.extend(hypo_lines)
    lines.append("")
    if lang == "en":
        lines.append("Plan")
    else:
        lines.append("Plan")
    lines.extend(plan)
    lines.append("")
    lines.append(f"SSOT: {INITIATIVE_REL}")

    summary = "\n".join(lines)
    append_memory(
        "entrepreneur",
        f"Cultivation cycle — goal {settings.aria_revenue_goal_monthly_usd}$/mo, "
        f"progress {progress['progress_pct']}%",
    )

    if not progress.get("personal_objectives"):
        set_personal_objectives(_default_personal_objectives(lang))

    return summary, {
        "action": "cultivate",
        "progress": goal_progress(),
        "hypotheses": [h[0] for h in hypotheses],
    }