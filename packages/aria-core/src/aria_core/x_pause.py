"""X (Twitter) interaction kill-switch -- ``/offx`` and ``/onx`` on Telegram.

Operator request (24/08): no independent lever existed for X -- silencing
tweets/likes/profile-sync meant arming ``/stop`` (``outgoing_pause``),
which also blocks real capital, or the operator had no way to touch X
alone at all.

Four independent pauses now exist, none of which ever touch each other's
state file:
- ``outgoing_pause`` (``/stop``/``/resume``, also aliased ``/offreal``/
  ``/onreal``) -- real capital ONLY, unchanged historical scope,
  "kill-switch, tested -- do not recode" per CLAUDE.md.
- ``paper_pause`` (``/offpaper``/``/onpaper``) -- paper-trading scanning
  only, fails OPEN (guards zero capital).
- this module (``/offx``/``/onx``) -- X interactions only.
- ``shadow_pause`` (``/offshadow``/``/onshadow``) -- the standalone shadow
  process only.

Every X-writing call site checks ONLY this module, never ``outgoing_pause``
directly. ``/off`` (the grouped shortcut, see ``telegram_bot._handle_off``)
and the automatic macro circuit breaker in ``risk_guard.py`` both arm this
module alongside the other three, so a full stop still blocks X -- but
``/onx`` can genuinely lift X on its own afterward, even if ``/stop``/
``/off`` stay armed for real capital.

Fails CLOSED on an unreadable/corrupted state, same doctrine as
``custody_pause.py`` (not ``paper_pause.py``'s fail-open): a tweet/like/
profile-sync posted publicly by mistake is a real, irreversible, visible
action -- unlike a skipped paper-trading scan -- so "unknown" must default
to "blocked", not "keep posting".

Structurally a near-exact copy of ``custody_pause.py`` (own JSON state
file, same read/write/fail-closed semantics) -- deliberately NOT the same
file as ``outgoing_pause.py`` (flagged in CLAUDE.md as "kill-switch, tested
-- do not recode").
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
    return data_dir() / "x_pause_state.json"


def _read_raw() -> dict[str, Any] | None:
    """Same three-case semantics as ``custody_pause._read_raw``: ``{}`` for
    "file absent" (clean, never armed), ``dict`` for a real read, ``None``
    for unreadable/corrupted (the doubt)."""
    path = _state_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        logger.warning("x_pause_state unreadable/corrupted (%s) -- UNKNOWN state", exc)
        return None
    if not isinstance(raw, dict):
        logger.warning("x_pause_state has unexpected shape (%r) -- UNKNOWN state", type(raw).__name__)
        return None
    return raw


def _write(payload: dict[str, Any]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def is_paused() -> bool:
    """True if X interactions (posting, replying, liking, profile sync)
    should be skipped. Fails CLOSED on an unreadable/corrupted state -- a
    tweet posted by mistake is irreversible and public, so "unknown" must
    default to "blocked". A missing file (never armed) returns False."""
    data = _read_raw()
    if data is None:
        logger.warning("x_pause state unreadable -- fail-closed: blocking X interactions for safety")
        return True
    return bool(data.get("paused"))


def pause_status() -> dict[str, Any]:
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
    _write(
        {
            "paused": True,
            "since": datetime.now(timezone.utc).isoformat(),
            "by": by,
            "reason": (reason or "").strip(),
        }
    )
    logger.warning("X INTERACTIONS PAUSED -- by=%s reason=%s", by, reason)
    return pause_status()


def resume(by: int | str | None = None) -> dict[str, Any]:
    _write(
        {
            "paused": False,
            "since": None,
            "by": by,
            "resumed_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    logger.warning("X INTERACTIONS RESUMED -- by=%s", by)
    return pause_status()


def since_label() -> str:
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


def blocked_notice(action: str = "Cette action X") -> str:
    return f"⛔ {action} est bloquée : X en pause manuelle ({since_label()}, /onx pour reprendre)."

