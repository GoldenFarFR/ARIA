"""Monitors the x.ai (Grok) prepaid balance — Telegram alert at $1, automatic
LLM circuit-breaker switchover (llm_circuit_breaker.py) to OpenRouter at $0.10.

Implicit gate: `xai_billing.xai_billing_configured()` — without
XAI_MANAGEMENT_KEY/XAI_TEAM_ID (a Management key distinct from GROK_API_KEY,
see services/xai_billing.py), this cycle just logs once and does nothing,
never a fabricated balance.

Minimal persistence (`data_dir()/xai_balance_monitor.json`) to NOT spam the
$1 alert on every pass — once alerted, it stays quiet until the balance
goes back above the threshold (top-up) OR the breaker trips.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Awaitable, Callable

from aria_core import llm_circuit_breaker
from aria_core.paths import data_dir
from aria_core.services.xai_billing import get_prepaid_balance, xai_billing_configured

logger = logging.getLogger(__name__)

LOW_BALANCE_ALERT_THRESHOLD_USD = 1.0
CIRCUIT_BREAKER_THRESHOLD_USD = 0.10

BREAKER_PROVIDER = "openrouter"
BREAKER_MODEL = "anthropic/claude-sonnet-5"
BREAKER_FALLBACK_MODEL = "anthropic/claude-haiku-4.5"


def _state_path() -> Path:
    return data_dir() / "xai_balance_monitor.json"


def _read_state() -> dict[str, Any]:
    path = _state_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (json.JSONDecodeError, OSError, ValueError):
        return {}


def _write_state(payload: dict[str, Any]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


async def run_balance_check_cycle(
    notifier: Callable[[str], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    if not xai_billing_configured():
        logger.info("xai_balance_monitor: XAI_MANAGEMENT_KEY/XAI_TEAM_ID missing — cycle skipped")
        return {"skipped": "not_configured"}

    result = await get_prepaid_balance()
    if not result.available or result.balance_usd is None:
        logger.warning("xai_balance_monitor: balance unavailable (%s)", result.error)
        return {"skipped": "unavailable", "error": result.error}

    balance = result.balance_usd
    state = _read_state()
    already_armed = llm_circuit_breaker.is_armed()

    if already_armed:
        # Already switched over on a previous pass (or manually armed) --
        # nothing more to do until the operator disarms it themselves (never
        # a re-arm/re-notify loop, and never an automatic disarm either: the
        # decision to go back to Grok stays with the operator).
        return {"action": "none", "balance_usd": balance}

    if balance <= CIRCUIT_BREAKER_THRESHOLD_USD:
        llm_circuit_breaker.arm(
            provider=BREAKER_PROVIDER,
            model=BREAKER_MODEL,
            fallback_model=BREAKER_FALLBACK_MODEL,
            reason=f"solde x.ai tombé à {balance:.2f}$ (seuil {CIRCUIT_BREAKER_THRESHOLD_USD}$)",
            triggered_by="xai_balance_monitor",
        )
        _write_state({"alerted_low": True})
        if notifier:
            await notifier(
                f"🔴 Grok déconnecté automatiquement — solde x.ai à {balance:.2f}$ "
                f"(seuil {CIRCUIT_BREAKER_THRESHOLD_USD}$). Bascule sur OpenRouter "
                f"({BREAKER_MODEL}, secours {BREAKER_FALLBACK_MODEL})."
            )
        return {"action": "circuit_breaker_armed", "balance_usd": balance}

    if balance <= LOW_BALANCE_ALERT_THRESHOLD_USD and not state.get("alerted_low"):
        _write_state({"alerted_low": True})
        if notifier:
            await notifier(
                f"🟡 Solde x.ai (Grok) bas : {balance:.2f}$ restants. Bascule automatique "
                f"sur OpenRouter prévue sous {CIRCUIT_BREAKER_THRESHOLD_USD}$."
            )
        return {"action": "low_balance_alerted", "balance_usd": balance}

    if balance > LOW_BALANCE_ALERT_THRESHOLD_USD and state.get("alerted_low"):
        _write_state({"alerted_low": False})
        return {"action": "reset_after_topup", "balance_usd": balance}

    return {"action": "none", "balance_usd": balance}
