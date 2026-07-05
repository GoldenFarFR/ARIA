"""Community suggestions → warm ack (public) or Cursor worker queue (operator)."""

from __future__ import annotations

import re
from datetime import datetime, timezone

_WORKER_DELEGATE_RE = re.compile(
    r"\b(?:ouvrier|worker[_ -]?delegate|aria-worker|community_improvement|"
    r"ship\s+confirm|file\s+done|tache\s+done)\b",
    re.IGNORECASE,
)

_COMMUNITY_SUGGESTION_RE = re.compile(
    r"\b(?:suggest(?:ion)?|propos(?:e|ition)?|ajoute(?:r)?|amélior(?:e|er)|amelior(?:e|er)|"
    r"would\s+like|feature\s+request|telegram|bandeau|banner|faq|feedback|avis)\b",
    re.IGNORECASE,
)


def wants_worker_delegate(message: str) -> bool:
    return bool(_WORKER_DELEGATE_RE.search((message or "").strip()))


def is_community_suggestion(message: str) -> bool:
    text = (message or "").strip()
    if len(text) < 12:
        return False
    if wants_worker_delegate(text):
        return False
    return bool(_COMMUNITY_SUGGESTION_RE.search(text))


def is_operator_heavy_structural_task(message: str) -> bool:
    """Operator-only: detect serious structural/cleanup work that should auto-delegate
    to the Cursor worker (ARIA-WORKER) instead of casual chat.

    Examples:
    - "supprime les traces de dexpulse"
    - "nettoie le répertoire des entrées retirées"
    - "purge les mentions de DEXPulse dans les faits canoniques"
    """
    text = (message or "").lower()
    if not text:
        return False

    heavy_verbs = ["supprime", "nettoie", "purge", "efface", "retire", "enlève", "enleve", "clean up", "remove traces"]
    structural = ["trace", "traces", "dexpulse", "répertoire", "repertoire", "canonique", "ssot", "ledger", "entrées", "fichiers", "mentions"]

    if any(v in text for v in heavy_verbs) and any(s in text for s in structural):
        return True

    # "supprime les traces ..." is almost always heavy
    if "supprime" in text and "trace" in text:
        return True

    if "nettoyage" in text and ("dexpulse" in text or "répertoire" in text or "canon" in text):
        return True

    return False


async def execute_worker_delegate(message: str, lang: str = "en") -> tuple[str, dict]:
    from aria_core.aria_worker_queue import enqueue_worker_task, sync_pending_local_tasks_to_md

    clean = (message or "").strip()[:2000]
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    slug = re.sub(r"[^a-z0-9]+", "-", clean.lower())[:24].strip("-") or "task"
    # Detect direct heavy operator structural tasks (e.g. "supprime les traces de dexpulse")
    is_heavy_operator = "supprime" in clean.lower() or "nettoie" in clean.lower() or "purge" in clean.lower()
    prefix = "operator-ouvrier" if is_heavy_operator else "community-ouvrier"
    task_id = f"{prefix}-{slug}-{day}"

    sync_pending_local_tasks_to_md()
    result = await enqueue_worker_task(
        task_id=task_id,
        title=clean[:120] or "Operator task",
        source="operator_direct" if is_heavy_operator else "community_improvement",
        problem=clean or "(non précisé)",
        action=(
            "Implémenter proprement la demande (nettoyage structuré, mises à jour SSOT, archiver les entrées obsolètes, "
            "mettre à jour les templates et logs). Pas de suppression aveugle. Lister les changements, produire commit clair + JOURNAL."
            if is_heavy_operator else
            "Implémenter l'amélioration si elle renforce Vanguard / aria-core / "
            "l'expérience communauté. Tests, journal, preuve (health ou capture)."
        ),
        priority="normal",
        repos=("aria-vanguard", "aria-sandbox"),
        acceptance=(
            "Critères d'acceptation documentés dans la PR ou le commit",
            "JOURNAL.md mis à jour",
        ),
        context="Délégation opérateur direct (tâche structurelle)" if is_heavy_operator else "Délégation opérateur — worker_delegate skill",
        lang=lang,
    )

    status = result.get("status", "unknown")
    if lang == "fr":
        reply = (
            f"File ouvrier Cursor — tâche `{task_id}`.\n"
            "Grok/Cursor traitera `sessions/ARIA-WORKER.md` à la prochaine session."
        )
    else:
        reply = (
            f"Cursor worker — task `{task_id}`.\n"
            "Grok/Cursor will process `sessions/ARIA-WORKER.md` on the next session."
        )
    return reply, {"worker_task_id": task_id, "worker_status": status}