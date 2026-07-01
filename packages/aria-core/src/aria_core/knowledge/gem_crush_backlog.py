"""Gem Crush backlog SSOT — gem_crush_backlog.yaml."""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_BACKLOG_PATH = Path(__file__).with_name("gem_crush_backlog.yaml")


@dataclass(frozen=True)
class BacklogItem:
    id: str
    title: str
    axis: str
    owner: str
    status: str
    priority: int
    acceptance: tuple[str, ...]
    detect_tsx: tuple[str, ...] = ()
    detect_css: tuple[str, ...] = ()


@lru_cache(maxsize=1)
def _load_raw() -> dict[str, Any]:
    if not _BACKLOG_PATH.exists():
        return {}
    return yaml.safe_load(_BACKLOG_PATH.read_text(encoding="utf-8")) or {}


def backlog_items() -> tuple[BacklogItem, ...]:
    data = _load_raw()
    out: list[BacklogItem] = []
    for raw in data.get("items") or []:
        if not isinstance(raw, dict) or not raw.get("id"):
            continue
        detect = raw.get("detect") or {}
        out.append(
            BacklogItem(
                id=str(raw["id"]),
                title=str(raw.get("title") or raw["id"]),
                axis=str(raw.get("axis") or "polish"),
                owner=str(raw.get("owner") or "aria"),
                status=str(raw.get("status") or "pending"),
                priority=int(raw.get("priority") or 50),
                acceptance=tuple(str(a) for a in (raw.get("acceptance") or [])),
                detect_tsx=tuple(str(s) for s in (detect.get("tsx") or [])),
                detect_css=tuple(str(s) for s in (detect.get("css") or [])),
            )
        )
    return tuple(out)


def backlog_axes() -> dict[str, str]:
    data = _load_raw()
    axes = data.get("axes") or {}
    return {str(k): str(v) for k, v in axes.items()}


def pending_items_for_aria() -> tuple[BacklogItem, ...]:
    """Items non terminés assignés à aria ou shared."""
    return tuple(
        item
        for item in backlog_items()
        if item.status not in ("done", "completed")
        and item.owner in ("aria", "shared")
    )