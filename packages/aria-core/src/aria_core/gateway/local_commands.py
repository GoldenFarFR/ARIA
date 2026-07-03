"""Commandes opérateur locales — même sémantique que Telegram, sans bot."""
from __future__ import annotations

import re
from typing import Any

_SLASH_RE = re.compile(r"^\s*/(\w+)(?:@\S+)?(?:\s+(.*))?$", re.DOTALL)

_LOCAL_COMMANDS = frozenset({
    "directive",
    "learn",
    "status",
    "qi",
    "level",
    "calibrate",
    "help",
    "commandes",
    "commands",
})


def parse_local_command(message: str) -> tuple[str, str] | None:
    """Retourne (commande, args) si message commence par /commande."""
    m = _SLASH_RE.match((message or "").strip())
    if not m:
        return None
    cmd = (m.group(1) or "").lower()
    if cmd not in _LOCAL_COMMANDS:
        return None
    return cmd, (m.group(2) or "").strip()


def is_local_command(message: str) -> bool:
    return parse_local_command(message) is not None


def _help_text(lang: str) -> str:
    if lang == "fr":
        return (
            "Commandes locales (console / API opérateur — pas besoin de Telegram)\n\n"
            "/directive <règle permanente>\n"
            "/learn <topic> | <leçon>\n"
            "/status — état ARIA (LLM, ACP, proactive, GitHub, X)\n"
            "/qi — indice capacité\n"
            "/level — niveaux · /level up codage [note]\n"
            "/calibrate <affirmation> | vrai|faux|incertain | [source]\n"
            "/help — cette aide\n\n"
            "Langage naturel : acp status, scan marché acp, traiter jobs acp, etc."
        )
    return (
        "Local operator commands (console / operator API — no Telegram needed)\n\n"
        "/directive <permanent rule>\n"
        "/learn <topic> | <lesson>\n"
        "/status\n"
        "/qi\n"
        "/level · /level up coding [note]\n"
        "/calibrate <claim> | true|false|uncertain | [source]\n"
        "/help\n"
    )


async def _cmd_status(lang: str) -> str:
    from aria_core.capability_levels import check_auto_completions, global_index
    from aria_core.gateway.x_twitter import is_x_post_configured, is_x_read_configured
    from aria_core.heartbeat import aria_heartbeat
    from aria_core.identity import official_x_at
    from aria_core.llm import is_llm_configured, is_llm_provider_configured
    from aria_core.runtime import settings
    from aria_core.skills.acp_cli import is_acp_available
    from aria_core.skills.github_skill import github_configured, github_unlimited_access

    check_auto_completions()
    hb = aria_heartbeat.get_status()
    last = hb.get("last_heartbeat")
    last_str = last.strftime("%H:%M UTC") if last else "never"
    qi = global_index()
    gh = "unlimited" if github_configured() and github_unlimited_access() else (
        "configured" if github_configured() else "missing"
    )
    x_post = "ok" if is_x_post_configured() else "missing"
    x_read = "ok" if is_x_read_configured() else "off"
    provider = settings.llm_provider or "none"
    acp = "cli ok" if is_acp_available() else "no cli"
    if getattr(settings, "aria_acp_provider_enabled", False):
        acp += " · provider ON"
    proactive = "on" if settings.aria_proactive_ideas else "off"

    if lang == "fr":
        return (
            f"ARIA — Status (console locale)\n"
            f"Indice : {qi} / 1000 — /qi pour détail\n"
            f"Heartbeat : {last_str}\n"
            f"Canal : console locale (pas Telegram requis)\n"
            f"X {official_x_at()} : post {x_post} · read {x_read}\n"
            f"LLM : {'actif' if is_llm_configured() else 'off'} "
            f"({provider} {'ok' if is_llm_provider_configured() else 'missing'})\n"
            f"GitHub : {gh}\n"
            f"ACP : {acp}\n"
            f"Proactive : {proactive}\n"
            f"Public grounded : {'on' if settings.aria_grounded_mode else 'off'}"
        )
    return (
        f"ARIA — Status (local console)\n"
        f"Index: {qi}/1000\n"
        f"Heartbeat: {last_str}\n"
        f"Channel: local console\n"
        f"X: post {x_post} read {x_read}\n"
        f"LLM: {'on' if is_llm_configured() else 'off'} ({provider})\n"
        f"GitHub: {gh}\n"
        f"ACP: {acp}\n"
        f"Proactive: {proactive}"
    )


async def execute_local_command(message: str, lang: str = "fr") -> tuple[str, dict[str, Any]] | None:
    """Exécute une commande / locale. Retourne None si pas une commande."""
    parsed = parse_local_command(message)
    if not parsed:
        return None
    cmd, args = parsed
    lang_key = "fr" if (lang or "").lower().startswith("fr") else "en"

    if cmd in ("help", "commandes", "commands"):
        return _help_text(lang_key), {"local_command": "help"}

    if cmd == "status":
        return await _cmd_status(lang_key), {"local_command": "status"}

    if cmd == "directive":
        if not args:
            usage = "Usage: /directive <règle permanente pour ARIA>"
            return usage, {"local_command": "directive", "ok": False}
        from aria_core.directives import append_directive

        entry = append_directive(args)
        msg = (
            "Directive enregistrée — prioritaire dans chaque appel LLM."
            if lang_key == "fr"
            else "Directive saved — prioritized in every LLM call."
        )
        return f"{msg}\n\n{entry}", {"local_command": "directive", "ok": True}

    if cmd == "learn":
        if "|" not in args:
            usage = (
                "Usage: /learn <topic> | <leçon>\n"
                "Ex: /learn acp | Chaque scan doit proposer 1 workflow <24h"
            )
            return usage, {"local_command": "learn", "ok": False}
        topic, content = [p.strip() for p in args.split("|", 1)]
        if not topic or not content:
            return "Topic et leçon requis.", {"local_command": "learn", "ok": False}
        from aria_core.knowledge.memory_triage import triaged_add_knowledge
        from aria_core.memory import append_memory

        item, triage_result = await triaged_add_knowledge(
            source="manual",
            topic=topic[:64],
            content=content[:2000],
            confidence=0.95,
            approved=True,
            skip_triage=False,
        )
        if item is None:
            return f"Rejeté par triage : {triage_result}", {"local_command": "learn", "ok": False}
        append_memory("curiosity", f"[manual/local] [{topic}] {content[:120]}")
        return (
            f"Appris [{item.id}] {topic}: {content[:200]}..."
            if lang_key == "fr"
            else f"Learned [{item.id}] {topic}: {content[:200]}..."
        ), {"local_command": "learn", "ok": True, "id": item.id}

    if cmd == "qi":
        from aria_core.skills.capability_skill import execute_capability

        out, data = await execute_capability("montre qi aria", lang=lang_key)
        return out, {"local_command": "qi", **data}

    if cmd == "level":
        from aria_core.skills.capability_skill import execute_capability

        if not args:
            out, data = await execute_capability("niveaux aria", lang=lang_key)
            hint = "\n\nUsage: /level up codage [note]" if lang_key == "fr" else "\n\nUsage: /level up coding [note]"
            return out + hint, {"local_command": "level", **data}
        out, data = await execute_capability(f"/level {args}", lang=lang_key)
        return out, {"local_command": "level", **data}

    if cmd == "calibrate":
        if not args:
            usage = (
                "Usage: /calibrate <affirmation> | vrai|faux|incertain | [source]"
                if lang_key == "fr"
                else "Usage: /calibrate <claim> | true|false|uncertain | [source]"
            )
            return usage, {"local_command": "calibrate", "ok": False}
        from aria_core.skills.calibrate_skill import execute_calibrate

        out, data = await execute_calibrate(args, lang=lang_key)
        return out, {"local_command": "calibrate", **data}

    return None