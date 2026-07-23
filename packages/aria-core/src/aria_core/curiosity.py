from __future__ import annotations

import logging
import re

from aria_core.gateway.telegram_bot import request_approval
from aria_core.knowledge.cognitive import add_knowledge, get_pending
from aria_core.memory import append_memory
from aria_core.runtime import settings

logger = logging.getLogger(__name__)

_LEARN_APPROVE = frozenset({"learn yes", "yes", "oui", "ok", "y"})
_LEARN_REJECT = frozenset({"learn no", "no", "non", "n"})


def parse_learn_approval(text: str) -> bool | None:
    """True = approve, False = reject, None = not a learn approval reply."""
    clean = re.sub(r"[^\w\s]", "", text.strip().lower()).strip()
    if clean in _LEARN_APPROVE:
        return True
    if clean in _LEARN_REJECT:
        return False
    return None


async def run_curiosity_cycle(*, notifier=None) -> dict:
    """
    Fetch X volume → extract insights → store as pending knowledge → ask Telegram approval.
    Requires X API keys in .env. Without keys, returns setup instructions.

    ``notifier`` (optional, e.g. `Heartbeat._notify_telegram`): if provided AND
    `opportunity_radar_enabled()`, also mines the SAME fetch for "opportunity"
    accounts (#52 -- Base ecosystem trends) and pushes a read-only digest to
    the operator. Same for `vc_intelligence_enabled()` (#58 -- crypto VC
    theses): LLM synthesis + issue proposal if judged durable. Never an extra
    X call: reuses `raw_items` already fetched below, in both cases.
    """
    if not settings.x_api_key and not settings.x_bearer_token:
        return {
            "status": "disabled",
            "reason": "X API not configured — create @Aria_ZHC account first, then add X_API_* to .env",
            "insights": 0,
        }

    from aria_core.gateway.x_twitter import fetch_curiosity_feed, is_placeholder_x_insight
    from aria_core.knowledge.cognitive import purge_placeholder_insights

    removed = await purge_placeholder_insights()
    if removed:
        append_memory("curiosity", f"Purged {removed} placeholder X setup insight(s)")

    raw_items = await fetch_curiosity_feed()
    new_insights = 0

    from aria_core.knowledge.x_insight_relevance import (
        assess_x_insight_for_memory,
        format_assessment_log,
    )

    for item in raw_items[:12]:
        text = item.get("text", "")
        topic = item.get("topic", "zhc")
        if is_placeholder_x_insight(text, topic):
            continue
        assessment = await assess_x_insight_for_memory(text, source="x_twitter")
        if not assessment.store:
            append_memory(
                "curiosity",
                f"Tweet veille rejeté Groq ({format_assessment_log(assessment)}): {text[:50]}",
            )
            continue
        await add_knowledge(
            source="x_twitter",
            topic=topic,
            content=text[:500],
            confidence=assessment.confidence,
            approved=False,
        )
        new_insights += 1

    if new_insights > 0:
        pending = await get_pending(limit=3)
        preview = "\n".join(f"- [{k.id}] {k.content[:120]}..." for k in pending)
        if settings.aria_autonomous:
            desc = f"Aperçu des {new_insights} insight(s) X intégrés :\n{preview}"
            memory_note = f"X curiosity: {new_insights} insights intégrés (autonome)"
        else:
            desc = (
                f"ARIA ZHC learned {new_insights} insights from X.\n\n"
                f"Preview:\n{preview}\n\n"
                f"Valider en mémoire ? Réponds : oui / non"
            )
            memory_note = f"X curiosity cycle: {new_insights} insights pending approval"
        await request_approval("learn_knowledge", desc)
        append_memory("curiosity", memory_note)

    opportunities_found = 0
    if notifier is not None:
        from aria_core.opportunity_radar import (
            format_operator_digest,
            mine_curiosity_items,
            opportunity_radar_enabled,
            rank_opportunities,
        )

        if opportunity_radar_enabled():
            from aria_core.knowledge.x_watchlist import opportunity_watch_handles

            cands = mine_curiosity_items(raw_items, opportunity_watch_handles())
            ranked = rank_opportunities(cands, top=5)
            if ranked:
                opportunities_found = len(ranked)
                digest = format_operator_digest(ranked, lang="fr", top=5)
                try:
                    await notifier(f"🧭 Radar opportunités\n\n{digest}")
                    append_memory(
                        "curiosity",
                        f"[opportunity_radar] {opportunities_found} opportunité(s) surfacée(s)",
                    )
                except Exception as exc:  # noqa: BLE001 -- a failed send never blocks the cycle
                    logger.warning("opportunity_radar notify failed: %s", exc)

    vc_intelligence_result = None
    if notifier is not None:
        from aria_core.skills.vc_intelligence import (
            run_vc_intelligence_cycle,
            vc_intelligence_enabled,
        )

        if vc_intelligence_enabled():
            from aria_core.knowledge.x_watchlist import vc_watch_handles

            vc_handles = {h.lower() for h in vc_watch_handles()}
            vc_items = [
                item for item in raw_items
                if str(item.get("topic") or "").lstrip("@").lower() in vc_handles
            ]
            if vc_items:
                vc_intelligence_result = await run_vc_intelligence_cycle(
                    items=vc_items, notifier=notifier,
                )
                if vc_intelligence_result.get("outcome") == "ok":
                    append_memory(
                        "curiosity",
                        f"[vc_intelligence] synthèse postée"
                        f"{' + issue proposée' if vc_intelligence_result.get('issue_url') else ''}",
                    )

    return {
        "status": "ok",
        "insights": new_insights,
        "opportunities": opportunities_found,
        "vc_intelligence": vc_intelligence_result,
    }


async def approve_pending_knowledge() -> int:
    pending = await get_pending()
    from aria_core.knowledge.cognitive import approve_knowledge
    count = 0
    for item in pending:
        if await approve_knowledge(item.id):
            count += 1
    append_memory("curiosity", f"Approved {count} knowledge items into cognitive memory")
    return count


async def reject_pending_knowledge() -> int:
    import aiosqlite
    from aria_core.knowledge.cognitive import DB_PATH, _ensure_table

    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("DELETE FROM cognitive_knowledge WHERE approved = 0")
        await db.commit()
        count = cursor.rowcount
    append_memory("curiosity", f"Rejected {count} pending knowledge items")
    return count