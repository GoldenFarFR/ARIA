"""Shadow-pocket no-new-positions switch -- ``/offshadowtrades`` and
``/onshadowtrades`` on Telegram.

Operator request (25/08), same day the 3 shadow pockets (base_momentum,
robinhood_pump, solana_late_bonding) were reset for the DefiLlama
chain-regime work: their current entry/exit thresholds are already known
to be poor, and re-accumulating more closures under that same known-bad
config while a recalibration is in progress is wasted signal. But the
candidate discovery loop itself (pretrade_rejection_log entries, regime
candidate tracking) stays useful and should keep running -- unlike
``shadow_pause`` (``/offshadow``), which cuts every loop wholesale, this
switch is scoped to ONLY the final decision to open a new shadow position.

Fifth independent pause, same family as ``shadow_pause.py`` -- own state
file, never touches another's:
- ``outgoing_pause`` (``/stop``/``/resume``) -- real capital AND X.
- ``paper_pause`` (``/offpaper``/``/onpaper``) -- paper-trading scanning.
- ``x_pause`` (``/offx``/``/onx``) -- X interactions.
- ``shadow_pause`` (``/offshadow``/``/onshadow``) -- every shadow loop.
- this module (``/offshadowtrades``/``/onshadowtrades``) -- just the
  final INSERT that opens a new shadow position; discovery/rejection
  logging/regime-candidate tracking are UNAFFECTED.

Fails OPEN on an unreadable/corrupted state, same doctrine as
``shadow_pause.py``: this guards zero capital (shadow is observation-only),
so "unknown" defaulting to "keep opening positions" is strictly safer than
silently starving every pocket's sample over a corrupted debug toggle.

Structurally a near-exact copy of ``shadow_pause.py``.
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
    return data_dir() / "shadow_discovery_only_state.json"


def _read_raw() -> dict[str, Any] | None:
    path = _state_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        logger.warning("shadow_discovery_only_state unreadable/corrupted (%s) -- UNKNOWN state", exc)
        return None
    if not isinstance(raw, dict):
        logger.warning("shadow_discovery_only_state has unexpected shape (%r) -- UNKNOWN state", type(raw).__name__)
        return None
    return raw


def _write(payload: dict[str, Any]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def is_discovery_only() -> bool:
    """True if the 3 shadow pockets should keep discovering/logging
    candidates but never open a new position. Fails OPEN (returns False --
    positions may open) on an unreadable/corrupted state -- this flag
    guards zero capital, same reasoning as ``shadow_pause.is_paused``."""
    data = _read_raw()
    if data is None:
        logger.warning("shadow_discovery_only state unreadable -- fail-open: positions may still open")
        return False
    return bool(data.get("discovery_only"))


def status() -> dict[str, Any]:
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
        "discovery_only": bool(data.get("discovery_only")),
        "since": since,
        "by": data.get("by"),
        "reason": data.get("reason") or "",
        "readable": readable,
    }


def arm(by: int | str | None = None, reason: str = "") -> dict[str, Any]:
    _write(
        {
            "discovery_only": True,
            "since": datetime.now(timezone.utc).isoformat(),
            "by": by,
            "reason": (reason or "").strip(),
        }
    )
    logger.warning("SHADOW DISCOVERY-ONLY ARMED (no new positions, discovery unaffected) -- by=%s reason=%s", by, reason)
    return status()


def disarm(by: int | str | None = None) -> dict[str, Any]:
    _write(
        {
            "discovery_only": False,
            "since": None,
            "by": by,
            "resumed_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    logger.warning("SHADOW DISCOVERY-ONLY DISARMED (positions may open again) -- by=%s", by)
    return status()


def since_label() -> str:
    since = status().get("since")
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
