"""Shadow-pocket kill-switch -- ``/offshadow`` and ``/onshadow`` on Telegram.

Operator request (24/08), same day as the Chainstack credit-leak fix: the
standalone shadow process (``shadow_persistent.py``, outside this repo,
systemd service ``aria-shadow-persistent``) had NO manual lever at all --
stopping it required a direct VPS ``systemctl`` action, never something the
operator could trigger from Telegram like every other pause in this
project. This module is that lever, read by that process's own supervisor
(``_shadow_should_be_paused()`` in ``shadow_persistent.py``), which also
reads ``outgoing_pause``/``custody_pause`` alongside it -- one upstream
breaker, three switches that can arm it, all downstream loops cut together
("comme un schema electrique": cut the switch, everything wired after it
goes dark, not one check per appliance).

Fourth and last of the independent pauses, none of which ever touch
another's state file:
- ``outgoing_pause`` (``/stop``/``/resume``) -- real capital AND X, the
  absolute kill-switch, "kill-switch, tested -- do not recode" per CLAUDE.md.
- ``paper_pause`` (``/offpaper``/``/onpaper``) -- paper-trading scanning
  only, fails OPEN (guards zero capital).
- ``x_pause`` (``/offx``/``/onx``) -- X interactions only, fails CLOSED.
- this module (``/offshadow``/``/onshadow``) -- the standalone shadow
  process only.

Fails OPEN on an unreadable/corrupted state, same doctrine as
``paper_pause.py`` (not ``x_pause.py``'s fail-closed): a shadow pocket is
observation-only, zero capital and zero irreversible public action, so
"unknown" defaulting to "keep running" is strictly safer than silently
freezing every shadow pocket's data collection over a corrupted debug
toggle.

Structurally a near-exact copy of ``paper_pause.py`` (own JSON state file,
same read/write/fail-open semantics).
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
    return data_dir() / "shadow_pause_state.json"


def _read_raw() -> dict[str, Any] | None:
    path = _state_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        logger.warning("shadow_pause_state unreadable/corrupted (%s) -- UNKNOWN state", exc)
        return None
    if not isinstance(raw, dict):
        logger.warning("shadow_pause_state has unexpected shape (%r) -- UNKNOWN state", type(raw).__name__)
        return None
    return raw


def _write(payload: dict[str, Any]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def is_paused() -> bool:
    """True if the standalone shadow process's supervisor should keep every
    loop cancelled. Fails OPEN (returns False -- keep running) on an
    unreadable/corrupted state -- this flag guards zero capital, same
    reasoning as ``paper_pause.is_paused``."""
    data = _read_raw()
    if data is None:
        logger.warning("shadow_pause state unreadable -- fail-open: shadow loops keep running")
        return False
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
    logger.warning("SHADOW PAUSED (standalone process loops cancelled) -- by=%s reason=%s", by, reason)
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
    logger.warning("SHADOW RESUMED (standalone process loops re-armed) -- by=%s", by)
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
