"""Valeurs opérationnelles ARIA — Phase E (SSOT YAML → injection LLM)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

_DNA_PATH = Path(__file__).resolve().parents[1] / "knowledge" / "dna.yaml"
_VALUES_BUDGET = 1400


@lru_cache(maxsize=1)
def _load_values() -> list[dict]:
    if not _DNA_PATH.is_file():
        return []
    try:
        raw = yaml.safe_load(_DNA_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        return []
    items = (raw.get("dna") or {}).get("valeurs") or []
    if not isinstance(items, list):
        return []
    out: list[dict] = []
    for item in items:
        if isinstance(item, dict) and item.get("text"):
            out.append(item)
    out.sort(key=lambda v: int(v.get("priority", 0)), reverse=True)
    return out


def get_values_text(*, budget_chars: int = _VALUES_BUDGET) -> str:
    """Bloc markdown des valeurs ARIA pour le contexte LLM."""
    values = _load_values()
    if not values:
        return ""
    lines = ["# Valeurs opérationnelles ARIA (Phase E)"]
    used = len(lines[0])
    for item in values:
        title = (item.get("title") or item.get("id") or "value").strip()
        text = " ".join(str(item.get("text") or "").split())
        line = f"- **{title}** : {text}"
        if used + len(line) + 1 > budget_chars:
            break
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines)


def values_count() -> int:
    return len(_load_values())


def clear_values_cache() -> None:
    _load_values.cache_clear()