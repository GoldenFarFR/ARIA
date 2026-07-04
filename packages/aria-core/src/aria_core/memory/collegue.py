"""COLLEGUE.md — mémoire opérateur injectée dans build_llm_context."""
from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path

from aria_core.memory.llm_context import sanitize_recall_text

_COLLEGUE_BUDGET = 2800


def _ops_memoire_candidates() -> list[Path]:
    paths: list[Path] = []
    for env_key in ("ARIA_OPS_ROOT", "COLLEGUE_MEMOIRE_ROOT", "ARIA_REPO_ROOT"):
        raw = (os.environ.get(env_key) or "").strip()
        if raw:
            paths.append(Path(raw) / "collegue-memoire")
            paths.append(Path(raw))
    home = Path.home()
    paths.extend(
        [
            home / "GitHub-Repos" / "aria-ops" / "collegue-memoire",
            home / "GitHub-Repos" / "ARIA" / "collegue-memoire",
            home / "projets" / "collegue-memoire",
        ]
    )
    seen: set[str] = set()
    out: list[Path] = []
    for p in paths:
        key = str(p)
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def _collegue_candidates() -> list[Path]:
    paths: list[Path] = []
    for root in _ops_memoire_candidates():
        paths.append(root / "COLLEGUE.md")
        if root.name != "collegue-memoire":
            paths.append(root / "collegue-memoire" / "COLLEGUE.md")
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
    r"pr[eé]f[eé]rences?\s+(?:aria|goldenfar|livrables)|"
    r"que sais[- ]?tu de|qu['']est[- ]?ce que tu sais|"
    r"collegue\.md|mémoire collègue"
    r")",
    re.IGNORECASE,
)


def is_collegue_recall_question(message: str) -> bool:
    return bool(_COLLEGUE_RECALL_RE.search((message or "").strip()))


def get_ops_journal_tail(*, lines: int = 3) -> list[str]:
    """Dernières lignes de collegue-memoire/JOURNAL.md (ops operateur)."""
    for root in _ops_memoire_candidates():
        path = root / "JOURNAL.md"
        if not path.is_file():
            continue
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            continue
        non_empty = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        if non_empty:
            return non_empty[-lines:]
    return []