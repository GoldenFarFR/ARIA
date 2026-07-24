"""Operator/ARIA conversation miner (Telegram) -- rereads exchanges already
logged by `relay_chat.py` (the existing Telegram channel, not a new one) and
PROPOSES (never imposes) a durable lesson observed in the real dialogue.
Same strict doctrine as `knowledge_inbox.py`/`claude_mentor.py`: GitHub ISSUE
only, never an autonomous commit or merge.

Guardrail specific to this module (beyond the common doctrine): the source
is a PRIVATE conversation (may contain an IP/password/key -- experienced
in real conditions this same night) and the destination is a PUBLIC GITHUB
ISSUE. Creating an issue does NOT go through the CI's `detect-secrets` scan
(which only covers pushes) -- `_looks_like_secret` is the only safety net here and
blocks publication (never a partial send) at the slightest doubt rather than
letting a secret fragment leak into the public repo.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone

TARGET_REPO = "ARIA"
MIN_INTERVAL_HOURS = 20.0  # same cadence as claude_mentor -- in-depth review, not a continuous chat
_MIN_NEW_MESSAGES = 6  # at least a few exchanges for a pattern to be credible
_MAX_MESSAGES_PER_RUN = 120

_MINER_SYSTEM = (
    "You are Claude Code, external reviewer of the Telegram exchanges between the "
    "operator (GoldenFarFR) and ARIA. You're shown a REAL excerpt of their "
    "conversation. Look for a DURABLE and GENERALIZABLE lesson (a repeated operator "
    "preference, a correction ARIA should retain, a recurring confusion to avoid) -- "
    "never a one-off anecdote. ABSOLUTE RULE: NEVER copy a verbatim fragment from the "
    "conversation into your proposal (IP address, password, API key, credential, "
    "private domain name, or any text that resembles one) -- describe the lesson in "
    "abstract language only, never a direct quote. If nothing solid emerges, say so "
    "honestly (durable=false) rather than inventing a pattern. Answer STRICTLY in "
    "JSON: "
    '{"durable": true|false, "proposal_title": "<short title if durable, else empty>", '
    '"proposal_body": "<structured proposal in markdown, in abstract language, without '
    'any direct quote -- else empty>"}'
)

# Local safety net before any publication -- independent from the CI scan (which only
# covers git push, not API calls like creating an issue).
_SECRET_PATTERNS = [
    re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"),  # IP
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bCG-[A-Za-z0-9]{15,}\b"),  # CoinGecko demo key
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),  # GitHub token
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),  # Slack token
    re.compile(r"\b[A-Za-z0-9+/]{40,}={0,2}\b"),  # generic long base64 blob
    re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),  # OpenAI/Anthropic-style API key
]


def _looks_like_secret(text: str) -> bool:
    return any(p.search(text) for p in _SECRET_PATTERNS)


def telegram_miner_enabled() -> bool:
    from aria_core import relay_chat
    from aria_core.skills.github_skill import github_configured

    if not relay_chat.relay_enabled() or not github_configured():
        return False
    return os.environ.get("ARIA_TELEGRAM_MINER_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _ensure_state_table(db) -> None:
    await db.execute(
        "CREATE TABLE IF NOT EXISTS telegram_miner_state ("
        "id INTEGER PRIMARY KEY CHECK (id = 1), last_mined_id INTEGER NOT NULL, "
        "last_run_at TEXT)"
    )
    await db.execute(
        "INSERT OR IGNORE INTO telegram_miner_state (id, last_mined_id, last_run_at) "
        "VALUES (1, 0, NULL)"
    )
    await db.commit()


async def _load_state() -> tuple[int, float | None]:
    import aiosqlite

    from aria_core.paths import aria_db_path

    async with aiosqlite.connect(str(aria_db_path())) as db:
        await _ensure_state_table(db)
        cursor = await db.execute(
            "SELECT last_mined_id, last_run_at FROM telegram_miner_state WHERE id = 1"
        )
        row = await cursor.fetchone()
    last_mined_id = row[0] if row else 0
    hours_since: float | None = None
    if row and row[1]:
        last_run = datetime.fromisoformat(row[1])
        hours_since = (datetime.now(timezone.utc) - last_run).total_seconds() / 3600.0
    return last_mined_id, hours_since


async def _save_state(last_mined_id: int) -> None:
    import aiosqlite

    from aria_core.paths import aria_db_path

    async with aiosqlite.connect(str(aria_db_path())) as db:
        await _ensure_state_table(db)
        await db.execute(
            "UPDATE telegram_miner_state SET last_mined_id = ?, last_run_at = ? WHERE id = 1",
            (last_mined_id, _now()),
        )
        await db.commit()


def _format_transcript(messages: list[dict]) -> str:
    lines = []
    for m in messages:
        sender = "Operator" if m["sender"] == "operator" else "ARIA"
        lines.append(f"{sender}: {m['content'][:500]}")
    return "\n".join(lines)


async def _propose_durable_insight(title: str, body: str, *, github_client=None) -> dict:
    """Publishes the issue -- ONLY if neither the title nor the body look like a secret."""
    if _looks_like_secret(title) or _looks_like_secret(body):
        return {"outcome": "blocked_suspected_secret"}

    from aria_core.runtime import settings

    if github_client is None:
        token = (settings.github_token or "").strip()
        if not token:
            return {"outcome": "no_token"}
        from aria_core.github_client import GitHubClient

        github_client = GitHubClient(token)

    owner = settings.github_owner
    body_full = (
        body
        + "\n\n---\n*Proposal generated by Claude from a pattern observed in the "
        "operator/ARIA exchanges -- never a direct quote, human review required before "
        "any integration into the knowledge files. Never an autonomous commit or "
        "merge.*"
    )
    try:
        issue = await github_client.create_issue(
            owner, TARGET_REPO, f"[knowledge] {title}", body_full,
            labels=["aria-knowledge-proposal"],
        )
    except Exception as exc:  # noqa: BLE001 -- a GitHub outage must never break the cycle
        return {"outcome": "error", "error": str(exc)[:300]}
    return {"outcome": "ok", "issue_url": issue.get("html_url")}


async def run_telegram_miner_cycle(*, llm=None, github_client=None) -> dict:
    """One pass: rereads new operator/ARIA exchanges since the last run,
    proposes a durable lesson if a solid pattern emerges. Fail-closed at every
    stage, internal throttle (~1x/day)."""
    if not telegram_miner_enabled():
        return {"outcome": "skipped_disabled"}

    from aria_core import outgoing_pause

    if outgoing_pause.is_paused():
        return {"outcome": "skipped_paused"}

    last_mined_id, hours_since = await _load_state()
    if hours_since is not None and hours_since < MIN_INTERVAL_HOURS:
        return {"outcome": "throttled", "hours_since_last": round(hours_since, 1)}

    from aria_core import relay_chat

    all_new = await relay_chat.recent_messages(since_id=last_mined_id, limit=_MAX_MESSAGES_PER_RUN)
    if not all_new:
        return {"outcome": "nothing_new"}

    highest_id = max(m["id"] for m in all_new)
    exchanges = [m for m in all_new if m["sender"] in ("operator", "aria")]

    if len(exchanges) < _MIN_NEW_MESSAGES:
        await _save_state(highest_id)
        return {"outcome": "insufficient_signal", "new_messages": len(exchanges)}

    if llm is None:
        from aria_core.llm import chat_with_context as llm

    transcript = _format_transcript(exchanges)
    prompt = f"Conversation excerpt:\n\n{transcript}\n\nLook for a durable lesson."
    raw = await llm(prompt, _MINER_SYSTEM, max_tokens=700)
    if not raw:
        return {"outcome": "llm_unavailable"}

    try:
        data = json.loads(raw)
        durable = bool(data.get("durable", False))
        title = str(data.get("proposal_title", "")).strip()
        body = str(data.get("proposal_body", "")).strip()
    except (json.JSONDecodeError, TypeError, ValueError, AttributeError):
        await _save_state(highest_id)
        return {"outcome": "parse_failed"}

    await _save_state(highest_id)

    if not durable or not title or not body:
        return {"outcome": "not_durable"}

    result = await _propose_durable_insight(title, body, github_client=github_client)
    return {**result, "title": title}
