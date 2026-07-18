"""Surveillance du solde prépayé x.ai (Grok) — alerte Telegram à 1$, bascule
automatique du disjoncteur LLM (llm_circuit_breaker.py) vers OpenRouter à 0,10$.

Gate implicite : `xai_billing.xai_billing_configured()` — sans XAI_MANAGEMENT_KEY/
XAI_TEAM_ID (clé Management distincte de GROK_API_KEY, cf. services/xai_billing.py),
ce cycle se contente de log une fois et ne fait rien, jamais un solde inventé.

Persistance minimale (`data_dir()/xai_balance_monitor.json`) pour ne PAS spammer
l'alerte à 1$ à chaque passage — une fois alertée, on se tait jusqu'à ce que le
solde remonte au-dessus du seuil (rechargement) OU que le disjoncteur s'arme.
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
        logger.info("xai_balance_monitor: XAI_MANAGEMENT_KEY/XAI_TEAM_ID absents — cycle ignoré")
        return {"skipped": "not_configured"}

    result = await get_prepaid_balance()
    if not result.available or result.balance_usd is None:
        logger.warning("xai_balance_monitor: solde indisponible (%s)", result.error)
        return {"skipped": "unavailable", "error": result.error}

    balance = result.balance_usd
    state = _read_state()
    already_armed = llm_circuit_breaker.is_armed()

    if already_armed:
        # Déjà basculé lors d'un passage précédent (ou armé manuellement) -- rien de
        # plus à faire tant que l'opérateur n'a pas désarmé lui-même (jamais un
        # ré-armement/re-notification en boucle, jamais un désarmement automatique
        # non plus : la décision de revenir sur Grok reste opérateur).
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
