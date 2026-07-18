"""Relais de conversation à 3 (opérateur, ARIA, Claude Code) — réutilise le canal Telegram
privé EXISTANT de l'opérateur avec ARIA (décision opérateur 08/07, après avoir écarté un
second bot/groupe jugé superflu). Claude Code lit l'historique récent à chaque réveil
programmé et répond à travers le bot ARIA existant, préfixé pour rester distinguable.

Accès dédié, minuscule : `ARIA_RELAY_ACCESS_TOKEN` (distinct du secret admin) — ne peut
QUE lire/poster dans ce relais, rien d'autre (pas de finance, pas de code, pas d'admin).
Fail-closed : sans ce token configuré, le relais entier est inerte.
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
    """Gate simple : sans token dédié configuré, le relais est inerte (rien n'est
    journalisé, rien n'est accessible)."""
    return bool(relay_access_token())


def relay_autoreply_enabled() -> bool:
    """Gate DISTINCT et plus fort que `relay_enabled()` — celui-ci autorise ARIA à
    répondre de façon autonome (envoi Telegram réel), donc off par défaut, opt-in séparé
    du token relay. Sans lui, le relais reste lecture/écriture pour Claude uniquement,
    ARIA ne répond jamais toute seule."""
    if not relay_enabled():
        return False
    return os.environ.get("ARIA_RELAY_AUTOREPLY_ENABLED", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def verify_relay_access(provided: str | None) -> bool:
    """Comparaison à temps constant — même politique que le secret admin
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
    """Journalise un message (operator/aria/claude). N'échoue jamais bruyamment — une
    panne de journalisation ne doit jamais casser l'envoi/la réception réelle côté Telegram."""
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
    except Exception:  # noqa: BLE001 — la journalisation du relais ne doit jamais remonter
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
    """Envoie un message à l'opérateur à travers le bot ARIA existant (préfixé), et le
    journalise. `sender` injectable (tests hors-ligne) ; par défaut
    `aria_core.gateway.telegram_bot.send_message`.

    18/07 -- trouvé par audit de sécurité : contrairement aux 20+ tâches heartbeat
    (couvertes centralement par `outgoing_pause.is_paused()` dans `heartbeat._tick`),
    ce chemin est atteint directement via `POST /api/aria/relay/reply` (token relay
    dédié, hors heartbeat) et ne vérifiait jamais le kill-switch -- un appel
    authentifié pouvait donc poster sur Telegram même pendant un `/stop`."""
    from aria_core import outgoing_pause

    if outgoing_pause.is_paused() or not relay_enabled() or not text.strip():
        return False
    if sender is None:
        from aria_core.gateway.telegram_bot import send_message as sender

    prefixed = f"{CLAUDE_PREFIX}{text.strip()}"
    try:
        await sender(prefixed)
    except Exception:  # noqa: BLE001 — un envoi rate ne doit jamais planter l'appelant
        return False
    await log_message("claude", text.strip())
    return True


async def send_aria_relay_reply(text: str, *, sender=None) -> bool:
    """Envoie une réponse d'ARIA ELLE-MÊME (pas Claude) dans le relay — sa vraie voix,
    aucun préfixe. Utilisé uniquement par `relay_conversation.run_relay_conversation_cycle`
    (gate `ARIA_RELAY_AUTOREPLY_ENABLED`), jamais appelable depuis la conversation
    opérateur normale."""
    if not relay_enabled() or not text.strip():
        return False
    if sender is None:
        from aria_core.gateway.telegram_bot import send_message as sender

    try:
        await sender(text.strip())
    except Exception:  # noqa: BLE001 — un envoi raté ne doit jamais planter l'appelant
        return False
    await log_message("aria", text.strip())
    return True
