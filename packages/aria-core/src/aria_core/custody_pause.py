"""Custody kill-switch — auto-armed, real-money-only, scoped separately from
``outgoing_pause``.

Item #62 (08/03), real incident: `agent_wallet_monitor.py`'s automatic
`unexpected_outflow` detector used to arm the SAME global flag as the manual
`/stop` (`outgoing_pause.py`) -- a false positive on a real wallet (a
legitimate OpenRouter recharge misclassified) silently froze ALL paper
trading (momentum_websocket.py's drain, paper_trader.py's cycles) for ~5h15,
even though paper trading has zero custody surface (no key, no CDP account,
cannot move real funds under any current design) and CLAUDE.md's own
absolute rule states it "is a pure test, without human approval" -- never
meant to be in scope of a real-money kill-switch.

Two independent workflow analyses (08/03) converged on the same fix:
separate the SIGNAL (an automatic custody-origin detector) from the EFFECT
(the operator's manual "stop everything" button). This module is the new
SIGNAL half -- checked ONLY by real-capital paths (`wallet_guard.py`,
`agent_wallet_pilot.py`, `agent_wallet_smart_swing.py`), alongside (never
instead of) the existing `outgoing_pause.is_paused(strict=True)` check,
which keeps covering the manual `/stop` case exactly as before, still
reaching paper trading too (the operator's own broad "halt everything"
lever stays a single, simple button).

Structurally a near-exact copy of ``outgoing_pause.py`` (own JSON state
file, same read/write/fail-closed semantics) -- deliberately NOT the same
file (``outgoing_pause.py`` is flagged in CLAUDE.md as "kill-switch, testé
-- ne pas recoder"). Forward-compatible with the planned 3-Smart-Wallet
architecture (scalping/swing/vc): today a single shared custody flag (n=1
real wallet); scoping it per-wallet later is a follow-up, not a
re-architecture.
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
    return data_dir() / "custody_pause_state.json"


def _read_raw() -> dict[str, Any] | None:
    """Same three-case semantics as ``outgoing_pause._read_raw``: ``{}`` for
    "file absent" (clean, never armed), ``dict`` for a real read, ``None``
    for unreadable/corrupted (the doubt)."""
    path = _state_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        logger.warning("custody_pause_state unreadable/corrupted (%s) — UNKNOWN state", exc)
        return None
    if not isinstance(raw, dict):
        logger.warning("custody_pause_state has unexpected shape (%r) — UNKNOWN state", type(raw).__name__)
        return None
    return raw


def _write(payload: dict[str, Any]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def is_paused() -> bool:
    """True if the custody kill-switch is armed. Always fail-closed on an
    unreadable/corrupted state (this module is real-money-only by
    construction -- there is no non-strict caller to fail open for, unlike
    ``outgoing_pause``). A missing file (never armed) returns False."""
    data = _read_raw()
    if data is None:
        logger.warning("custody_pause state unreadable — fail-closed: freezing real-money paths for safety")
        return True
    return bool(data.get("paused"))


def money_block_reason(action: str = "Cette dépense") -> str | None:
    """Same fail-closed doctrine as ``outgoing_pause.money_block_reason`` --
    kept separate so a caller that already combines both checks (e.g.
    ``wallet_guard.py``) gets one clear reason string per source."""
    data = _read_raw()
    if data is None:
        return (
            f"⛔ {action} est bloquée : l'état de pause de custody est illisible/corrompu. "
            "Par sécurité, les dépenses sont gelées dans le doute (fail-closed)."
        )
    if data.get("paused"):
        return blocked_notice(action)
    return None


def pause_status() -> dict[str, Any]:
    """Current state: {paused, since (datetime|None), by, reason, readable}."""
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
    """Arms the custody kill-switch. Blocks real-money paths until ``resume``
    -- never touches paper trading (that's the whole point of this module's
    existence, see the module docstring)."""
    _write(
        {
            "paused": True,
            "since": datetime.now(timezone.utc).isoformat(),
            "by": by,
            "reason": (reason or "").strip(),
        }
    )
    logger.warning("CUSTODY PAUSED (real-money kill-switch armed) — by=%s reason=%s", by, reason)
    return pause_status()


def resume(by: int | str | None = None) -> dict[str, Any]:
    """Lifts the custody kill-switch."""
    _write(
        {
            "paused": False,
            "since": None,
            "by": by,
            "resumed_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    logger.warning("CUSTODY RESUMED (real-money kill-switch lifted) — by=%s", by)
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


def blocked_notice(action: str = "Cette action sortante") -> str:
    return (
        f"⛔ {action} est bloquée : anomalie de custody détectée automatiquement {since_label()} "
        "(sortie non initiée par ARIA sur un wallet réel, cf. agent_wallet_monitor.py).\n"
        "Confirme via le message d'alerte Telegram (« Autorisé par moi » lève cette pause) "
        "avant toute nouvelle dépense réelle."
    )
