"""Objectifs opérationnels ARIA — Phase F (SSOT YAML + état dynamique → injection LLM)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

_GOALS_PATH = Path(__file__).resolve().parents[1] / "knowledge" / "aria_goals.yaml"
_GOALS_BUDGET = 1600


@lru_cache(maxsize=1)
def _load_goals() -> list[dict]:
    if not _GOALS_PATH.is_file():
        return []
    try:
        raw = yaml.safe_load(_GOALS_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        return []
    items = raw.get("goals") or []
    if not isinstance(items, list):
        return []
    out: list[dict] = []
    for item in items:
        if isinstance(item, dict) and item.get("text"):
            out.append(item)
    out.sort(key=lambda g: int(g.get("priority", 0)), reverse=True)
    return out


def _dynamic_state_lines() -> list[str]:
    lines: list[str] = []
    try:
        from aria_core.revenue_goals import goal_progress, total_revenue_usd

        p = goal_progress()
        lines.append(
            f"- **Revenu réel total** : {total_revenue_usd():.2f} $ "
            "(aucun produit payant aujourd'hui — track-record d'abord)"
        )
        personal = p.get("personal_objectives") or []
        for obj in personal[:3]:
            if obj:
                lines.append(f"- **Objectif perso** : {str(obj)[:120]}")
    except Exception:
        pass
    return lines


def get_goals_text(*, budget_chars: int = _GOALS_BUDGET, lang: str = "fr") -> str:
    """Bloc markdown des objectifs ARIA pour le contexte LLM (opérateur)."""
    goals = _load_goals()
    if not goals and not _dynamic_state_lines():
        return ""
    title = "Objectifs opérationnels ARIA (Phase F)" if lang == "fr" else "ARIA operational goals (Phase F)"
    lines = [f"# {title}"]
    used = len(lines[0])
    for item in goals:
        name = (item.get("title") or item.get("id") or "goal").strip()
        horizon = (item.get("horizon") or "").strip()
        status = (item.get("status") or "pending").strip()
        text = " ".join(str(item.get("text") or "").split())
        meta = f" [{horizon}, {status}]" if horizon else f" [{status}]"
        line = f"- **{name}**{meta} : {text}"
        if used + len(line) + 1 > budget_chars:
            break
        lines.append(line)
        used += len(line) + 1
    dynamic = _dynamic_state_lines()
    if dynamic and used < budget_chars:
        lines.append("\n## État actuel")
        used += len(lines[-1]) + 1
        for dline in dynamic:
            if used + len(dline) + 1 > budget_chars:
                break
            lines.append(dline)
            used += len(dline) + 1
    return "\n".join(lines)


def goals_count() -> int:
    return len(_load_goals())


def clear_goals_cache() -> None:
    _load_goals.cache_clear()