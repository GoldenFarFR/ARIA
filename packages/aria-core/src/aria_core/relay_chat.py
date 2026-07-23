"""3-way conversation relay (operator, ARIA, Claude Code) — reuses the operator's
EXISTING private Telegram channel with ARIA (operator decision 08/07, after ruling out
a second bot/group deemed superfluous). Claude Code reads the recent history on each
scheduled wake-up and replies through the existing ARIA bot, prefixed to stay
distinguishable.

Small, dedicated access: `ARIA_RELAY_ACCESS_TOKEN` (distinct from the admin secret) —
can ONLY read/post in this relay, nothing else (no finance, no code, no admin).
Fail-closed: without this token configured, the whole relay is inert.
"""
from __future__ import annotations

import os
import secrets as _secrets
from datetime import datetime, timezone

import aiosqlite

from aria_core.paths import aria_db_path

DB_PATH = str(aria_db_path())

CLAUDE_PREFIX = "🤖 Claude — "


def relay_access_token() -> str:
    return (os.environ.get("ARIA_RELAY_ACCESS_TOKEN") or "").strip()


def relay_enabled() -> bool:
    """Simple gate: without a dedicated token configured, the relay is inert (nothing is
    logged, nothing is accessible)."""
    return bool(relay_access_token())


def relay_autoreply_enabled() -> bool:
    """Gate DISTINCT from and stronger than `relay_enabled()` — this one authorizes ARIA
    to reply autonomously (real Telegram send), so off by default, opt-in separate from
    the relay token. Without it, the relay stays read/write for Claude only,
    ARIA never replies on her own."""
    if not relay_enabled():
        return False
    return os.environ.get("ARIA_RELAY_AUTOREPLY_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def verify_relay_access(provided: str | None) -> bool:
    """Constant-time comparison — same policy as the admin secret
    (`public_mode.is_operator_request`)."""
    configured = relay_access_token()
    if not configured or not provided:
        return False
    return _secrets.compare_digest(provided.strip(), configured)


async def _ensure_table() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS relay_message (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sender TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        await db.commit()


async def log_message(sender: str, content: str) -> None:
    """Logs a message (operator/aria/claude). Never fails noisily — a logging
    failure must never break the actual send/receive on the Telegram side."""
    if not relay_enabled() or not content:
        return
    try:
        await _ensure_table()
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "INSERT INTO relay_message (sender, content, created_at) VALUES (?, ?, ?)",
                (sender, content[:4000], datetime.now(timezone.utc).isoformat()),
            )
            await db.commit()
    except Exception:  # noqa: BLE001 — relay logging must never bubble up
        pass


async def recent_messages(since_id: int = 0, limit: int = 50) -> list[dict]:
    await _ensure_table()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT id, sender, content, created_at FROM relay_message "
            "WHERE id > ? ORDER BY id ASC LIMIT ?",
            (since_id, limit),
        )
        rows = await cursor.fetchall()
    return [{"id": r[0], "sender": r[1], "content": r[2], "created_at": r[3]} for r in rows]


async def send_relay_reply(text: str, *, sender=None) -> bool:
    """Sends a message to the operator through the existing ARIA bot (prefixed), and
    logs it. `sender` injectable (offline tests); defaults to
    `aria_core.gateway.telegram_bot.send_message`.

    18/07 -- found via security audit: unlike the 20+ heartbeat tasks (covered
    centrally by `outgoing_pause.is_paused()` in `heartbeat._tick`), this path is
    reached directly via `POST /api/aria/relay/reply` (dedicated relay token,
    outside the heartbeat) and never checked the kill-switch -- an authenticated
    call could therefore post to Telegram even during a `/stop`."""
    from aria_core import outgoing_pause

    if outgoing_pause.is_paused() or not relay_enabled() or not text.strip():
        return False
    if sender is None:
        from aria_core.gateway.telegram_bot import send_message as sender

    prefixed = f"{CLAUDE_PREFIX}{text.strip()}"
    try:
        await sender(prefixed)
    except Exception:  # noqa: BLE001 — a failed send must never crash the caller
        return False
    await log_message("claude", text.strip())
    return True


async def send_aria_relay_reply(text: str, *, sender=None) -> bool:
    """Sends a reply from ARIA HERSELF (not Claude) in the relay — her real voice,
    no prefix. Used only by `relay_conversation.run_relay_conversation_cycle`
    (gate `ARIA_RELAY_AUTOREPLY_ENABLED`), never callable from the normal
    operator conversation."""
    if not relay_enabled() or not text.strip():
        return False
    if sender is None:
        from aria_core.gateway.telegram_bot import send_message as sender

    try:
        await sender(text.strip())
    except Exception:  # noqa: BLE001 — a failed send must never crash the caller
        return False
    await log_message("aria", text.strip())
    return True
