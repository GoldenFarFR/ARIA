from __future__ import annotations

from datetime import datetime, timezone

from aria_core.actions import execute_contact_juno
from aria_core.curiosity import approve_pending_knowledge
from aria_core.exchanges import get_latest_juno
from aria_core.memory import append_memory
from aria_core.skills.zhc_bridge import execute_zhc_bridge

JUNO_CONTACT_COOLDOWN_DAYS = 7


async def should_contact_juno() -> tuple[bool, str]:
    """ARIA decides on her own whether a new JUNO contact is relevant."""
    latest = await get_latest_juno()
    if not latest:
        return True, "Premier contact JUNO — initiation réseau ZHC."

    age_days = (datetime.now(timezone.utc) - latest.created_at).days
    if age_days < JUNO_CONTACT_COOLDOWN_DAYS:
        return (
            False,
            f"Dernier échange JUNO #{latest.id} il y a {age_days}j — veille seule, pas de nouveau message.",
        )

    if latest.status.value in ("published", "awaiting_reply", "replied"):
        return False, f"Échange #{latest.id} en cours ({latest.status.value}) — pas de doublon."

    return True, f"Pas de contact récent (> {JUNO_CONTACT_COOLDOWN_DAYS}j) — nouvelle prise d'initiative."


async def execute_autonomous_action(action: str, context: str = "") -> str:
    """Executes a ZHC action without a human gate — logs + returns an FYI summary."""
    if action == "contact_juno":
        from aria_core.runtime import settings

        benchmark, _, _ = await execute_zhc_bridge("benchmark", lang="en")
        if not settings.aria_juno_outreach:
            append_memory("zhc", "[autonomous] Outreach JUNO désactivé — benchmark seul")
            return (
                "🤖 ARIA — veille ZHC\n\n"
                "Outreach JUNO désactivé (inspiration design uniquement).\n\n"
                f"{benchmark[:400]}"
            )

        should, reason = await should_contact_juno()
        if not should:
            append_memory("zhc", f"[autonomous] Veille JUNO — pas de contact: {reason}")
            return (
                "🤖 ARIA ZHC — veille autonome\n\n"
                f"Décision : pas de nouveau message JUNO\n"
                f"Raison : {reason}\n\n"
                f"{benchmark[:400]}"
            )

        _, summary = await execute_contact_juno(approval_id="autonomous")
        append_memory("zhc", f"[autonomous] Contact JUNO initié — {reason}")
        return (
            "🤖 ARIA ZHC — action autonome\n\n"
            f"Décision : prise d'initiative JUNO\n"
            f"Raison : {reason}\n\n"
            f"{summary[:3500]}"
        )

    if action == "choose_avatar":
        from aria_core.avatar import aria_choose_avatar, get_avatar_status

        pick_id = await aria_choose_avatar()
        status = get_avatar_status()
        note = (status.get("current") or {}).get("note", "")
        append_memory("identity", f"[autonomous] Avatar choisi : {pick_id} — {note[:200]}")
        return (
            "🤖 ARIA ZHC — identité visuelle\n\n"
            f"Photo de profil : {pick_id}\n"
            f"{note}\n\n"
            f"Sync : Telegram {'✅' if (status.get('current') or {}).get('sync', {}).get('telegram') else '—'} "
            f"· X {'✅' if (status.get('current') or {}).get('sync', {}).get('x') else '—'}"
        )

    if action == "update_x_banner":
        from aria_core.self_maintenance import run_curiosity_x_banner_cycle

        summary = await run_curiosity_x_banner_cycle(lang="fr")
        append_memory("identity", f"[autonomous] Banniere X — {summary[:300]}")
        return f"🤖 ARIA ZHC — banniere X\n\n{summary[:3500]}"

    if action == "learn_knowledge":
        count = await approve_pending_knowledge()
        append_memory("curiosity", f"[autonomous] {count} insights intégrés en mémoire cognitive")
        preview = context[:500] if context else ""
        return (
            "🤖 ARIA ZHC — apprentissage autonome\n\n"
            f"✅ {count} insight(s) X intégré(s) en mémoire cognitive\n"
            f"(mode autonome — rien à répondre)\n\n"
            f"{preview}"
        )

    append_memory("heartbeat", f"[autonomous] Action loguée : {action}")
    return f"🤖 ARIA ZHC — action autonome loguée : {action}"