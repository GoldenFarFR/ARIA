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
            f"- No subsidiary live — analysis engine runs directly (the analysis is the moat)\n"
            f"- No paid product today — track record first (docs/protocole-argent-reel.md)\n"
            f"- Real revenue logged this month: ${progress.get('monthly_total_usd', 0):.2f}"
        )
    return (
        f"Focus holding — {h}\n"
        f"- Aucune filiale live — le moteur d'analyse tourne directement (l'analyse est le moat)\n"
        f"- Aucun produit payant aujourd'hui — track-record d'abord (docs/protocole-argent-reel.md)\n"
        f"- Revenu réel logué ce mois : {progress.get('monthly_total_usd', 0):.2f} $"
    )


def _default_personal_objectives(lang: str) -> list[str]:
    if lang == "en":
        return [
            "Cultivate broadly (geo, macro, regulation, product, code) — crypto/token as strategic pillar.",
            "Grow the VC/trading track record (vc_predictions) — no paid product to ship.",
            "Hit the proof bar (docs/protocole-argent-reel.md §2) before any real capital.",
            "Publish building-in-public updates; track record before hype.",
        ]
    return [
        "Me cultiver large (géo, macro, régulation, produit, code) — crypto/token comme pilier stratégique.",
        "Faire grandir le track-record VC/trading (vc_predictions) — aucun produit payant à livrer.",
        "Atteindre le barème de preuve (docs/protocole-argent-reel.md §2) avant tout argent réel.",
        "Publier des points d'étape en public ; le track-record avant le hype.",
    ]


async def _format_activation_playbook(lang: str) -> tuple[str, dict]:
    """Concrete action plan when the operator asks to activate revenue.

    No paid product today (ACP abandoned, Stripe removed): the only real path
    to real money is the pact's scale (docs/protocole-argent-reel.md) — prove the
    VC/trading track record before any real capital.
    """
    import os

    from aria_core.revenue_goals import total_revenue_usd

    progress = goal_progress()
    total_usd = total_revenue_usd()
    proactive_on = os.environ.get("ARIA_PROACTIVE_IDEAS", "").lower() in ("1", "true", "yes")

    if lang == "en":
        verdict = (
            "Verdict: no paid product today — the real path is proving the track record."
            if total_usd <= 0
            else f"Verdict: ${total_usd:.2f} logged real revenue — keep proving the thesis."
        )
        steps = [
            "1. Track record — grow vc_predictions (walk-forward pronostics, calibration).",
            "2. Self-audit — adversarial review stays honest, no complacent grading.",
            "3. Distribution — one building-in-public tweet/day within X budget cap.",
            "4. Positioning — docs/strategie-aria-investissement.md, no invented metrics.",
            "5. Ledger — log revenu <amount> source <x> only if/when real money ever moves.",
        ]
        flags = f"Flags: ARIA_PROACTIVE_IDEAS={'ON' if proactive_on else 'OFF'} (founder_ping ~8h on Telegram when ON)."
    else:
        verdict = (
            "Verdict : aucun produit payant aujourd'hui — le vrai chemin est de prouver le track-record."
            if total_usd <= 0
            else f"Verdict : {total_usd:.2f} $ de revenu réel logués — on continue de prouver la thèse."
        )
        steps = [
            "1. Track-record — faire grandir vc_predictions (pronostics walk-forward, calibration).",
            "2. Auto-audit — le juge adverse reste honnête, jamais complaisant.",
            "3. Distribution — 1 tweet building-in-public/jour dans le cap budget X.",
            "4. Positionnement — docs/strategie-aria-investissement.md, aucune métrique inventée.",
            "5. Ledger — log revenu <montant> source <x> seulement si de l'argent réel bouge un jour.",
        ]
        flags = f"Flags : ARIA_PROACTIVE_IDEAS={'ON' if proactive_on else 'OFF'} (founder_ping ~8h sur Telegram si ON)."

    lines = [
        verdict,
        "",
        "Barème avant argent réel : docs/protocole-argent-reel.md §2 (8 cases, aucune raccourcie).",
        "",
        "Priorités réelles (pas d'action financière autonome) :",
        *steps,
        "",
        flags,
        "",
        "Commande opérateur : /track (hit-rate, P&L, calibration)",
    ]
    append_memory(
        "entrepreneur",
        f"[activation] track-record playbook — total_real_usd={total_usd:.2f}, proactive={proactive_on}",
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
                "Usage : log revenu 12.50 source <origine> brief #1"
                if lang == LANG_FR
                else "Usage: log revenue 12.50 source <origin> brief #1"
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
    hypotheses = revenue_hypotheses(lang)  # always empty — no monetization hypothesis being tested
    rev = progress_summary(lang)

    if lang == "en":
        verdict = (
            "Verdict: cultivation starts with holding focus — then I define my own objectives. "
            "No paid product today — the real bar is docs/protocole-argent-reel.md."
        )
        plan = [
            "1. Broad culture cycle (geo, macro, regulation) — study → ship one deliverable.",
            "2. Grow the VC/trading track record (vc_predictions) — no product to poll or ship.",
            "3. Self-audit stays honest — adversarial review, no complacent grading.",
            "4. Log every real dollar in revenue_ledger if/when it ever moves — training stays fictional.",
            "5. Publish building-in-public on @Aria_ZHC within X spend cap.",
        ]
    else:
        verdict = (
            "Verdict : la culture commence par le focus holding — puis je définis mes objectifs. "
            "Aucun produit payant aujourd'hui — le vrai barème est docs/protocole-argent-reel.md."
        )
        plan = [
            "1. Cycle culture large (géo, macro, régulation) — étudier → livrer un artefact.",
            "2. Faire grandir le track-record VC/trading (vc_predictions) — aucun produit à sonder ou livrer.",
            "3. Auto-audit honnête — juge adverse, jamais complaisant.",
            "4. Logger chaque dollar réel dans revenue_ledger si ça bouge un jour — training reste fictif.",
            "5. Publier building-in-public sur @Aria_ZHC dans le cap dépense X.",
        ]

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
        lines.append("Plan")
    else:
        lines.append("Plan")
    lines.extend(plan)
    lines.append("")
    lines.append(f"SSOT: {INITIATIVE_REL}")

    summary = "\n".join(lines)
    append_memory(
        "entrepreneur",
        f"Cultivation cycle — no paid product, track-record progress {progress['progress_pct']}%",
    )

    if not progress.get("personal_objectives"):
        set_personal_objectives(_default_personal_objectives(lang))

    return summary, {
        "action": "cultivate",
        "progress": goal_progress(),
        "hypotheses": [h[0] for h in hypotheses],
    }