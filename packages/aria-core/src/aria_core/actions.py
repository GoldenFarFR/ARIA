from __future__ import annotations

import json
from urllib.parse import quote

from aria_core.exchanges import AgentExchange, ExchangeStatus, create_exchange, update_status
from aria_core.narrative import x_juno_greeting, x_juno_hashtags
from aria_core.skills.repertoire_skill import get_repertoire_summary
from aria_core.skills.zhc_bridge import build_intro_message


def build_juno_public_message(repertoire_summary: str) -> str:
    msg = build_intro_message(repertoire_summary)
    greeting = msg.payload.get("greeting", "")
    proposal = msg.payload.get("proposal", "")
    caps = ", ".join(msg.payload.get("capabilities", []))
    return (
        f"{x_juno_greeting()}\n\n"
        f"{greeting}\n\n"
        f"Capabilities: {caps}\n"
        f"Repertoire: {repertoire_summary}\n\n"
        f"Proposal: {proposal}\n\n"
        f"{x_juno_hashtags()}"
    )


def x_publish_url(message: str) -> str:
    return f"https://twitter.com/intent/tweet?text={quote(message)}"


async def execute_contact_juno(approval_id: str | None = None) -> tuple[AgentExchange, str]:
    """
    Prepares and logs a JUNO message. In autonomous mode, also attempts to publish on X.
    """
    from aria_core.runtime import settings

    autonomous = approval_id == "autonomous" or settings.aria_autonomous
    rep_summary = await get_repertoire_summary(lang="en")
    public_text = build_juno_public_message(rep_summary)
    intro = build_intro_message(rep_summary)

    exchange = await create_exchange(
        target_agent="JUNO@ZHC",
        channel="x_api" if autonomous else "x_telegram_manual",
        message_body=public_text,
        message_json=intro.model_dump(),
        approval_id=approval_id,
        status=ExchangeStatus.APPROVED,
    )

    post_note = ""
    if autonomous:
        from aria_core.gateway.x_twitter import post_tweet

        _, post_note = await post_tweet(public_text[:280], approval_id or "autonomous")

    x_url = x_publish_url(public_text[:280])

    if autonomous:
        instructions = (
            f"Exchange #{exchange.id} — autonomous ZHC initiative\n\n"
            f"{post_note}\n\n"
            f"Prepared message:\n{public_text[:500]}...\n\n"
            f"X link if needed: {x_url}\n"
            f"JUNO community: https://t.me/JUNOCOINBASE\n"
            f"Tracked in: ARIA memory journal"
        )
    else:
        instructions = (
            f"✅ Approved — exchange #{exchange.id} logged\n\n"
            f"━━━ COPY THIS MESSAGE ━━━\n"
            f"{public_text}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Option A — Post on X (one click):\n{x_url}\n\n"
            f"Option B — Telegram JUNO community:\nhttps://t.me/JUNOCOINBASE\n\n"
            f"After posting, reply:\n"
            f"published {exchange.id}\n\n"
            f"Track status in ARIA memory journal"
        )

    return exchange, instructions


async def mark_published(exchange_id: str) -> AgentExchange | None:
    return await update_status(
        exchange_id,
        ExchangeStatus.AWAITING_REPLY,
        notes="Admin confirmed message was published",
    )


def format_exchanges_list(exchanges: list[AgentExchange]) -> str:
    if not exchanges:
        return "No agent exchanges yet."

    lines = ["Agent exchanges log:", ""]
    for ex in exchanges[:10]:
        lines.append(f"#{ex.id} → {ex.target_agent}")
        lines.append(f"  Status: {ex.status.value}")
        lines.append(f"  Channel: {ex.channel}")
        if ex.published_at:
            lines.append(f"  Published: {ex.published_at.strftime('%Y-%m-%d %H:%M UTC')}")
        if ex.reply_body:
            lines.append(f"  Reply: {ex.reply_body[:80]}...")
        lines.append("")
    lines.append("Commands:")
    lines.append("- published <id> — mark as posted")
    lines.append("- reply <id> <text> — log JUNO's response")
    return "\n".join(lines)