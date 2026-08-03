"""Paper-trading runtime kill-switch -- ``/off`` and ``/on`` on Telegram.

Item #64 (08/03), operator request during a live debugging session: GeckoTerminal
was being hit both by manual investigation calls AND by every pocket's continuous
scanning (scalping/swing/vc/megacap, heartbeat cycles + the websocket drain), and
there was no way to halt the LATTER at runtime -- ``ARIA_PAPER_TRADING_ENABLED`` is
an environment variable, read once at container start, so flipping it requires a
full rebuild/redeploy (too slow for "stop scanning for the next 10 minutes").

Structurally separate from ``outgoing_pause``/``custody_pause`` (both real-money-
only, per Item #62's own split -- paper trading must never be blockable by either,
that split is the whole point of Item #62's existence). This module exists purely
to silence data-provider load during manual debugging, never a financial safety
mechanism -- there is nothing to "block" here, only scanning/sourcing to skip.

Near-exact structural copy of ``custody_pause.py`` (own JSON state file, same
read/write/fail-*-open* semantics -- see ``is_paused`` for why this one fails
OPEN, the opposite of custody_pause's fail-closed doctrine).
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
    return data_dir() / "paper_pause_state.json"


def _read_raw() -> dict[str, Any] | None:
    path = _state_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        logger.warning("paper_pause_state unreadable/corrupted (%s) -- UNKNOWN state", exc)
        return None
    if not isinstance(raw, dict):
        logger.warning("paper_pause_state has unexpected shape (%r) -- UNKNOWN state", type(raw).__name__)
        return None
    return raw


def _write(payload: dict[str, Any]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def is_paused() -> bool:
    """True if paper-trading scanning/sourcing should be skipped this tick.

    Fails OPEN (returns False -- keep trading) on an unreadable/corrupted state,
    the deliberate opposite of custody_pause's fail-closed doctrine: this flag
    guards zero capital and zero irreversible action, so treating "unknown" as
    "keep running normally" is strictly safer than silently freezing the entire
    $1M diagnostic test over a corrupted debug toggle nobody asked to be a
    financial guard rail.
    """
    data = _read_raw()
    if data is None:
        logger.warning("paper_pause state unreadable -- fail-open: paper trading keeps running")
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
    logger.warning("PAPER TRADING PAUSED (scanning/sourcing skipped) -- by=%s reason=%s", by, reason)
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
    logger.warning("PAPER TRADING RESUMED (scanning/sourcing re-armed) -- by=%s", by)
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
