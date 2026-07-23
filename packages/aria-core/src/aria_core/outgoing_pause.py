"""Outgoing kill-switch — global pause of all of ARIA's actions toward the world.

Covers tweets, X replies/likes, ACP spending, and scheduled jobs (heartbeat).
The state is **persisted to disk** (`data_dir()/pause_state.json`) and
**re-read on every check**: it therefore survives a process restart — no
in-memory variable that would be lost on reboot.

This module NEVER freezes operator Telegram messaging (`send_message` /
`notify_admin`): the control channel must stay open to receive the `/stop`
confirmation, approval prompts, and to allow `/start`.

Behavior on unreadable/corrupted state ("the doubt"), **asymmetric and
deliberate**:
  - tweets / replies / likes / jobs → **fail-open** (``is_paused()``): ARIA
    keeps going. A damaged file must not brick her on its own.
  - spending / wallet_guard → **fail-closed** (``is_paused(strict=True)`` /
    ``money_block_reason()``): when in doubt, freeze the money.

A **missing** file is not a doubt: it's the clean "never paused" state -> everything
goes through (otherwise ARIA could never do anything). Only corruption triggers
the fail-closed on the money side. Corruption is always logged.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aria_core.paths import data_dir

logger = logging.getLogger(__name__)


def _state_path() -> Path:
    return data_dir() / "pause_state.json"


def _read_raw() -> dict[str, Any] | None:
    """Reads the raw state. Distinguishes three cases:
      - ``{}``   → file absent: clean "never paused" state (not a doubt).
      - ``dict`` → content read correctly.
      - ``None`` → file present but unreadable/corrupted: UNKNOWN state (the doubt).
    """
    path = _state_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        logger.warning("pause_state unreadable/corrupted (%s) — UNKNOWN state", exc)
        return None
    if not isinstance(raw, dict):
        logger.warning("pause_state has unexpected shape (%r) — UNKNOWN state", type(raw).__name__)
        return None
    return raw


def _write(payload: dict[str, Any]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write: tmp then replace, so a reader never sees a partial JSON.
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def is_paused(*, strict: bool = False) -> bool:
    """True if the kill-switch is armed. Re-reads disk on every call (survives a restart).

    On unreadable/corrupted state:
      - ``strict=False`` (default — tweets, replies, likes, jobs) → **fail-open**: False.
      - ``strict=True`` (spending / wallet_guard) → **fail-closed**: True (safety freeze).
    A missing file always returns False (clean state, not a doubt).
    """
    data = _read_raw()
    if data is None:
        if strict:
            logger.warning("Pause state unreadable — fail-closed (strict): freezing money for safety")
        return strict
    return bool(data.get("paused"))


def money_block_reason(action: str = "Cette dépense") -> str | None:
    """Money path (wallet_guard). ``None`` → spending allowed; otherwise a block message.

    **Fail-closed**: blocks if ARIA is paused OR if the state is
    unreadable/corrupted (the doubt favors safety). A missing file (never
    paused) lets it through.
    """
    data = _read_raw()
    if data is None:
        return (
            f"⛔ {action} est bloquée : l'état de pause est illisible/corrompu. "
            "Par sécurité, les dépenses sont gelées dans le doute (fail-closed).\n"
            "Répare/supprime pause_state.json — ou fais /stop puis /start — avant toute dépense."
        )
    if data.get("paused"):
        return blocked_notice(action)
    return None


def pause_status() -> dict[str, Any]:
    """Current state: {paused, since (datetime|None), by, reason, readable}.

    ``readable=False`` signals a corrupted file (spending frozen, tweets/jobs active).
    """
    raw = _read_raw()
    readable = raw is not None
    data = raw or {}
    since: datetime | None = None
    since_raw = data.get("since")
    if isinstance(since_raw, str):
        try:
            since = datetime.fromisoformat(since_raw.replace("Z", "+00:00"))
            if since.tzinfo is None:
                since = since.replace(tzinfo=timezone.utc)
        except ValueError:
            since = None
    return {
        "paused": bool(data.get("paused")),
        "since": since,
        "by": data.get("by"),
        "reason": data.get("reason") or "",
        "readable": readable,
    }


def pause(by: int | str | None = None, reason: str = "") -> dict[str, Any]:
    """Arms the kill-switch. All outgoing actions will be blocked until ``resume``."""
    _write(
        {
            "paused": True,
            "since": datetime.now(timezone.utc).isoformat(),
            "by": by,
            "reason": (reason or "").strip(),
        }
    )
    logger.warning("ARIA PAUSED (outgoing kill-switch armed) — by=%s reason=%s", by, reason)
    return pause_status()


def resume(by: int | str | None = None) -> dict[str, Any]:
    """Lifts the kill-switch. Outgoing actions resume."""
    _write(
        {
            "paused": False,
            "since": None,
            "by": by,
            "resumed_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    logger.warning("ARIA RESUMED (kill-switch lifted) — by=%s", by)
    return pause_status()


def since_label() -> str:
    """"depuis 14:32 UTC (il y a 1h07)" — reminds the operator how long this has been going on."""
    since = pause_status().get("since")
    if not isinstance(since, datetime):
        return "depuis un instant indéterminé"
    elapsed_min = int((datetime.now(timezone.utc) - since).total_seconds() // 60)
    if elapsed_min < 1:
        human = "à l'instant"
    elif elapsed_min < 60:
        human = f"il y a {elapsed_min} min"
    else:
        hours, mins = divmod(elapsed_min, 60)
        human = f"il y a {hours}h{mins:02d}"
    return f"depuis {since.strftime('%H:%M UTC')} ({human})"


def blocked_notice(action: str = "Cette action sortante") -> str:
    """Block message — reminds that the pause is active AND since when (operator's choice)."""
    return (
        f"⏸ {action} est bloquée : ARIA est en pause {since_label()}.\n"
        "Envoie /start (ou /resume) pour reprendre les actions sortantes."
    )
