"""COLLEGUE.md — mémoire opérateur injectée dans build_llm_context."""
from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path

from aria_core.memory.llm_context import sanitize_recall_text

_COLLEGUE_BUDGET = 2800


def _collegue_candidates() -> list[Path]:
    paths: list[Path] = []
    for env_key in ("COLLEGUE_MEMOIRE_ROOT", "ARIA_REPO_ROOT"):
        raw = (os.environ.get(env_key) or "").strip()
        if raw:
            paths.append(Path(raw) / "collegue-memoire" / "COLLEGUE.md")
            paths.append(Path(raw) / "COLLEGUE.md")
    home = Path.home()
    paths.extend(
        [
            home / "GitHub-Repos" / "ARIA" / "collegue-memoire" / "COLLEGUE.md",
            home / "projets" / "collegue-memoire" / "COLLEGUE.md",
        ]
    )
    seen: set[str] = set()
    out: list[Path] = []
    for p in paths:
        key = str(p.resolve()) if p.exists() else str(p)
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


@lru_cache(maxsize=1)
def get_collegue_text(*, max_chars: int = _COLLEGUE_BUDGET) -> str:
    for path in _collegue_candidates():
        if not path.is_file():
            continue
        try:
            raw = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if not raw:
            continue
        return sanitize_recall_text(raw)[:max_chars]
    return ""


_COLLEGUE_RECALL_RE = re.compile(
    r"(?:"
    r"collegue\.md|mémoire collègue|memoire collegue|"
    r"pr[eé]f[eé]rences?\s+(?:excel|livrables)|preferences?\s+excel|"
    r"que sais[- ]?tu de|qu['']est[- ]?ce que tu sais|"
    r"aptos|ddc|calculateur excel|synth[eè]se.*n[oœ]ud"
    r")",
    re.IGNORECASE,
)


def is_collegue_recall_question(message: str) -> bool:
    return bool(_COLLEGUE_RECALL_RE.search((message or "").strip()))