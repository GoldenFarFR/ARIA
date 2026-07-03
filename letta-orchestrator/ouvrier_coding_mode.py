"""Mode débranchement Grok — détection tâches coding (KART unifié)."""
from __future__ import annotations

import re

_CODING_PURE_CMD_RE = re.compile(
    r"(?i)(?:^|\s)(?:"
    r"!débranche|!debranch|/grok-coding|/coding-pure|/debrancher|/grok|mode\s+grok\s+coding"
    r")\b"
)
_CODING_PURE_NATURAL_RE = re.compile(
    r"(?i)\b(?:débranch(?:e|er)|debranch(?:e|er)|mode\s+coding\s+pur|grok\s+build\s+pur)\b"
)
_CODING_TASK_RE = re.compile(
    r"(?i)\b(?:"
    r"cod(?:e|er|age)|refactor|debug(?:ger|ging)?|implément|implement|"
    r"corrige(?:r)?|fix|pytest|commit|push|fichier|script|module|fonction|"
    r"class|endpoint|api\s|write_repo|read_repo|build-local|git\s|patch_vault|"
    r"déploie|deploy|modifie|écris|ecris|exécute|execute"
    r")\b"
)
_STRIP_TRIGGER_RE = re.compile(
    r"(?i)(?:^|\s)(?:"
    r"!débranche|!debranch|/grok-coding|/coding-pure|/debrancher|/grok|mode\s+grok\s+coding"
    r")\s*"
)


def wants_coding_pure(message: str) -> bool:
    """Commande explicite de débranchement (ex. /grok-coding, !débranche)."""
    text = (message or "").strip()
    if not text:
        return False
    return bool(_CODING_PURE_CMD_RE.search(text) or _CODING_PURE_NATURAL_RE.search(text))


def is_coding_task(message: str) -> bool:
    """Tâche code/repo sans commande explicite."""
    return bool(_CODING_TASK_RE.search(message or ""))


def should_debranch(message: str, *, needs_bootstrap: bool = False) -> tuple[bool, bool]:
    """
    Retourne (skip_cerveau, coding_pure).

    coding_pure = prompt Grok Build + Grok-only (pas Groq/Ollama).
    skip_cerveau = ne pas passer par aria_brain avant l'ouvrier.
    """
    explicit = wants_coding_pure(message)
    coding = explicit or is_coding_task(message)
    skip = coding or needs_bootstrap
    return skip, explicit or coding


def strip_coding_triggers(message: str) -> str:
    """Retire les tokens de commande débranchement du message utilisateur."""
    cleaned = _STRIP_TRIGGER_RE.sub(" ", message or "").strip()
    return re.sub(r"\s{2,}", " ", cleaned) or (message or "").strip()