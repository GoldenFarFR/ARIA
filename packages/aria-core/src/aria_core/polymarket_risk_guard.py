"""Drawdown circuit breaker for the Polymarket paper portfolio (Item #109,
26/07) -- same doctrine as ``risk_guard.py``'s portfolio circuit breaker
(#186), applied to a STRUCTURALLY SEPARATE pocket (``polymarket_paper_
trader.py``, its own $100k fictitious capital, its own equity high-water
mark).

Deliberately a DEDICATED module rather than reusing risk_guard.py directly:
these are two independent portfolios (different starting capital, different
equity, different position lifecycle -- Polymarket positions never get
managed/trailing-stopped, they just wait for a binary resolution) with their
own risk state. A drawdown on one must never silently gate the other, and a
persisted "blocked" file shared between them would do exactly that. Same
"never confuse mechanisms" principle risk_guard.py itself already states
about ``outgoing_pause`` vs its own circuit breaker.

Thresholds start at the SAME values as risk_guard.py's (no real resolved-bet
history yet to justify a different number) but are separate constants here,
free to diverge once real Polymarket paper results accumulate -- same
"start conservative, measure, adjust independently" doctrine as
``polymarket_thesis.py``'s own MIN_EDGE_PROBABILITY/MIN_WIN_PROBABILITY.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aria_core.paths import data_dir

logger = logging.getLogger(__name__)

SOFT_DRAWDOWN_PCT = 0.10       # -10% from equity high -> new bets halved
HARD_DRAWDOWN_PCT = 0.20       # -20% from the high -> blocks any new bet
HARD_CONSECUTIVE_LOSSES = 5    # 5 consecutive resolved losses -> also blocks
SOFT_ALLOC_MULTIPLIER = 0.5

_BAND_NONE = "none"
_BAND_SOFT = "soft"
_BAND_HARD = "hard"


def _state_path() -> Path:
    return data_dir() / "polymarket_risk_guard_state.json"


def _read_raw() -> dict[str, Any] | None:
    """Same three-state semantics as ``risk_guard._read_raw``: ``{}`` (file
    absent -- never triggered, not a doubt), ``dict`` (read correctly),
    ``None`` (corrupted -- UNKNOWN state, fail-closed by the caller)."""
    path = _state_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        logger.warning("polymarket_risk_guard_state unreadable/corrupted (%s) -- UNKNOWN state", exc)
        return None
    if not isinstance(raw, dict):
        logger.warning("polymarket_risk_guard_state has unexpected shape (%r) -- UNKNOWN state", type(raw).__name__)
        return None
    return raw


def _write(payload: dict[str, Any]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def new_bet_block_status() -> dict[str, Any]:
    """Current state of the dedicated circuit breaker:
    ``{blocked, since, reason, by, last_alert_band, readable}``.
    ``readable=False`` signals a corrupted file -- fail-closed on the
    caller's side (``blocks_new_bets``), same "money" doctrine as
    ``risk_guard.new_entry_block_status``."""
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
        "blocked": bool(data.get("blocked")),
        "since": since,
        "by": data.get("by"),
        "reason": data.get("reason") or "",
        "last_alert_band": data.get("last_alert_band") or _BAND_NONE,
        "readable": readable,
    }


def block_new_bets(reason: str, *, by: int | str | None = None) -> dict[str, Any]:
    """Arms the hard tier: no more NEW Polymarket bets until
    ``resume_new_bets`` has been called explicitly (never automatic)."""
    _write(
        {
            "blocked": True,
            "since": datetime.now(timezone.utc).isoformat(),
            "by": by,
            "reason": (reason or "").strip(),
            "last_alert_band": _BAND_HARD,
        }
    )
    logger.warning("polymarket_risk_guard: circuit breaker ARMED (hard tier) -- reason=%s", reason)
    return new_bet_block_status()


def resume_new_bets(*, by: int | str | None = None) -> dict[str, Any]:
    """Lifts the circuit breaker. NEVER called automatically by
    ``evaluate_portfolio_risk`` -- reserved for an explicit human action,
    even if the drawdown has since recovered."""
    _write(
        {
            "blocked": False,
            "since": None,
            "by": by,
            "reason": "",
            "last_alert_band": _BAND_NONE,
            "resumed_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    logger.warning("polymarket_risk_guard: circuit breaker LIFTED (manual resume) -- by=%s", by)
    return new_bet_block_status()


def blocks_new_bets() -> tuple[bool, str | None]:
    """``(blocked, reason)`` -- combines the dedicated circuit breaker AND
    ``outgoing_pause`` (a global pause also blocks new paper bets) WITHOUT
    ever confusing the two mechanisms in the reported reason. Fail-closed on
    unreadable state ("money" doctrine)."""
    from aria_core import outgoing_pause

    if outgoing_pause.is_paused():
        return True, "ARIA en pause globale (kill-switch sortant) — aucun nouveau pari Polymarket tant que /start n'est pas donné."

    status = new_bet_block_status()
    if not status["readable"]:
        return True, "état du coupe-circuit Polymarket illisible/corrompu — fail-closed par sécurité"
    if status["blocked"]:
        return True, status["reason"] or "coupe-circuit Polymarket armé — reprise manuelle requise"
    return False, None


@dataclass
class PolymarketRiskState:
    equity: float
    high_water_mark: float
    drawdown_pct: float             # 0..1 from the high
    consecutive_losses: int
    alloc_multiplier: float         # 1.0 normal, SOFT_ALLOC_MULTIPLIER if soft tier
    blocked: bool
    blocked_reason: str | None = None
    newly_triggered_soft: bool = False
    newly_triggered_hard: bool = False


async def evaluate_portfolio_risk() -> PolymarketRiskState:
    """Snapshot of the Polymarket paper portfolio's risk -- to be called ONCE
    per cycle, before attempting to judge/book any new bet (never before
    ``check_resolutions``, which must always run regardless of the circuit
    breaker's state). Updates the persisted equity high-water mark and arms
    the dedicated circuit breaker if a hard tier is crossed for the first
    time."""
    from aria_core import polymarket_paper_trader as ppt

    summary = await ppt.portfolio_summary()
    equity = float(summary["equity"])

    hwm = await ppt.get_equity_high_water_mark()
    if equity > hwm:
        hwm = equity
        await ppt.set_equity_high_water_mark(hwm)
    drawdown_pct = max(0.0, (hwm - equity) / hwm) if hwm > 0 else 0.0

    closed = await ppt.get_closed_positions(limit=HARD_CONSECUTIVE_LOSSES)
    consecutive_losses = 0
    for p in closed:
        if (p.get("pnl_usd") or 0.0) < 0:
            consecutive_losses += 1
        else:
            break

    status = new_bet_block_status()
    already_blocked = status["blocked"]
    hard_breach = drawdown_pct >= HARD_DRAWDOWN_PCT or consecutive_losses >= HARD_CONSECUTIVE_LOSSES
    newly_triggered_hard = False
    if hard_breach and not already_blocked and status["readable"]:
        reason = (
            f"drawdown {drawdown_pct:.1%} depuis le plus haut d'équité Polymarket ({hwm:,.0f} $)"
            if drawdown_pct >= HARD_DRAWDOWN_PCT
            else f"{consecutive_losses} pertes consécutives (Polymarket)"
        )
        block_new_bets(reason)
        newly_triggered_hard = True
        already_blocked = True

    soft_breach = SOFT_DRAWDOWN_PCT <= drawdown_pct < HARD_DRAWDOWN_PCT
    newly_triggered_soft = False
    if not already_blocked:
        last_band = status["last_alert_band"]
        if soft_breach and last_band != _BAND_SOFT:
            _write({"blocked": False, "since": None, "by": None, "reason": "", "last_alert_band": _BAND_SOFT})
            newly_triggered_soft = True
        elif not soft_breach and last_band == _BAND_SOFT:
            _write({"blocked": False, "since": None, "by": None, "reason": "", "last_alert_band": _BAND_NONE})

    blocked, blocked_reason = blocks_new_bets()
    alloc_multiplier = SOFT_ALLOC_MULTIPLIER if (soft_breach and not blocked) else 1.0

    return PolymarketRiskState(
        equity=equity,
        high_water_mark=hwm,
        drawdown_pct=drawdown_pct,
        consecutive_losses=consecutive_losses,
        alloc_multiplier=alloc_multiplier,
        blocked=blocked,
        blocked_reason=blocked_reason,
        newly_triggered_soft=newly_triggered_soft,
        newly_triggered_hard=newly_triggered_hard,
    )


def format_soft_drawdown_alert(state: PolymarketRiskState) -> str:
    return "\n".join([
        "🧪 [FICTIF] Coupe-circuit Polymarket — palier SOUPLE",
        f"Drawdown {state.drawdown_pct:.1%} depuis le plus haut d'équité Polymarket ({state.high_water_mark:,.0f} $).",
        f"Taille des NOUVEAUX paris réduite de moitié (×{SOFT_ALLOC_MULTIPLIER}) jusqu'à résorption.",
        "Positions déjà ouvertes : inchangées, attendent leur résolution normale.",
    ])


def format_hard_drawdown_alert(state: PolymarketRiskState) -> str:
    return "\n".join([
        "🧪 [FICTIF] Coupe-circuit Polymarket — palier DUR",
        f"Drawdown {state.drawdown_pct:.1%} depuis le plus haut d'équité Polymarket ({state.high_water_mark:,.0f} $) "
        f"ou {state.consecutive_losses} pertes consécutives.",
        "Plus aucun NOUVEAU pari tant que la reprise manuelle n'est pas donnée.",
        "Positions déjà ouvertes : inchangées, attendent leur résolution normale.",
    ])
