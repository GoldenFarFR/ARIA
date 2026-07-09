"""X curiosity watchlist — operator + ZHC peers + defaults (SSOT: x_watchlist.yaml)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_WATCHLIST_PATH = Path(__file__).with_name("x_watchlist.yaml")


@lru_cache(maxsize=1)
def _load() -> dict[str, Any]:
    if not _WATCHLIST_PATH.exists():
        return {}
    return yaml.safe_load(_WATCHLIST_PATH.read_text(encoding="utf-8")) or {}


def operator_watch_handles() -> list[str]:
    """YAML SSOT + ajouts opérateur (handle_registry.json)."""
    try:
        from aria_core.handle_registry import known_handles

        return known_handles()
    except Exception:
        data = _load()
        out: list[str] = []
        for entry in data.get("operator_handles") or []:
            if isinstance(entry, str):
                out.append(entry.lstrip("@"))
            elif isinstance(entry, dict) and entry.get("handle"):
                out.append(str(entry["handle"]).lstrip("@"))
        return out


def default_watch_handles() -> list[str]:
    data = _load()
    return [str(h).lstrip("@") for h in (data.get("default_handles") or [])]


def opportunity_watch_handles() -> list[str]:
    """Comptes suivis pour les OPPORTUNITÉS écosystème (ex. @base) — annonces produit /
    standards / grants à évaluer pour ARIA, pas des candidats token."""
    data = _load()
    out: list[str] = []
    for entry in data.get("opportunity_handles") or []:
        if isinstance(entry, str):
            out.append(entry.lstrip("@"))
        elif isinstance(entry, dict) and entry.get("handle"):
            out.append(str(entry["handle"]).lstrip("@"))
    return out


def vc_watch_handles() -> list[str]:
    """Comptes VC crypto reconnus (ex. @a16zcrypto, @paradigm) — thèse/conviction publique,
    jamais une source de vérité en soi (tâche #58)."""
    data = _load()
    out: list[str] = []
    for entry in data.get("vc_handles") or []:
        if isinstance(entry, str):
            out.append(entry.lstrip("@"))
        elif isinstance(entry, dict) and entry.get("handle"):
            out.append(str(entry["handle"]).lstrip("@"))
    return out


def all_curiosity_handles() -> tuple[str, ...]:
    """Deduped handles for passive X curiosity — peers, operator picks, opportunities, VC, defaults."""
    from aria_core.knowledge.zhc_peer_agents import curiosity_handles

    seen: set[str] = set()
    ordered: list[str] = []
    for handle in (
        list(operator_watch_handles())
        + list(curiosity_handles())
        + list(opportunity_watch_handles())
        + list(vc_watch_handles())
        + list(default_watch_handles())
    ):
        key = handle.lstrip("@").lower()
        if key and key not in seen:
            seen.add(key)
            ordered.append(handle.lstrip("@"))
    return tuple(ordered)