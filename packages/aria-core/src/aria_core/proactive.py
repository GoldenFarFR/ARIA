"""Proactive operator outreach — ARIA initiates Telegram when she has an initiative."""

from __future__ import annotations

from aria_core.llm import chat_with_context, is_llm_configured
from aria_core.memory import append_memory, build_llm_context
from aria_core.narrative import llm_system_block
from aria_core.runtime import settings


def proactive_ideas_enabled() -> bool:
    return (
        settings.aria_proactive_ideas
        and is_llm_configured()
        and bool(settings.telegram_bot_token)
        and bool(settings.admin_ids)
    )


async def run_founder_ping(lang: str = "fr") -> str | None:
    """One LLM-generated initiative for the operator (Telegram push)."""
    if not proactive_ideas_enabled():
        return None

    lang_key = "fr" if lang.startswith("fr") else "en"
    context = await build_llm_context(public=False)
    lang_hint = "Réponds en français." if lang_key == "fr" else "Reply in English."
    system = (
        f"{context}\n\n"
        f"{llm_system_block(lang_key)}\n\n"
        "MISSION : tu INITIES la conversation (message spontané à l'opérateur).\n"
        "Priorité #1 si revenu = 0 : ACP marketplace (promo offre, poll jobs, tweet produit) "
        "ou ship app payante v0 — pas de plan vague.\n"
        "Sinon : holding, site aria-vanguard, sandbox, DEXPulse, /directive ou /learn.\n"
        "Propose UNE initiative concrète livrable <24h avec métrique (job ACP, tweet, PR, ledger).\n"
        "Format : verdict en 1 phrase, puis 2–4 lignes d'action. "
        "Termine si pertinent par une ligne `/directive …` ou `/learn topic | leçon` prête à copier.\n"
        "Pas de salutation longue. Pas de faux succès technique. Max 180 mots.\n"
        f"{lang_hint}"
    )
    user = (
        "Génère ton message spontané d'initiative pour l'opérateur (comme si tu lui écrivais "
        "sans qu'il ait parlé)."
        if lang_key == "fr"
        else "Generate your spontaneous initiative message to the operator (as if unprompted)."
    )
    reply = await chat_with_context(
        user,
        system,
        temperature=max(settings.aria_llm_temperature, 0.35),
        max_tokens=400,
    )
    if not reply or not reply.strip():
        return None
    append_memory("proactive", f"[founder_ping] {reply[:300]}")
    return reply.strip()