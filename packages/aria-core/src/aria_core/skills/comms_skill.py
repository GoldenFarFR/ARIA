"""Marketing & communication skill — drafts site copy, posts, updates."""

from __future__ import annotations

import re

from aria_core.content.content_db import save_draft
from aria_core.content.site_copy import public_site_payload
from aria_core.holding import holding_name
from aria_core.memory import append_memory
from aria_core.narrative import one_liner
from aria_core.skills.repertoire_skill import get_repertoire_summary

# Only treat as explicit tweet body after a comms command — not any ":" in French prose.
_EXPLICIT_X_POST = re.compile(
    r"(?:^|\b)(?:publie sur x|poste sur x|post on x|tweet sur x|tweet)\s*:\s*(.+)$",
    re.IGNORECASE | re.DOTALL,
)

_TAG_WATCHLIST = re.compile(
    r"tag(?:ue|ger)?|mentions?|handles?|watchlist|@(?:golden|solvr|grok|aixbt)",
    re.IGNORECASE,
)


def _wants_x_channel(lower: str) -> bool:
    return any(
        w in lower
        for w in (
            "tweet",
            "x post",
            "twitter",
            "social",
            "poste sur x",
            "publie sur x",
            "publie un tweet",
            "post on x",
        )
    )


_PROPOSAL_CONTEXT = re.compile(
    r"\b(propos|brouillon|draft|sugg[eè]re|rédig|redig|écris|ecris)\b|"
    r"(?:un\s+)?tweet\s+(?:à|a)\s+publier\b|"
    r"publier\s+qui\b",
    re.IGNORECASE,
)

_IMPERATIVE_PUBLISH = re.compile(
    r"(?:^|\b)(?:publie\s+(?:maintenant\s+)?(?:sur\s+)?x|poste\s+(?:sur\s+)?x|"
    r"publish\s+(?:now\s+)?(?:on\s+)?x|/x\s+post)\b",
    re.IGNORECASE,
)


def _wants_immediate_x_publish(message: str) -> bool:
    """
    Publication API uniquement sur ordre explicite — pas sur « propose un tweet à publier ».
    """
    lower = message.lower().strip()
    if _PROPOSAL_CONTEXT.search(lower):
        return False
    if extract_explicit_x_post_body(message):
        return True
    if _IMPERATIVE_PUBLISH.search(lower):
        return True
    if re.match(r"^publie\b", lower):
        return True
    return False


def extract_explicit_x_post_body(message: str) -> str | None:
    """Return operator-supplied tweet text only after `publie sur x: …`."""
    match = _EXPLICIT_X_POST.search(message.strip())
    if not match:
        return None
    body = match.group(1).strip()
    return body if body else None


def _should_tag_watchlist(message: str) -> bool:
    return bool(_TAG_WATCHLIST.search(message))


def _watchlist_mentions() -> str:
    from aria_core.handle_registry import mentions_for_pack

    return mentions_for_pack()


def _fallback_x_tweet(user_message: str, *, tag_watchlist: bool) -> str:
    tags = f" {_watchlist_mentions()}" if tag_watchlist else ""
    lower = user_message.lower()
    if any(w in lower for w in ("apprend", "learn", "curiosit", "first information")):
        return (
            "Learning sprint #1 — what should ARIA study next: on-chain signals, "
            f"agent ops, or market structure? Reply with one topic.{tags} "
            "Education only — not financial advice."
        )[:280]
    return (
        f"{one_liner('en')} Portfolio pulse — building in public with verified facts only.{tags}"
    )[:280]


async def compose_x_tweet(user_message: str, lang: str = "en") -> str:
    """Draft tweet from operator intent (LLM when available, else template)."""
    from aria_core.llm import chat_with_context, is_llm_configured

    tag_watchlist = _should_tag_watchlist(user_message)
    mentions = _watchlist_mentions() if tag_watchlist else ""
    lang_hint = "Réponds en français si l'opérateur a écrit en français." if lang == "fr" else ""

    if is_llm_configured():
        from aria_core.x_publication_policy import policy_rules_for_llm

        from aria_core.tweet_compose_workflow import _gather_compose_context

        context = await _gather_compose_context()
        from aria_core.x_voice import human_voice_rules_for_llm, humanize_tweet_for_x

        system = (
            "You post for @Aria_ZHC (Aria Vanguard ZHC holding).\n"
            "Write ONE English X tweet (max 280 chars).\n"
            "NEVER mention DEXPulse — retired 2026-06-19. Active product: Aria Market.\n"
            f"{policy_rules_for_llm('en')}\n"
            f"{human_voice_rules_for_llm('en')}\n"
            "Natural human voice — not an AI character, not a feature comma-list.\n"
            "Do not copy the operator prompt verbatim. Vary from recent posts in context.\n"
            f"{f'- Include these @ at the end: {mentions}' if mentions else ''}\n"
            "Tweet text only — no quotes."
        )
        composed = await chat_with_context(
            f"{user_message}\n\nContexte mémoire :\n{context}",
            system,
            temperature=0.75,
            max_tokens=140,
        )
        if composed and composed.strip():
            from aria_core.handle_registry import resolve_handles_in_text

            text = resolve_handles_in_text(composed.strip().strip('"').strip("'"))
            if tag_watchlist and "+veille" not in user_message.lower():
                text = resolve_handles_in_text(f"{text} +veille")
            if len(text) <= 280:
                text = await humanize_tweet_for_x(text)
                return text
            text = await humanize_tweet_for_x(text[:280])
            return text

    return _fallback_x_tweet(user_message, tag_watchlist=tag_watchlist)


def _wants_faq_draft(message: str, lower: str) -> bool:
    from aria_core.tweet_compose_workflow import wants_role_coaching

    if wants_role_coaching(message):
        return False
    if re.search(r"(?:tu\s+as|as[- ]tu|vous\s+avez).{0,40}questions?", lower):
        return False
    if re.search(r"concernant\s+ton\s+(?:travail|identit|rôle)", lower):
        return False
    return any(w in lower for w in ("faq", "question", "answer"))


async def execute_comms_draft(user_message: str, lang: str = "en") -> tuple[str, dict]:
    """Produce a comms/marketing draft and store it for review."""
    from aria_core.tweet_compose_workflow import start_role_coaching_workflow, wants_role_coaching

    if wants_role_coaching(user_message):
        out = await start_role_coaching_workflow(user_message)
        return out, {"role_coaching": True, "channel": "compose"}

    site = public_site_payload()
    rep_summary = await get_repertoire_summary(lang)
    h = holding_name()
    lower = user_message.lower()

    if _wants_x_channel(lower):
        kind, channel = "social", "x"
        title = f"{h} — milestone update"
        from aria_core.gateway.x_twitter import is_x_post_configured, post_tweet

        from aria_core.tweet_compose_workflow import extract_operator_supplied_tweet

        explicit = extract_explicit_x_post_body(user_message)
        supplied = extract_operator_supplied_tweet(user_message)
        if explicit:
            tweet_text = explicit
        elif supplied:
            tweet_text = supplied
        else:
            tweet_text = await compose_x_tweet(user_message, lang)

        wants_publish = _wants_immediate_x_publish(user_message) or (
            supplied
            and re.search(r"valide|validé|approved|publie|publish", user_message, re.I)
        )

        if wants_publish and is_x_post_configured():
            _, post_note = await post_tweet(tweet_text[:280], approval_id="comms_skill")
            posted = "x.com/" in post_note.lower() and "/status/" in post_note.lower()
            append_memory("comms", f"[x_post] posted={posted} {tweet_text[:80]}")
            title = "Publication X" if posted else ("Publication X — brouillon" if lang == "fr" else "X — draft")
            draft_line = (
                f"Rédaction :\n{tweet_text}\n\n"
                if lang == "fr"
                else f"Draft:\n{tweet_text}\n\n"
            )
            return f"{title}\n\n{draft_line}{post_note}", {
                "channel": "x",
                "posted": posted,
                "draft_only": not posted,
                "tweet_text": tweet_text,
                "composed": explicit is None and supplied is None,
            }

        append_memory("comms", f"[x_draft] {tweet_text[:80]}")
        if lang == "fr":
            hint = (
                "Pour le parcours complet (validation + horaire) : /x compose\n"
                "Publication immédiate explicite : publie sur x: <texte>"
            )
            output = (
                f"**Brouillon X** (non publié)\n\n"
                f"Rédaction :\n{tweet_text}\n\n"
                f"_{hint}_"
            )
        else:
            hint = "Full workflow: /x compose · Immediate post: publish on x: <text>"
            output = (
                f"**X draft** (not published)\n\n"
                f"Draft:\n{tweet_text}\n\n"
                f"_{hint}_"
            )
        return output, {
            "channel": "x",
            "posted": False,
            "draft_only": True,
            "tweet_text": tweet_text,
            "composed": explicit is None and supplied is None,
        }
    elif _wants_faq_draft(user_message, lower):
        kind, channel = "faq", "site"
        title = "FAQ draft from visitor question"
        body = (
            f"Q: {user_message[:200]}\n\n"
            f"A: [{h}] DEXPulse is the flagship subsidiary. "
            f"{site['governance_rule']}"
        )
    elif any(w in lower for w in ("newsletter", "update", "weekly", "announce")):
        kind, channel = "update", "site"
        title = f"{h} weekly update"
        body = f"Holding update\n\n{rep_summary}\n\nNext: product + content pulse."
    else:
        kind, channel = "marketing", "site"
        title = f"{h} — public message"
        body = (
            f"{site['aria_role']}\n\n"
            f"{one_liner('en')}\n\n"
            f"Request: {user_message[:400]}"
        )

    draft = await save_draft(kind, title, body, channel=channel, tags="auto")
    append_memory("comms", f"[{kind}] {title[:80]}")

    if lang == "fr":
        output = (
            f"**Brouillon {kind}** enregistré ({channel}).\n\n"
            f"**Titre:** {title}\n\n{body}\n\n"
            f"_ID: {draft['id'][:8]}… — ARIA gère build, marketing, comms et FAQ._"
        )
    else:
        output = (
            f"**{kind.title()} draft** saved ({channel}).\n\n"
            f"**Title:** {title}\n\n{body}\n\n"
            f"_ID: {draft['id'][:8]}… — ARIA runs build, marketing, comms, and FAQ._"
        )
    return output, {"draft_id": draft["id"], "kind": kind, "channel": channel}