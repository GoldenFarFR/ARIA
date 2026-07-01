"""Real revenue tracking toward ARIA monthly goal — separate from fictional training portfolio."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from aria_core.runtime import settings
from aria_core.paths import memory_dir

LEDGER_PATH = memory_dir() / "revenue_ledger.json"
INITIATIVE_PATH = memory_dir() / "entrepreneur_initiative.md"


def _load() -> dict[str, Any]:
    if not LEDGER_PATH.exists():
        return {
            "goal_monthly_usd": settings.aria_revenue_goal_monthly_usd,
            "goal_started_at": datetime.now(timezone.utc).isoformat(),
            "entries": [],
            "personal_objectives": [],
        }
    try:
        return json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"goal_monthly_usd": settings.aria_revenue_goal_monthly_usd, "entries": [], "personal_objectives": []}


def _save(data: dict[str, Any]) -> None:
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    LEDGER_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _parse_ts(raw: str) -> datetime:
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


def _entries_this_month(ledger: dict[str, Any]) -> list[dict]:
    month = datetime.now(timezone.utc).strftime("%Y-%m")
    out = []
    for e in ledger.get("entries", []):
        try:
            if _parse_ts(e["at"]).strftime("%Y-%m") == month:
                out.append(e)
        except Exception:
            continue
    return out


def monthly_total_usd() -> float:
    return round(sum(float(e.get("amount_usd", 0)) for e in _entries_this_month(_load())), 2)


def total_revenue_usd() -> float:
    return round(sum(float(e.get("amount_usd", 0)) for e in _load().get("entries", [])), 2)


def goal_progress() -> dict[str, Any]:
    ledger = _load()
    goal = float(ledger.get("goal_monthly_usd", settings.aria_revenue_goal_monthly_usd))
    total = monthly_total_usd()
    remaining = max(0.0, round(goal - total, 2))
    pct = min(100.0, round((total / goal * 100) if goal > 0 else 0, 1))
    return {
        "goal_monthly_usd": goal,
        "monthly_total_usd": total,
        "remaining_usd": remaining,
        "progress_pct": pct,
        "on_track": total >= goal,
        "entries_this_month": len(_entries_this_month(ledger)),
        "goal_started_at": ledger.get("goal_started_at"),
        "personal_objectives": ledger.get("personal_objectives", []),
    }


def record_revenue(
    amount_usd: float,
    *,
    source: str,
    note: str = "",
    recurring: bool = False,
) -> dict[str, Any]:
    ledger = _load()
    entry = {
        "at": datetime.now(timezone.utc).isoformat(),
        "amount_usd": round(float(amount_usd), 2),
        "source": source[:80],
        "note": note[:200],
        "recurring": recurring,
    }
    ledger.setdefault("entries", []).append(entry)
    _save(ledger)
    return entry


def set_personal_objectives(objectives: list[str]) -> list[str]:
    ledger = _load()
    cleaned = [o.strip()[:200] for o in objectives if o.strip()][:10]
    ledger["personal_objectives"] = cleaned
    _save(ledger)
    return cleaned


def progress_summary(lang: str = "fr") -> str:
    p = goal_progress()
    if lang == "en":
        status = "ON TRACK" if p["on_track"] else "IN PROGRESS"
        return (
            f"Revenue goal — {status}\n"
            f"- Target: ${p['goal_monthly_usd']:.0f}/mo (operator mandate, month 1)\n"
            f"- This month: ${p['monthly_total_usd']:.2f} ({p['progress_pct']:.0f}%)\n"
            f"- Remaining: ${p['remaining_usd']:.2f}\n"
            f"- Logged entries: {p['entries_this_month']}"
        )
    status = "ATTEINT" if p["on_track"] else "EN COURS"
    return (
        f"Objectif revenu — {status}\n"
        f"- Cible : {p['goal_monthly_usd']:.0f} $/mois (mandat opérateur, mois 1)\n"
        f"- Ce mois : {p['monthly_total_usd']:.2f} $ ({p['progress_pct']:.0f} %)\n"
        f"- Reste : {p['remaining_usd']:.2f} $\n"
        f"- Entrées loguées : {p['entries_this_month']}"
    )