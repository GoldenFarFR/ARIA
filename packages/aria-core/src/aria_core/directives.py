"""Operator directives — persistent instructions from the human principal."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from aria_core.paths import data_dir

_BUILTIN_DIRECTIVES = Path(__file__).parent / "directives.md"


def operator_directives_path() -> Path:
    path = data_dir() / "directives" / "operator.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        if _BUILTIN_DIRECTIVES.exists():
            path.write_text(_BUILTIN_DIRECTIVES.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            path.write_text("# Operator directives\n\n", encoding="utf-8")
    return path


def get_directives_text(limit: int = 4000) -> str:
    import re

    parts: list[str] = []
    if _BUILTIN_DIRECTIVES.exists():
        parts.append(_BUILTIN_DIRECTIVES.read_text(encoding="utf-8"))
    op = operator_directives_path()
    if op.exists():
        text = op.read_text(encoding="utf-8")
        live = re.findall(r"(## \[[^\]]+\][\s\S]*?)(?=\n## \[|\Z)", text)
        if live:
            parts.append("\n# Live operator directives\n" + "\n".join(s.strip() for s in live))
    return "\n".join(parts)[:limit]


def append_directive(text: str) -> str:
    text = text.strip()
    if not text:
        return ""
    path = operator_directives_path()
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    entry = f"\n\n## [{ts}]\n{text}\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(entry)
    return entry.strip()