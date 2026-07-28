"""Time-confirmation constants shared by the momentum pipeline (07/20).

Extracted from ``paper_trader.HIGH_WATER_CONFIRMATION_SECONDS`` and
``momentum_entry._WASH_TRADING_CONFIRMATION_SECONDS`` -- these two constants
used to be independent copies of the same value (75s), deliberately not linked
by a direct import to avoid a cycle (``paper_trader.py`` already imports from
``momentum_entry.py``). An external cross-review correctly flagged that this
duplication is real maintenance debt: nothing prevents changing one without
thinking of the other. This neutral module (no dependency on either) is now the
SOLE source of truth -- both files import it, never a hand-copied value.
"""
from __future__ import annotations

import time

MOMENTUM_CONFIRMATION_SECONDS = 75.0


# #128, 28/07 -- cross-path evaluation dedup. The periodic REST scan
# (momentum_entry.discover_momentum_candidates, ~30min heartbeat cadence) and
# the WebSocket drain (momentum_websocket.py, ~30s cadence) poll the SAME 4
# DexScreener discovery endpoints (token-boosts/profiles latest+top+recent) --
# a trending token surfaces on BOTH feeds around the same real-world time, so
# the slower periodic path can re-run the ENTIRE expensive per-candidate
# pipeline (honeypot, OHLCV, up to 2 LLM calls) on a token the WebSocket just
# evaluated moments ago.
#
# Written by ``paper_trader._default_momentum_analyzer``'s ``analyzer``
# closure -- the ONE place both the periodic cycle and the WebSocket drain
# actually call ``evaluate_momentum_entry``/``evaluate_bonding_entry``, so a
# single write site naturally covers both callers without needing to touch
# either evaluation function's many internal early-returns.
#
# Consulted ONLY by the periodic discovery (``momentum_entry._add_candidate``)
# -- deliberately NEVER by the WebSocket path itself. The WebSocket already
# has its OWN, more nuanced adaptive cooldown (``momentum_websocket.
# RESCAN_COOLDOWN_SECONDS``, price-move-aware: a >10% move deliberately
# BYPASSES its 4h cooldown). A generic short window consulted symmetrically
# here would silently fight that bypass -- so this module stays a one-way
# signal (WebSocket/periodic -> periodic only), never a second cooldown layer
# competing with the WebSocket's own.
#
# In-process only, same as ``momentum_websocket``'s own ``_seen``/``_pending``
# state -- lost on restart with no correctness impact (worst case: one extra
# cold re-evaluation), never persisted.
_RECENT_EVALUATION_WINDOW_SECONDS = 15 * 60  # same order of magnitude as
# momentum_websocket.DEDUP_TTL_SECONDS (not imported -- that would reintroduce
# the very cycle this module exists to avoid; kept as an independent value).
_MAX_TRACKED_EVALUATIONS = 2000  # bounds the dict on a long-running process;
# opportunistically purged on every write, never a scheduled task of its own.

_recent_evaluations: dict[tuple[str, str], tuple[float, str | None]] = {}


def _purge_expired_evaluations(now: float) -> None:
    expired = [
        key for key, (ts, _action) in _recent_evaluations.items()
        if (now - ts) >= _RECENT_EVALUATION_WINDOW_SECONDS
    ]
    for key in expired:
        del _recent_evaluations[key]
    # Defensive cap even if purge somehow lags (e.g. window bumped later
    # without updating this constant) -- drops the oldest entries first.
    if len(_recent_evaluations) > _MAX_TRACKED_EVALUATIONS:
        overflow = len(_recent_evaluations) - _MAX_TRACKED_EVALUATIONS
        oldest = sorted(_recent_evaluations.items(), key=lambda kv: kv[1][0])[:overflow]
        for key, _ in oldest:
            del _recent_evaluations[key]


def record_evaluation(contract: str, chain: str, action: str | None, *, now: float | None = None) -> None:
    """Best-effort bookkeeping -- called after EVERY real momentum evaluation,
    regardless of which path (periodic cycle or WebSocket drain) triggered
    it. Never raises, never blocks a decision."""
    ts = now if now is not None else time.time()
    _recent_evaluations[(contract.lower(), chain.lower())] = (ts, action)
    _purge_expired_evaluations(ts)


def recently_evaluated_action(
    contract: str, chain: str, *, window_seconds: float = _RECENT_EVALUATION_WINDOW_SECONDS,
    now: float | None = None,
) -> str | None:
    """Returns the last recorded action (e.g. "BUY"/"HOLD") if this (contract,
    chain) was evaluated within ``window_seconds``, else ``None`` (never
    evaluated, or long enough ago that a fresh evaluation is warranted)."""
    entry = _recent_evaluations.get((contract.lower(), chain.lower()))
    if entry is None:
        return None
    ts, action = entry
    current = now if now is not None else time.time()
    if (current - ts) >= window_seconds:
        return None
    return action
