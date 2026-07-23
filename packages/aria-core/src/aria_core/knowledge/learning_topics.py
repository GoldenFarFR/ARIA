"""Research topics for ARIA's continuous self-training (macro-economics,
trading psychology, documentation/tools) -- SSOT: learning_topics.yaml.

Distinct from x_watchlist.py (X accounts to follow, people/entities) -- these
are general web search QUERIES, consumed by skills/tavily_learning.py.
Self-curation by ARIA (proposing additions via a GitHub issue, same doctrine
as knowledge_inbox.py): DEFERRED, not yet built -- list edited manually for
now.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_TOPICS_PATH = Path(__file__).with_name("learning_topics.yaml")


@lru_cache(maxsize=1)
def _load() -> dict[str, Any]:
    if not _TOPICS_PATH.exists():
        return {}
    return yaml.safe_load(_TOPICS_PATH.read_text(encoding="utf-8")) or {}


def all_learning_topics() -> list[dict[str, str]]:
    """Ordered list (query, category) -- the order encodes priority (macro
    first, more entries = more round-robin passes)."""
    data = _load()
    out: list[dict[str, str]] = []
    for entry in data.get("topics") or []:
        if not isinstance(entry, dict):
            continue
        query = str(entry.get("query") or "").strip()
        if not query:
            continue
        out.append({"query": query, "category": str(entry.get("category") or "general").strip()})
    return out
