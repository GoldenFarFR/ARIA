"""Operator directives — persistent instructions from the human principal.

La commande d'origine (/directive, ecriture live) est retiree (10/07) : jamais
utilisee en pratique, doublon du vrai flux (l'operateur demande a Claude Code
d'editer directives.md directement -- revu, teste, commite). get_directives_text()
reste la lecture reelle (doctrine + tout contenu operator.md deja existant),
consommee par grounding.py / memory/llm_context.py / memory/arbitrator.py.
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

    # Les directives operateur VIVANTES (recentes, /directive) priment sur la doctrine
    # statique : jamais coupees, meme partiellement. Bug reel trouve le 10/07 -- une
    # fois directives.md > limit, [:limit] sur la chaine concatenee coupait AVANT (ou
    # EN PLEIN MILIEU DE) la section live -> /directive devenait silencieusement sans
    # effet sur ARIA. Aucune troncature finale sur le resultat joint : seul le builtin
    # est raccourci, le separateur et le bloc live sont toujours preserves intacts.
    if len(live_block) >= limit:
        return live_block[:limit]
    separator = "\n" if builtin_text and live_block else ""
    remaining_for_builtin = limit - len(live_block) - len(separator)
    return builtin_text[:remaining_for_builtin] + separator + live_block