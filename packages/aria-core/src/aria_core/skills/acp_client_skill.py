"""ACP marketplace — status, browse, poll provider (Virtuals / acp-cli)."""
from __future__ import annotations

import os
import re
from pathlib import Path

from aria_core.skills.acp_cli import (
    browse_agents,
    is_acp_available,
    list_agents,
    list_offerings,
)
from aria_core.skills.acp_offering_skill import (
    execute_adhoc_workflow_create,
    execute_offering_create,
    execute_offering_delete,
    format_templates_help,
    wants_acp_offering_create,
    wants_acp_offering_delete,
    wants_acp_offering_templates,
    wants_adhoc_acp_workflow,
)
from aria_core.skills.acp_product_launch_skill import (
    execute_product_launch,
    wants_acp_product_launch,
)
from aria_core.skills.acp_provider_skill import default_events_file, run_provider_cycle

_ACP_RE = re.compile(
    r"\b(?:acp|virtuals|marketplace|offering|job\s+acp)\b",
    re.IGNORECASE,
)

_STATUS_RE = re.compile(r"\bacp\s+status\b|état\s+acp|status\s+acp", re.I)
_POLL_RE = re.compile(
    r"traiter\s+jobs?\s+acp|poll\s+acp|drain\s+acp|jobs?\s+acp",
    re.I,
)
_BROWSE_RE = re.compile(r"\bbrowse\b|\bparcourir\b|offres?\s+disponibles", re.I)
_REVENUE_RE = re.compile(r"revenu|revenue|générer|generer|monétis|monetis|plan", re.I)


def wants_acp_marketplace(message: str) -> bool:
    text = (message or "").strip()
    if wants_acp_offering_delete(text):
        return True
    if wants_adhoc_acp_workflow(text):
        return True
    if wants_acp_offering_create(text):
        return True
    if wants_acp_offering_templates(text):
        return True
    if wants_acp_product_launch(text):
        return True
    return bool(_ACP_RE.search(text))


def _events_file_status() -> tuple[int, str]:
    path = Path(default_events_file())
    if not path.is_file():
        return 0, "absent"
    try:
        lines = sum(1 for _ in path.open(encoding="utf-8", errors="replace"))
        return lines, str(path)
    except OSError:
        return 0, "erreur lecture"


async def _format_status(lang: str) -> tuple[str, dict]:
    if not is_acp_available():
        msg = (
            "ACP — acp-cli introuvable.\n"
            "Installe : npm i -g @virtuals-protocol/acp-cli\n"
            "Puis : acp configure"
            if lang == "fr"
            else "ACP — acp-cli not found. npm i -g @virtuals-protocol/acp-cli"
        )
        return msg, {"acp": "no_cli"}

    agents, err_a = list_agents()
    offerings, err_o = list_offerings()
    lines_count, events_path = _events_file_status()
    provider_on = os.environ.get("ARIA_ACP_PROVIDER_ENABLED", "").lower() in ("1", "true", "yes")

    if lang == "fr":
        lines = ["═══ ACP STATUS ═══", ""]
        if err_a:
            lines.append(f"Agents : erreur — {err_a[:200]}")
        elif agents:
            for ag in agents[:3]:
                name = ag.get("name") or ag.get("NAME") or "?"
                aid = ag.get("id") or ag.get("ID") or "?"
                role = ag.get("role") or ag.get("ROLE") or ""
                lines.append(f"• {name} ({role}) — {aid}")
        if err_o:
            lines.append(f"Offerings : erreur — {err_o[:200]}")
        elif offerings:
            lines.append("")
            lines.append("Offres actives :")
            for off in offerings[:6]:
                name = off.get("name") or "?"
                price = off.get("priceValue") or off.get("price") or "?"
                lines.append(f"  - {name} — {price} USDC")
        lines.extend(
            [
                "",
                f"Provider poll : {'ON' if provider_on else 'OFF'} (ARIA_ACP_PROVIDER_ENABLED)",
                f"Events file : {lines_count} lignes — {events_path}",
                "",
                "Commandes : acp status | traiter jobs acp | browse offerings | plan revenus acp",
                "Workflows : templates offres acp | créer offre acp template <nom>",
                "Lancement : lancer produit acp template <nom> et publier sur X",
                "Démarrage local : vanguard\\operator\\start-acp-local.ps1",
            ]
        )
        return "\n".join(lines), {"acp": "status", "offerings": len(offerings or [])}

    lines = ["═══ ACP STATUS ═══", ""]
    if agents:
        lines.append(f"Agents: {len(agents)}")
    if offerings:
        lines.append(f"Offerings: {len(offerings)}")
    lines.append(f"Provider poll: {'ON' if provider_on else 'OFF'}")
    lines.append(f"Events: {lines_count} @ {events_path}")
    return "\n".join(lines), {"acp": "status"}


async def _format_revenue_plan(lang: str) -> tuple[str, dict]:
    offerings, _ = list_offerings()
    names = [o.get("name") for o in offerings if o.get("name")]
    if lang == "fr":
        off_txt = ", ".join(names[:4]) if names else "analyse_lite_x1, analyse_full_x1"
        return (
            "Plan revenus ACP (Aria Vanguard ZHC)\n\n"
            "1. Court terme — écouteur local + bot :8000 (start-acp-local.ps1).\n"
            f"2. Offres marketplace : {off_txt} — paiement à la livraison.\n"
            "3. Quand un job arrive : poll traite l'event → audit heuristique → submit deliverable.\n"
            "4. Revenu enregistré côté Virtuals ; objectif ARIA : log dans revenue_ledger.json.\n"
            "5. Prod Render : pas acp-cli (keychain PC) — provider reste sur ton PC.\n\n"
            "État : intégration locale active ; premier $ = 1er job client payé livré.",
            {"acp": "revenue_plan"},
        )
    return (
        "ACP revenue: local listener + bot, fulfill jobs, log revenue. Prod stays PC-side.",
        {"acp": "revenue_plan"},
    )


async def execute_acp_marketplace(message: str, lang: str = "en") -> tuple[str, dict]:
    text = (message or "").strip()
    lang_key = "fr" if lang == "fr" else "en"

    if _STATUS_RE.search(text) or re.search(r"^acp\s*$", text, re.I):
        return await _format_status(lang_key)

    if _POLL_RE.search(text):
        result = await run_provider_cycle()
        if lang_key == "fr":
            body = (
                f"Poll ACP — {result.get('events_read', 0)} event(s) lus, "
                f"{result.get('processed', 0)} job(s) traité(s)."
            )
            if result.get("actions"):
                body += "\nActions : " + ", ".join(result["actions"])
            if result.get("errors"):
                body += "\nErreurs : " + "; ".join(result["errors"])
            return body, {"acp": "poll", **result}
        return (
            f"ACP poll — read {result.get('events_read')} events, "
            f"processed {result.get('processed')}.",
            {"acp": "poll", **result},
        )

    if _BROWSE_RE.search(text):
        items, err = browse_agents("")
        if err:
            return f"Browse ACP : {err[:300]}", {"acp": "browse_error"}
        if not items:
            return "Browse ACP : aucun agent trouvé.", {"acp": "browse_empty"}
        lines = ["Agents marketplace (extrait) :"]
        for item in items[:8]:
            name = item.get("name") or item.get("agentName") or "?"
            lines.append(f"• {name}")
        return "\n".join(lines), {"acp": "browse", "count": len(items)}

    if _REVENUE_RE.search(text):
        return await _format_revenue_plan(lang_key)

    if wants_acp_product_launch(text):
        return await execute_product_launch(text, lang_key)

    if wants_acp_offering_delete(text):
        return await execute_offering_delete(text, lang_key)

    if wants_adhoc_acp_workflow(text):
        return await execute_adhoc_workflow_create(text, lang_key)

    if wants_acp_offering_create(text):
        return await execute_offering_create(text, lang_key)

    if wants_acp_offering_templates(text):
        return await format_templates_help(lang_key)

    if lang_key == "fr":
        return (
            "ACP marketplace ARIA\n\n"
            "• acp status — agent + offres + fichier events\n"
            "• traiter jobs acp — poll provider (livrer jobs en attente)\n"
            "• browse offerings — parcourir le marketplace\n"
            "• plan revenus acp — stratégie monétisation\n"
            "• templates offres acp — workflows disponibles\n"
            "• créer offre acp template <nom> — publier sur marketplace\n"
            "• lancer produit acp template <nom> et publier sur X — produit + promo\n"
            "• supprime le workflow <nom> — retirer une offre du marketplace\n\n"
            "Lance : vanguard\\operator\\start-acp-local.ps1",
            {"acp": "help"},
        )
    return (
        "ACP: acp status | traiter jobs acp | browse offerings | revenue plan",
        {"acp": "help"},
    )