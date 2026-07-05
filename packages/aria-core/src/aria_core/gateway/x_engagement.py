"""X mentions — auto-reply only by default; optional learn-to-memory (X_MENTIONS_LEARN_ENABLED)."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from aria_core.gateway.x_twitter import (
    _oauth1_auth,
    _verify_me_sync,
    is_x_post_configured,
    is_x_read_configured,
    reply_to_tweet,
)
from aria_core.memory import append_memory
from aria_core.paths import memory_dir
from aria_core.runtime import settings

logger = logging.getLogger(__name__)

X_API_BASE = "https://api.twitter.com/2"
LEDGER_PATH = memory_dir() / "x_mentions_ledger.json"
MAX_MENTIONS_PER_CYCLE = 15
MAX_LIKES_PER_CYCLE = 15
MAX_REPLIES_PER_CYCLE = 5


def _load_ledger() -> dict[str, Any]:
    if not LEDGER_PATH.exists():
        return {"processed_ids": [], "since_id": None, "likes": [], "replies": []}
    try:
        data = json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
        data.setdefault("likes", [])
        data.setdefault("replies", [])
        return data
    except Exception:
        return {"processed_ids": [], "since_id": None, "likes": [], "replies": []}


def _save_ledger(data: dict[str, Any]) -> None:
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    processed = data.get("processed_ids") or []
    if len(processed) > 500:
        data["processed_ids"] = processed[-500:]
    LEDGER_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _fetch_mentions_sync(
    user_id: str, since_id: str | None,
) -> tuple[list[dict[str, Any]], str | None, str | None]:
    import requests

    params: dict[str, Any] = {
        "max_results": min(MAX_MENTIONS_PER_CYCLE, 100),
        "tweet.fields": "author_id,created_at,conversation_id,in_reply_to_user_id",
        "expansions": "author_id",
        "user.fields": "username",
    }
    if since_id:
        params["since_id"] = since_id

    auth = _oauth1_auth()
    response = requests.get(
        f"{X_API_BASE}/users/{user_id}/mentions",
        auth=auth,
        params=params,
        timeout=30,
    )
    data = response.json() if response.content else {}
    if response.status_code != 200:
        err = data.get("detail") or data.get("title") or response.text[:300]
        raise RuntimeError(f"X mentions {response.status_code}: {err}")

    users = {u["id"]: u for u in data.get("includes", {}).get("users", [])}
    items: list[dict[str, Any]] = []
    for tweet in data.get("data") or []:
        author_id = tweet.get("author_id", "")
        username = users.get(author_id, {}).get("username", "?")
        items.append({
            "tweet_id": tweet.get("id"),
            "text": tweet.get("text", ""),
            "author_id": author_id,
            "username": username,
            "created_at": tweet.get("created_at"),
            "in_reply_to_user_id": tweet.get("in_reply_to_user_id"),
        })
    meta = data.get("meta") or {}
    return items, meta.get("newest_id"), meta.get("oldest_id")


def _like_tweet_sync(user_id: str, tweet_id: str) -> bool:
    import requests

    from aria_core import outgoing_pause
    from aria_core.x_publication_policy import (
        check_engagement_allowed,
        record_engagement,
    )

    if outgoing_pause.is_paused():
        logger.info("Like bloqué — ARIA en pause (%s)", outgoing_pause.since_label())
        return False

    allowed, reason, cost = check_engagement_allowed("like")
    if not allowed:
        logger.info("Like skipped: %s", reason)
        return False

    auth = _oauth1_auth()
    response = requests.post(
        f"{X_API_BASE}/users/{user_id}/likes",
        auth=auth,
        json={"tweet_id": tweet_id},
        timeout=30,
    )
    data = response.json() if response.content else {}
    if response.status_code not in (200, 201):
        err = data.get("detail") or data.get("title") or response.text[:300]
        if "already liked" in str(err).lower() or response.status_code == 409:
            return True
        raise RuntimeError(f"X like {response.status_code}: {err}")
    record_engagement("like", target=tweet_id, cost_usd=cost)
    return True


async def compose_mention_reply(username: str, mention_text: str) -> str | None:
    """Draft an English reply to an @Aria_ZHC mention (LLM + x_voice policy)."""
    from aria_core.identity import x_identity_prompt
    from aria_core.llm import chat_with_context, is_llm_configured
    from aria_core.x_publication_policy import check_tweet_content, policy_rules_for_llm
    from aria_core.x_voice import human_voice_rules_for_llm, humanize_tweet_for_x

    if not is_llm_configured():
        logger.info("Mention reply skipped — LLM not configured")
        return None

    system = (
        "You reply on X as @Aria_ZHC to a community mention.\n"
        f"{policy_rules_for_llm('en')}\n"
        f"{human_voice_rules_for_llm('en')}\n"
        f"{x_identity_prompt()}\n"
        "Write ONE reply tweet (max 280 chars). Direct, on-topic, helpful.\n"
        "Do not start with @username — X threads handle that.\n"
        "Verified facts only; if unsure, say what you are exploring next."
    )
    user = f"@{username} wrote:\n{mention_text.strip()}\n\nYour reply:"
    raw = await chat_with_context(user, system, max_tokens=160, temperature=0.6)
    if not raw:
        return None

    body = await humanize_tweet_for_x(raw.strip())
    ok, reason = check_tweet_content(body)
    if not ok:
        logger.warning("Mention reply blocked by policy: %s", reason)
        return None
    return body[:280]


async def run_mentions_learn_cycle() -> dict[str, Any]:
    """
    Poll @Aria_ZHC mentions and reply when X_ALLOW_REPLIES.
    Memory/insight store only if X_MENTIONS_LEARN_ENABLED — never like unless X_ALLOW_LIKES.
    """
    import asyncio

    if not is_x_post_configured():
        return {
            "status": "disabled",
            "reason": "OAuth X requis pour lire les mentions",
            "processed": 0,
        }

    ledger = _load_ledger()
    processed_set = set(ledger.get("processed_ids") or [])
    replied_set = {
        str(r.get("in_reply_to")) for r in ledger.get("replies") or [] if r.get("in_reply_to")
    }
    since_id = ledger.get("since_id")

    try:
        me = await asyncio.to_thread(_verify_me_sync)
        user_id = me.get("id")
        aria_username = (me.get("username") or "").lower()
        if not user_id:
            return {"status": "error", "reason": "user id introuvable", "processed": 0}

        mentions, newest_id, _oldest = await asyncio.to_thread(
            _fetch_mentions_sync, user_id, since_id,
        )
    except Exception as exc:
        logger.warning("Mentions fetch failed: %s", exc)
        return {"status": "error", "reason": str(exc)[:200], "processed": 0}

    if not mentions:
        if newest_id:
            ledger["since_id"] = newest_id
            _save_ledger(ledger)
        return {"status": "ok", "processed": 0, "liked": 0, "replied": 0}

    mentions.sort(key=lambda m: m.get("tweet_id") or "")

    from aria_core.gateway.telegram_bot import notify_admin

    learn_enabled = bool(getattr(settings, "x_mentions_learn_enabled", False))
    new_count = 0
    liked_count = 0
    replied_count = 0
    skipped_count = 0
    previews: list[str] = []
    reply_previews: list[str] = []

    for item in mentions:
        tweet_id = str(item.get("tweet_id") or "")
        if not tweet_id or tweet_id in processed_set:
            continue

        username = (item.get("username") or "?").lower()
        if username == aria_username:
            processed_set.add(tweet_id)
            continue

        text = (item.get("text") or "").strip()
        if not text:
            processed_set.add(tweet_id)
            continue

        if learn_enabled:
            from aria_core.knowledge.cognitive import add_knowledge, approve_knowledge
            from aria_core.knowledge.x_insight_relevance import (
                assess_x_insight_for_memory,
                format_assessment_log,
            )

            assessment = await assess_x_insight_for_memory(text, source="x_mention")
            if assessment.store:
                knowledge = await add_knowledge(
                    source="x_mention",
                    topic=f"@{item.get('username', '?')}",
                    content=text[:500],
                    confidence=assessment.confidence,
                    approved=False,
                )
                if settings.aria_autonomous:
                    await approve_knowledge(knowledge.id)
                new_count += 1
                previews.append(
                    f"@{item.get('username')}: {text[:80]} "
                    f"[{format_assessment_log(assessment)}]"
                )
            else:
                skipped_count += 1
                append_memory(
                    "curiosity",
                    f"X mention rejetée Groq ({format_assessment_log(assessment)}): "
                    f"@{item.get('username')} {text[:50]}",
                )

        if (
            settings.x_allow_replies
            and replied_count < MAX_REPLIES_PER_CYCLE
            and tweet_id not in replied_set
        ):
            try:
                draft = await compose_mention_reply(item.get("username") or "?", text)
                if draft:
                    reply_id, note = await reply_to_tweet(
                        draft,
                        in_reply_to_tweet_id=tweet_id,
                    )
                    if reply_id:
                        replied_count += 1
                        replied_set.add(tweet_id)
                        ledger.setdefault("replies", []).append({
                            "at": datetime.now(timezone.utc).isoformat(),
                            "in_reply_to": tweet_id,
                            "reply_id": reply_id,
                            "author": item.get("username"),
                            "preview": draft[:120],
                        })
                        reply_previews.append(
                            f"→ @{item.get('username')}: {draft[:100]}"
                        )
                    else:
                        logger.info("Reply skipped for %s: %s", tweet_id, note)
            except Exception as exc:
                logger.warning("Reply failed for %s: %s", tweet_id, exc)

        if settings.x_allow_likes and liked_count < MAX_LIKES_PER_CYCLE:
            try:
                ok = await asyncio.to_thread(_like_tweet_sync, user_id, tweet_id)
                if ok:
                    liked_count += 1
                    ledger.setdefault("likes", []).append({
                        "at": datetime.now(timezone.utc).isoformat(),
                        "tweet_id": tweet_id,
                        "author": item.get("username"),
                    })
            except Exception as exc:
                logger.warning("Like failed for %s: %s", tweet_id, exc)

        processed_set.add(tweet_id)

    if newest_id:
        ledger["since_id"] = newest_id
    ledger["processed_ids"] = list(processed_set)
    _save_ledger(ledger)

    if replied_count > 0 or (learn_enabled and new_count > 0):
        if learn_enabled:
            append_memory(
                "curiosity",
                f"X mentions: {new_count} learned, {replied_count} replied, {liked_count} liked",
            )
        if settings.aria_autonomous and (previews or reply_previews):
            parts = []
            if replied_count:
                parts.append(f"{replied_count} reply(s) posted")
            if new_count:
                parts.append(f"{new_count} insight(s) in memory")
            if liked_count:
                parts.append(f"{liked_count} like(s)")
            preview_text = "\n".join(
                f"• {p}" for p in (reply_previews + previews)[:6]
            )
            await notify_admin(
                "🤖 ARIA ZHC — mentions X\n\n"
                f"{' · '.join(parts)}\n\n"
                f"{preview_text}"
            )

    return {
        "status": "ok",
        "processed": new_count,
        "skipped": skipped_count,
        "liked": liked_count,
        "replied": replied_count,
        "mentions_fetched": len(mentions),
    }


def mentions_reply_enabled() -> bool:
    """Heartbeat reply cycle — X_ALLOW_REPLIES, no memory store required."""
    if not settings.x_allow_replies:
        return False
    return is_x_post_configured() and is_x_read_configured()


def mentions_learn_enabled() -> bool:
    """Opt-in store mentions in cognitive memory (Groq triage)."""
    if not getattr(settings, "x_mentions_learn_enabled", False):
        return False
    return is_x_post_configured() and is_x_read_configured()