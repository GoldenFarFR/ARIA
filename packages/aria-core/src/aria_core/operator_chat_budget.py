"""Operator chat spend guardrail — caps DAILY LLM cost on the operator-only
chat channel (Telegram admin + mobile), never the public showcase or the
trading/analysis cycles (VC, momentum, scalping...), which don't go through
``AriaBrain._general_response`` at all.

07/08 operator decision ("bloque toute depense inutile"): the free-form LLM
personality chat stays exactly as it is — a prior proposal to template-reply
small talk by content was explicitly rejected ("non laisse le mais bloque
toute depense inutile") — but unbounded spend on casual exchanges (real
example: a 6451-token reply to a one-line joke) is not acceptable. Once the
daily cap is reached, the operator sees a plain template instead of a real
LLM call, until the next UTC-midnight reset. Threshold explicitly set by the
operator ("tu met 0.1"), deliberately tight — the goal is a real cutoff on
casual chat, not a comfortable full-day allowance.

State persisted to disk (`data_dir()/operator_chat_budget.json`) and
re-read on every check — survives a process restart, same pattern as
`outgoing_pause.py`. Fail-open on a missing/corrupted file: this is a
spend guardrail, not a money guardrail (real capital stays governed
exclusively by `outgoing_pause`/`wallet_guard`, untouched by this module) —
a damaged file must never brick the operator's ability to talk to ARIA.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aria_core.paths import data_dir

logger = logging.getLogger(__name__)

# 07/08, explicit operator decision ("tu met 0.1").
DAILY_LIMIT_USD = 0.10


def _state_path() -> Path:
    return data_dir() / "operator_chat_budget.json"


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _read_state() -> dict[str, Any]:
    path = _state_path()
    if not path.exists():
        return {"day": _today(), "spent_usd": 0.0}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("state file does not contain a JSON object")
        day = str(raw.get("day") or "")
        spent = float(raw.get("spent_usd") or 0.0)
        if day != _today():
            return {"day": _today(), "spent_usd": 0.0}
        return {"day": day, "spent_usd": spent}
    except Exception:
        logger.warning(
            "operator_chat_budget: corrupted state file, fail-open (reset to 0)", exc_info=True,
        )
        return {"day": _today(), "spent_usd": 0.0}


def _write_state(state: dict[str, Any]) -> None:
    try:
        _state_path().write_text(json.dumps(state), encoding="utf-8")
    except Exception:
        logger.warning("operator_chat_budget: failed to persist state", exc_info=True)


def daily_spent_usd() -> float:
    return _read_state()["spent_usd"]


def budget_exceeded() -> bool:
    return daily_spent_usd() >= DAILY_LIMIT_USD


def record_spend(cost_usd: float) -> None:
    if cost_usd <= 0:
        return
    state = _read_state()
    state["spent_usd"] += float(cost_usd)
    _write_state(state)


def budget_exceeded_reply(lang: str) -> str:
    if lang == "fr":
        return (
            f"Budget LLM du jour atteint ({DAILY_LIMIT_USD:.2f}$) — je redeviens "
            "bavarde après minuit UTC. Les commandes et consultations de données "
            "restent disponibles normalement, ce n'est que la conversation libre "
            "qui est mise en pause."
        )
    return (
        f"Daily LLM budget reached (${DAILY_LIMIT_USD:.2f}) — free-form replies "
        "resume after UTC midnight. Commands and data lookups stay available as "
        "usual, only free conversation is paused."
    )
