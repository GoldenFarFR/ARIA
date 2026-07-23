"""Operator directives — persistent instructions from the human principal.

The original command (/directive, live write) was removed (10/07): never used
in practice, a duplicate of the real flow (the operator asks Claude Code to
edit directives.md directly -- reviewed, tested, committed). get_directives_text()
remains the real read path (doctrine + any pre-existing operator.md content),
consumed by grounding.py / memory/llm_context.py / memory/arbitrator.py.
"""

from __future__ import annotations

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

    op = operator_directives_path()
    live_block = ""
    if op.exists():
        text = op.read_text(encoding="utf-8")
        live = re.findall(r"(## \[[^\]]+\][\s\S]*?)(?=\n## \[|\Z)", text)
        if live:
            live_block = "\n# Live operator directives\n" + "\n".join(s.strip() for s in live)

    builtin_text = _BUILTIN_DIRECTIVES.read_text(encoding="utf-8") if _BUILTIN_DIRECTIVES.exists() else ""

    # LIVE operator directives (recent, /directive) take priority over the
    # static doctrine: never truncated, not even partially. Real bug found on
    # 10/07 -- once directives.md > limit, [:limit] on the concatenated string
    # would cut BEFORE (or IN THE MIDDLE OF) the live section -> /directive
    # silently had no effect on ARIA. No final truncation on the joined
    # result: only the builtin part is shortened, the separator and the live
    # block are always preserved intact.
    if len(live_block) >= limit:
        return live_block[:limit]
    separator = "\n" if builtin_text and live_block else ""
    remaining_for_builtin = limit - len(live_block) - len(separator)
    return builtin_text[:remaining_for_builtin] + separator + live_block