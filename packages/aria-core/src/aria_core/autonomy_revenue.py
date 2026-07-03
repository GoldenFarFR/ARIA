"""Boucle autonomie revenu — ARIA agit sans relance opérateur (local PC)."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aria_core.memory import append_memory
from aria_core.revenue_goals import monthly_total_usd
from aria_core.runtime import settings

_AUTONOMY_LOG = Path(os.environ.get("LOCALAPPDATA", "")) / "GoldenFar" / "aria-autonomy.jsonl"
_STATE_PATH = Path(os.environ.get("DATA_DIR", ".")) / "memory" / "autonomy_revenue_state.json"


def revenue_autonomy_enabled() -> bool:
    if not settings.aria_autonomous:
        return False
    raw = os.environ.get("ARIA_REVENUE_AUTONOMY", "true").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _log_autonomy(event: str, detail: dict[str, Any] | None = None) -> None:
    _AUTONOMY_LOG.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "at": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **(detail or {}),
    }
    with _AUTONOMY_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _load_state() -> dict[str, Any]:
    if not _STATE_PATH.is_file():
        return {}
    try:
        return json.loads(_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(state: dict[str, Any]) -> None:
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def _hours_since(iso: str | None) -> float:
    if not iso:
        return 1e9
    try:
        then = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - then).total_seconds() / 3600.0
    except Exception:
        return 1e9


async def run_revenue_autonomy_cycle(*, lang: str = "fr") -> dict[str, Any]:
    """Cycle autonome : poll ACP, scan marché, promo produit si revenu=0."""
    from aria_core.skills.acp_cli import is_acp_available
    from aria_core.skills.acp_market_intelligence import run_market_scan
    from aria_core.skills.acp_provider_skill import run_provider_cycle
    from aria_core.skills.acp_product_launch_skill import execute_product_launch

    result: dict[str, Any] = {
        "ok": True,
        "actions": [],
        "skipped": [],
    }
    state = _load_state()
    now = datetime.now(timezone.utc).isoformat()

    if not revenue_autonomy_enabled():
        result["ok"] = False
        result["reason"] = "ARIA_REVENUE_AUTONOMY off ou ARIA_AUTONOMOUS=false"
        return result

    month_usd = monthly_total_usd()

    if is_acp_available() and settings.aria_acp_provider_enabled:
        poll = await run_provider_cycle()
        result["acp_poll"] = poll
        if poll.get("processed", 0) > 0:
            result["actions"].append(f"acp_jobs:{poll.get('processed')}")
            _log_autonomy("acp_job_processed", poll)
        else:
            result["skipped"].append("acp_poll:0_events")

    if is_acp_available() and _hours_since(state.get("last_market_scan")) >= 20:
        scan = await run_market_scan()
        state["last_market_scan"] = now
        result["market_scan"] = {"source": scan.get("source"), "agents": scan.get("agent_count")}
        result["actions"].append("market_scan")
        _log_autonomy("market_scan", {"source": scan.get("source"), "agents": scan.get("agent_count")})

    promo_hours = float(os.environ.get("ARIA_AUTONOMY_PROMO_HOURS", "72"))
    if (
        month_usd <= 0
        and is_acp_available()
        and _hours_since(state.get("last_acp_promo")) >= promo_hours
    ):
        msg = "lancer produit acp template analyse_lite_x1 et publier sur X"
        reply, data = await execute_product_launch(msg, lang=lang)
        state["last_acp_promo"] = now
        result["acp_promo"] = data
        if data.get("promo", {}).get("x_posted"):
            result["actions"].append("x_promo_analyse_lite")
            _log_autonomy("x_promo_posted", {"offering": "analyse_lite_x1"})
        else:
            result["actions"].append("acp_promo_draft")
            _log_autonomy("acp_promo_draft", {"note": str(data.get("promo", {}))[:200]})
        result["promo_reply"] = reply[:400]

    if _hours_since(state.get("last_founder_initiative")) >= float(
        os.environ.get("ARIA_AUTONOMY_INITIATIVE_HOURS", "8")
    ):
        from aria_core.proactive import run_founder_ping

        initiative = await run_founder_ping(lang=lang)
        state["last_founder_initiative"] = now
        if initiative:
            result["actions"].append("founder_initiative")
            result["initiative"] = initiative[:500]
            _log_autonomy("founder_initiative", {"text": initiative[:300]})
            try:
                from aria_core.gateway.telegram_bot import notify_admin

                await notify_admin(f"Initiative autonome ARIA\n\n{initiative[:1500]}")
            except Exception:
                pass

    state["last_cycle"] = now
    state["monthly_usd"] = month_usd
    _save_state(state)
    append_memory(
        "autonomy",
        f"[revenue_cycle] actions={result.get('actions')} month=${month_usd:.2f}",
    )
    _log_autonomy("cycle_done", result)
    return result


def format_autonomy_status(lang: str = "fr") -> str:
    state = _load_state()
    on = revenue_autonomy_enabled()
    if lang == "fr":
        lines = [
            "═══ AUTONOMIE REVENU ARIA ═══",
            "",
            f"Mode : {'ON' if on else 'OFF'} (ARIA_AUTONOMOUS + ARIA_REVENUE_AUTONOMY)",
            f"Revenu ce mois : {monthly_total_usd():.2f} $",
            f"Dernier cycle : {state.get('last_cycle', 'jamais')}",
            f"Dernier scan marché : {state.get('last_market_scan', '—')}",
            f"Dernière promo ACP : {state.get('last_acp_promo', '—')}",
            f"Dernière initiative : {state.get('last_founder_initiative', '—')}",
            "",
            "Boucle auto (heartbeat ~6h si bot :8000 actif) :",
            "• poll jobs ACP (5 min)",
            "• scan marché (24h)",
            "• promo analyse_lite_x1 si 0 $ (72h)",
            "• initiative LLM (8h)",
            "",
            f"Journal : {_AUTONOMY_LOG}",
            "Démarrage : vanguard\\operator\\start-aria-autonomous.ps1",
        ]
    else:
        lines = [
            "═══ ARIA REVENUE AUTONOMY ═══",
            f"Mode: {'ON' if on else 'OFF'}",
            f"Monthly revenue: ${monthly_total_usd():.2f}",
            f"Log: {_AUTONOMY_LOG}",
        ]
    return "\n".join(lines)