"""Leçons post-tour ouvrier → reflections aria-core (boucle d'amélioration)."""
from __future__ import annotations

import re

from ouvrier_memory import bootstrap_aria_core_runtime

_CLOUD_FAIL_RE = re.compile(r"(?i)quota|429|cloud indisponible|rate limit")
_ACP_OK_RE = re.compile(r"(?i)workflow .+ (?:créé|supprimé|mis à jour)|offering .+ deleted")
_ERROR_RE = re.compile(r"(?i)^erreur|échec|introuvable sur acp|pas de fallback")


def _append_reflection(content: str, *, context: str, outcome: str) -> None:
    bootstrap_aria_core_runtime()
    try:
        from aria_core.memory.reflection import append_reflection

        append_reflection(content, context=context, outcome=outcome)
    except Exception:
        pass


def maybe_record_lesson(user_message: str, reply: str, *, channel: str = "ouvrier") -> None:
    """Enregistre une leçon si la tournée est notable (échec cloud, ACP, erreur explicite)."""
    user = (user_message or "").strip()
    body = (reply or "").strip()
    if not body or len(body) < 8:
        return

    if _CLOUD_FAIL_RE.search(body):
        _append_reflection(
            f"Groq/cloud KO — message opérateur : {user[:120]}. "
            "Réessayer sans fallback Ollama ; routes déterministes si urgent.",
            context=channel,
            outcome="cloud_quota",
        )
        return

    if _ACP_OK_RE.search(body):
        _append_reflection(
            f"ACP action confirmée : {body[:200]}",
            context="acp",
            outcome="success",
        )
        return

    if _ERROR_RE.search(body) and len(user) > 10:
        _append_reflection(
            f"Échec ouvrier — demande : {user[:120]} · réponse : {body[:200]}",
            context=channel,
            outcome="error",
        )