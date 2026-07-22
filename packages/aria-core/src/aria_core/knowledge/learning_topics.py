"""Sujets de recherche pour l'auto-formation continue d'ARIA (macro-économie,
psychologie de trading, documentation/outils) -- SSOT: learning_topics.yaml.

Distinct de x_watchlist.py (comptes X à suivre, personnes/entités) -- ici ce
sont des REQUÊTES de recherche web générale, consommées par
skills/tavily_learning.py. Auto-curation par ARIA (proposition d'ajout via
issue GitHub, même doctrine que knowledge_inbox.py) : DIFFÉRÉE, pas encore
construite -- liste éditée manuellement pour l'instant.
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
    """Liste ordonnée (query, category) -- l'ordre encode la priorité (macro
    en tête, plus d'entrées = plus de passages en round-robin)."""
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
