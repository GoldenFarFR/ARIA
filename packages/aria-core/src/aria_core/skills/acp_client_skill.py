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
from aria_core.skills.acp_client_actions import (
    execute_acp_client_action,
    wants_acp_client_action,
)
from aria_core.skills.acp_market_intelligence import (
    execute_acp_leaderboard,
    execute_acp_market_research,
    wants_acp_leaderboard,
    wants_acp_market_research,
)
from aria_core.skills.acp_email_watcher import (
    execute_acp_email_watch,
    wants_acp_email_watch,
)
from aria_core.skills.acp_prepare_skill import (
    execute_acp_prepare,
    wants_acp_prepare,
)
from aria_core.skills.acp_provider_skill import default_events_file, run_provider_cycle
from aria_core.skills.acp_conversational import is_conversational_acp_question

_ACP_RE = re.compile(
    r"\b(?:acp|virtuals|marketplace|offering|job\s+acp)\b",
    re.IGNORECASE,
)

_STATUS_RE = re.compile(r"\bacp\s+status\b|état\s+acp|status\s+acp", re.I)
_POLL_RE = re.compile(
    r"traiter\s+jobs?\s+acp|poll\s+acp|drain\s+acp",
    re.I,
)
_BROWSE_RE = re.compile(r"\bbrowse\b|\bparcourir\b|offres?\s+disponibles", re.I)
_REVENUE_RE = re.compile(
    r"revenu|revenue|gagn(?:é|er)|argent|earnings|générer|generer|monétis|monetis|plan",
    re.I,
)
_WORKFLOW_REVENUE_RE = re.compile(
    r"(?:nos?\s+)?workflows?.*(?:rapport|gagn|revenu|argent)|"
    r"(?:ou|où)\s+on\s+en\s+(?:est|ai\b).*(?:workflow|revenu|argent|acp)",
    re.I,
)


def wants_acp_marketplace(message: str) -> bool:
    from aria_core.operator_conversational import is_injected_factual_claim

    text = (message or "").strip()
    if is_injected_factual_claim(text):
        return False
    if _WORKFLOW_REVENUE_RE.search(text):
        return True
    if wants_acp_market_research(text):
        return True
    if wants_acp_leaderboard(text):
        return True
    if wants_acp_prepare(text):
        return True
    if wants_acp_email_watch(text):
        return True
    if wants_acp_client_action(text):
        return True
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
                "Commandes : acp status | traiter jobs acp | préparer job acp | surveiller email acp",
                "Dégradé Virtuals : préparer job acp 0x… → coller JSON dans Hermès",
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


async def _format_conversational_status(lang: str) -> tuple[str, dict]:
    """Réponse naturelle quand Sylvain demande comment va ACP / les revenus."""
    from aria_core.revenue_goals import monthly_total_usd, total_revenue_usd

    lines_count, events_path = _events_file_status()
    month_usd = monthly_total_usd()
    total_usd = total_revenue_usd()
    offerings, _ = list_offerings()
    off_count = len(offerings or [])

    if lang == "fr":
        if month_usd > 0:
            lead = (
                f"Oui — {month_usd:.2f} $ ce mois "
                f"({total_usd:.2f} $ au total dans revenue_ledger.json)."
            )
        else:
            lead = (
                "Pas encore aujourd'hui — 0 $ ce mois. "
                f"{off_count} offre(s) sur le marketplace, listener local actif ; "
                "le premier revenu arrive au premier job client payé livré."
            )
        body = f"{lead}\n\n({lines_count} event(s) en file — {events_path})"
        return body, {"acp": "conversational_status", "offerings": off_count}

    if month_usd > 0:
        lead = f"Yes — ${month_usd:.2f} this month (${total_usd:.2f} total in revenue_ledger.json)."
    else:
        lead = (
            f"Not yet — $0 this month. {off_count} offering(s) live, local listener on; "
            "first revenue when a paid client job is delivered."
        )
    return (
        f"{lead}\n\n({lines_count} queued event(s) — {events_path})",
        {"acp": "conversational_status", "offerings": off_count},
    )


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

    if is_conversational_acp_question(text):
        return await _format_conversational_status(lang_key)

    if _STATUS_RE.search(text) or re.search(r"^acp\s*$", text, re.I):
        return await _format_status(lang_key)

    if wants_acp_prepare(text):
        return await execute_acp_prepare(text, lang_key)

    if wants_acp_email_watch(text):
        return await execute_acp_email_watch(text, lang_key)

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

    if wants_acp_leaderboard(text):
        return await execute_acp_leaderboard(text, lang_key)

    if wants_acp_market_research(text):
        return await execute_acp_market_research(text, lang_key)

    if _REVENUE_RE.search(text):
        return await _format_revenue_plan(lang_key)

    if wants_acp_client_action(text):
        return await execute_acp_client_action(text, lang_key)

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

    if re.search(r"(?i)pr[eé]vu|faire\s+quoi|concernant|plan\b|quoi\s+sur", text):
        return await _format_revenue_plan(lang_key)

    if lang_key == "fr":
        return (
            "ACP — dis « acp status » pour l'état, « plan revenus acp » pour la stratégie, "
            "« traiter jobs acp » pour livrer. (Liste complète : « aide acp ».)\n\n"
            "• acp status — agent + offres + fichier events\n"
            "• traiter jobs acp — poll provider (livrer jobs en attente)\n"
            "• browse offerings — parcourir le marketplace\n"
            "• plan revenus acp — stratégie monétisation\n"
            "• templates offres acp — workflows disponibles\n"
            "• créer offre acp template <nom> — publier sur marketplace\n"
            "• lancer produit acp template <nom> et publier sur X — produit + promo\n"
            "• réparer offres acp — upgrade premium (schémas + exemples dashboard)\n"
            "• supprime tous les workflows sur acp — vider le marketplace\n"
            "• supprime le workflow <nom> — retirer une offre\n"
            "• créer job acp offre <nom> — acheter un service\n"
            "• financer / approuver / rejeter job acp <id>\n"
            "• trade acp swap 10 USDC contre ETH — acp trade\n"
            "• négocier abonnement acp — aria_full_access\n"
            "• scan marché acp — étudier offre/demande + gaps workflows\n"
            "• préparer job acp 0x… offre analyse_lite_x1 contract 0x… — livrable JSON (Hermès)\n"
            "• surveiller email acp — alertes jobs via agents.world\n\n"
            "Lance : vanguard\\operator\\start-acp-local.ps1",
            {"acp": "help"},
        )
    return (
        "ACP: acp status | traiter jobs acp | browse offerings | revenue plan",
        {"acp": "help"},
    )