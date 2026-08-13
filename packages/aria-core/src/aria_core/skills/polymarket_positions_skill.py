"""Natural-language detector for the Polymarket paper-trading portfolio.

13/08 fix: free text like "polymarket" or "je veux voir les paris en cours"
matched no ``brain.INTENT_PATTERNS`` regex (``ANALYZE_PORTFOLIO`` only matches
the bare word "positions?", never "polymarket"/"paris") and fell through to
the paid web-search fallback, returning an unrelated FanDuel/DraftKings/
Wikipedia answer at real cost. See ``brain._try_polymarket_positions_response``
for the deterministic, zero-LLM handler wired ahead of that fallback.
"""
from __future__ import annotations

import re

_POLYMARKET_RE = re.compile(
    r"polymarket|paris?\s+(?:en\s+cours|ouverts?|possibles?)",
    re.IGNORECASE,
)


def wants_polymarket_positions(message: str) -> bool:
    return bool(_POLYMARKET_RE.search((message or "").strip()))
