"""Audit « ok tout est prêt — qu'est-ce qu'il manque ? » — opérateur operateur."""
from __future__ import annotations

import re
from typing import Any

import httpx

from aria_core.llm import is_llm_configured
from aria_core.runtime import settings

_READINESS_RE = re.compile(
    r"(?:"
    r"qu['']?est[- ]?ce qu['']?il manque"
    r"|il manque.*pour que tu"
    r"|what(?:'s| is) missing.*for you"
    r"|(?:ok|oui|yes).{0,40}(?:tout est pr[eê]t|maintenant|c['']est bon|ready)"
    r"|tout est pr[eê]t.{0,60}(?:manque|pour que tu)"
    r")",
    re.IGNORECASE | re.DOTALL,
)
_GO_AHEAD_RE = re.compile(
    r"(?:"
    r"si c['']est b[eé]n[eé]fique"
    r"|si [çc]a t['']aide"
    r"|fais[- ]?le\b"
    r"|fait[- ]?le\b"
    r"|(?:^|\s)(?:vazy|vas[- ]?y)\b"
    r"|go ahead"
    r"|avance\b"
    r")",
    re.IGNORECASE,
)
_GOAL_RE = re.compile(
    r"pour que tu (?:puisses?|peux|puisse)\s+(.+?)(?:[.?!,]|$)",
    re.IGNORECASE,
)
_STATUS_PULSE_RE = re.compile(
    r"(?:"
    r"rien\s+de\s+nouveau(?:\s+(?:a|à)\s+)?(?:d[eé]clar|signal|rapporter)?"
    r"|quoi\s+de\s+neuf"
    r"|(?:y\s*['']?a[- ]?t[- ]?il|as[- ]?tu)\s+quelque\s+chose\s+(?:a|à)\s+"
    r"(?:d[eé]clar|signal|rapporter)"
    r"|(?:something|anything)\s+new\s+to\s+report"
    r")",
    re.IGNORECASE,
)


def wants_operator_status_pulse(message: str) -> bool:
    text = (message or "").strip()
    if len(text) < 10:
        return False
    return bool(_STATUS_PULSE_RE.search(text))


def wants_operator_readiness(message: str) -> bool:
    text = (message or "").strip()
    if len(text) < 12:
        return False
    return bool(_READINESS_RE.search(text))


def wants_operator_go_ahead(message: str) -> bool:
    return bool(_GO_AHEAD_RE.search((message or "").strip()))


def parse_readiness_goal(message: str) -> str:
    m = _GOAL_RE.search(message or "")
    if not m:
        return ""
    return m.group(1).strip()[:200]


async def _probe_local_health() -> tuple[bool, str]:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get("http://127.0.0.1:8000/api/health")
            if r.status_code == 200 and (r.json() or {}).get("status") == "ok":
                return True, "API locale :8000 OK"
            return False, f"API :8000 HTTP {r.status_code}"
    except Exception as exc:
        return False, f"API :8000 injoignable ({exc.__class__.__name__})"


def _pending_worker_count() -> int:
    try:
        from aria_core.aria_worker_queue import count_pending_tasks, resolve_local_worker_md

        path = resolve_local_worker_md()
        if not path or not path.is_file():
            return 0
        return count_pending_tasks(path.read_text(encoding="utf-8"))
    except OSError:
        return 0


async def collect_readiness_gaps(
    *, goal: str = "",
) -> tuple[list[dict[str, Any]], list[str]]:
    gaps: list[dict[str, Any]] = []
    ok_items: list[str] = []

    if is_llm_configured():
        prov = settings.llm_provider or "?"
        model = settings.llm_model or "default"
        ok_items.append(f"LLM cloud : {prov} / {model}")
    else:
        gaps.append({
            "id": "llm_config",
            "label": "LLM non configuré (ARIA_LLM_ENABLED ou clé API)",
            "worker": "configurer vault local.env + redémarrer bot",
            "capability_id": "",
        })

    if not (settings.github_token or "").strip():
        gaps.append({
            "id": "github_token",
            "label": "GITHUB_TOKEN absent — skills GitHub bloqués",
            "worker": "ajouter token dans vault + sync-local.ps1",
            "capability_id": "",
        })
    else:
        ok_items.append("GitHub token présent")

    try:
        from aria_core.skills.acp_cli import is_acp_available

        if is_acp_available():
            ok_items.append("ACP cli disponible")
        else:
            gaps.append({
                "id": "acp_cli",
                "label": "acp-cli introuvable — marketplace ACP limitée",
                "worker": "npm install -g @virtuals-protocol/acp-cli",
                "capability_id": "",
            })
    except Exception:
        gaps.append({
            "id": "acp_cli",
            "label": "ACP cli — vérification impossible",
            "worker": "vérifier acp-cli sur le PC",
            "capability_id": "",
        })

    health_ok, health_detail = await _probe_local_health()
    if health_ok:
        ok_items.append(health_detail)
    else:
        gaps.append({
            "id": "local_health",
            "label": health_detail,
            "worker": "lancer start-acp-local.ps1",
            "capability_id": "health_render_regression",
        })

    pending = _pending_worker_count()
    if pending:
        gaps.append({
            "id": "worker_pending",
            "label": f"{pending} tâche(s) ouvrier [pending] dans ARIA-WORKER.md",
            "worker": "ouvrier Cursor traite la file",
            "capability_id": "",
        })

    goal_l = (goal or "").lower()
    if goal_l and re.search(r"banni[eè]re|banner|profil\s+x|@aria", goal_l):
        try:
            from aria_core.x_banner import get_x_banner_status

            status = await get_x_banner_status()
            if not status.get("x_configured"):
                gaps.append({
                    "id": "x_oauth",
                    "label": "Clés X OAuth Read+Write manquantes",
                    "worker": "sync-render / vault X API",
                    "capability_id": "x_oauth_write",
                })
            elif not status.get("has_banner"):
                gaps.append({
                    "id": "x_banner",
                    "label": "Bannière X absente sur le profil",
                    "worker": "self_maintenance bannière ou IMAGE_API_KEY",
                    "capability_id": "x_banner_generate",
                })
            else:
                ok_items.append("Bannière X — profil OK")
        except Exception:
            pass

    return gaps, ok_items


async def _maybe_act_on_gaps(
    gaps: list[dict[str, Any]],
    *,
    lang: str,
) -> list[str]:
    """Actions sûres : cap-gap catalogué + file ouvrier — pas d'install auto aveugle."""
    actions: list[str] = []
    for gap in gaps:
        cap_id = (gap.get("capability_id") or "").strip()
        if not cap_id:
            continue
        try:
            from aria_core.capability_gap import file_capability_gap, format_gap_reply

            record = await file_capability_gap(
                cap_id,
                context=f"operator_readiness: {gap.get('label', '')}",
                lang=lang,
                open_pr=False,
            )
            if record.get("issue_url"):
                actions.append(f"Issue : {record['issue_url']}")
            elif record.get("deduped"):
                actions.append(f"Cap-gap {cap_id} déjà documentée (7j)")
            reply = format_gap_reply(record, lang=lang)
            if reply:
                actions.append(reply[:280])
        except Exception as exc:
            actions.append(f"Cap-gap {cap_id} : {exc.__class__.__name__}")
    return actions


async def execute_operator_status_pulse(message: str, *, lang: str = "fr") -> tuple[str, dict[str, Any]]:
    """Pulse opérateur — file ouvrier, santé locale, journal (sans web)."""
    gaps, ok_items = await collect_readiness_gaps()
    lang_key = "fr" if lang == "fr" else "en"
    lines: list[str] = []

    if lang_key == "fr":
        lines.append("Pulse opérateur — sources locales (pas de web)")
        lines.append("")
        if gaps:
            lines.append("À signaler :")
            for g in gaps:
                lines.append(f"  • {g['label']}")
                if g.get("worker"):
                    lines.append(f"    → {g['worker']}")
        else:
            lines.append("Rien de nouveau à déclarer — tout est calme côté ARIA.")
        if ok_items:
            lines.append("")
            lines.append("OK :")
            lines.extend(f"  ✓ {item}" for item in ok_items[:6])
    else:
        lines.append("Operator pulse — local sources (no web)")
        if gaps:
            lines.extend(f"  • {g['label']}" for g in gaps)
        else:
            lines.append("Nothing new to report — ARIA stack looks quiet.")
        lines.extend(f"  OK {item}" for item in ok_items[:6])

    try:
        from aria_core.memory import get_journal_summary

        journal = (get_journal_summary() or "").strip()
        if journal:
            tail = "\n".join(journal.splitlines()[-4:])
            if lang_key == "fr":
                lines.append("")
                lines.append("Journal (fin) :")
            else:
                lines.append("")
                lines.append("Journal (tail) :")
            lines.append(tail[:500])
    except Exception:
        pass

    data = {
        "operator_status_pulse": True,
        "gaps": gaps,
        "ok": ok_items,
        "skip_web": True,
    }
    return "\n".join(lines), data


async def execute_operator_readiness(message: str, *, lang: str = "fr") -> tuple[str, dict[str, Any]]:
    goal = parse_readiness_goal(message)
    go = wants_operator_go_ahead(message)
    gaps, ok_items = await collect_readiness_gaps(goal=goal)

    lang_key = "fr" if lang == "fr" else "en"
    lines: list[str] = []
    if lang_key == "fr":
        lines.append("Audit opérateur — autonomie ARIA")
        if goal:
            lines.append(f"Objectif : {goal}")
        lines.append("")
        if ok_items:
            lines.append("Prêt :")
            lines.extend(f"  ✅ {item}" for item in ok_items)
        if gaps:
            lines.append("")
            lines.append("Il manque :")
            for g in gaps:
                lines.append(f"  ❌ {g['label']}")
                if g.get("worker"):
                    lines.append(f"     → {g['worker']}")
        if not gaps:
            lines.append("")
            lines.append("Rien de bloquant détecté — tu peux avancer.")
    else:
        lines.append("Operator readiness audit")
        if goal:
            lines.append(f"Goal: {goal}")
        for item in ok_items:
            lines.append(f"  OK {item}")
        for g in gaps:
            lines.append(f"  MISSING {g['label']}")

    acted: list[str] = []
    if go and gaps:
        acted = await _maybe_act_on_gaps(
            [g for g in gaps if g.get("capability_id")],
            lang=lang_key,
        )
        if lang_key == "fr":
            lines.append("")
            lines.append("Actions (bénéfique pour moi — sans install aveugle) :")
            if acted:
                lines.extend(f"  • {a}" for a in acted)
            else:
                lines.append(
                    "  • Lacunes listées — dis « ajoute tâche ouvrier pour X » "
                    "si tu veux du code."
                )
        elif acted:
            lines.extend(acted)

    data = {
        "operator_readiness": True,
        "goal": goal,
        "go_ahead": go,
        "gaps": gaps,
        "ok": ok_items,
        "actions": acted,
    }
    return "\n".join(lines), data