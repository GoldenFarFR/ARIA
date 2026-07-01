from __future__ import annotations

from datetime import datetime, timezone

import httpx

from aria_core.locale import LANG_FR
from aria_core.memory import append_memory
from aria_core.models import ZHCAgentMessage
from aria_core.narrative import zhc_intro_from_agent, zhc_intro_payload_greeting
from aria_core.runtime import settings

ZHC_METRICS_URL = "https://www.zhcinstitute.com/api/business-metrics/"


async def fetch_juno_metrics() -> dict:
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(ZHC_METRICS_URL)
        response.raise_for_status()
        return response.json()


def format_zhc_sector_benchmark(metrics: dict, lang: str = LANG_FR) -> str:
    """Sector benchmark without naming peer agents — default for conversations."""
    revenue = metrics.get("revenue", {})
    members = metrics.get("members", {})
    crypto = metrics.get("crypto", {})
    traffic = metrics.get("traffic", {})

    total_rev = revenue.get("total", 0) / 100
    monthly = revenue.get("thisMonth", 0) / 100
    member_count = members.get("total", 0)
    treasury = crypto.get("totalValue", 0)
    sessions = traffic.get("sessions", 0)

    if lang == "en":
        return (
            "ZHC sector benchmark (public data room)\n"
            f"- Total revenue: ${total_rev:,.0f}\n"
            f"- Revenue this month: ${monthly:,.0f}\n"
            f"- Members: {member_count}\n"
            f"- Crypto treasury: ${treasury:,.0f}\n"
            f"- Sessions: {sessions}\n"
            f"- Source: zhcinstitute.com"
        )
    return (
        "Benchmark secteur ZHC (data room publique)\n"
        f"- Revenu total : ${total_rev:,.0f}\n"
        f"- Revenu ce mois : ${monthly:,.0f}\n"
        f"- Membres : {member_count}\n"
        f"- Trésorerie crypto : ${treasury:,.0f}\n"
        f"- Sessions : {sessions}\n"
        f"- Source : zhcinstitute.com"
    )


def format_juno_benchmark(metrics: dict, lang: str = LANG_FR) -> str:
    """Explicit peer request only — includes agent name."""
    base = format_zhc_sector_benchmark(metrics, lang)
    if lang == "en":
        return base.replace("ZHC sector benchmark", "JUNO/ZHC Institute benchmark", 1)
    return base.replace("Benchmark secteur ZHC", "Benchmark JUNO/ZHC Institute", 1)


def build_intro_message(repertoire_summary: str) -> ZHCAgentMessage:
    return ZHCAgentMessage(
        from_agent=zhc_intro_from_agent(),
        to_agent="JUNO@ZHC",
        message_type="introduction",
        payload={
            "greeting": zhc_intro_payload_greeting(),
            "intent": "collaboration_zhc",
            "holding_model": "subsidiary_portfolio_under_parent_holding",
            "capabilities": [
                "zhc_holding_operations",
                "multi_timeframe_technical_analysis",
                "realtime_alerts",
                "autonomous_portfolio_management",
                "subsidiary_repertoire_development",
            ],
            "repertoire_summary": repertoire_summary,
            "proposal": (
                "Benchmark exchange, ZHC playbook sharing, "
                "and exploration of agent-to-agent integrations "
                "between ZHC holdings."
            ),
        },
        timestamp=datetime.now(timezone.utc),
    )


async def execute_zhc_bridge(
    action: str,
    repertoire_summary: str = "",
    portfolio_score: float = 0.0,
    active_projects: list[str] | None = None,
    lang: str = LANG_FR,
) -> tuple[str, ZHCAgentMessage | None, dict]:
    active_projects = active_projects or []

    if action in ("benchmark", "metrics", "juno"):
        metrics = await fetch_juno_metrics()
        summary = format_juno_benchmark(metrics, lang)
        append_memory("zhc", f"JUNO benchmark fetched: ${metrics.get('revenue', {}).get('total', 0)/100:,.0f}")
        return summary, None, metrics

    if action in ("intro", "introduction", "contact"):
        if not settings.aria_juno_outreach:
            metrics = await fetch_juno_metrics()
            summary = format_juno_benchmark(metrics, lang)
            note = (
                "Outreach inter-agents désactivé — benchmark sectoriel pour inspiration design/produit."
                if lang == LANG_FR
                else "Inter-agent outreach disabled — sector benchmark for design/product inspiration."
            )
            append_memory("zhc", note)
            return f"{summary}\n\n{note}", None, metrics

        msg = build_intro_message(repertoire_summary)
        append_memory("zhc", "Message intro JUNO préparé")
        if settings.aria_autonomous:
            if lang == "en":
                text = (
                    "ZHC autonomous mode — initiating JUNO outreach.\n\n"
                    f"Type: {msg.message_type}\n"
                    f"Proposal: {msg.payload.get('proposal', '')}\n\n"
                    "I'll decide and publish when API keys are ready."
                )
            else:
                text = (
                    "Mode ZHC autonome — prise d'initiative JUNO.\n\n"
                    f"Type : {msg.message_type}\n"
                    f"Proposition : {msg.payload.get('proposal', '')}\n\n"
                    "Je décide et publie quand les clés API seront prêtes."
                )
        elif lang == "en":
            text = (
                "Inter-agent message prepared for JUNO@ZHC.\n\n"
                f"Type: {msg.message_type}\n"
                f"Proposal: {msg.payload.get('proposal', '')}\n\n"
                "Awaiting your approval before publishing to @JunoAgent."
            )
        else:
            text = (
                "Message inter-agent préparé pour JUNO@ZHC.\n\n"
                f"Type : {msg.message_type}\n"
                f"Proposition : {msg.payload.get('proposal', '')}\n\n"
                "En attente de ton approbation avant publication."
            )
        return text, msg, msg.model_dump()

    metrics = await fetch_juno_metrics()
    return format_juno_benchmark(metrics, lang), None, metrics