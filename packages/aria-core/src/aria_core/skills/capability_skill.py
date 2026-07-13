"""Indice ARIA / niveaux par catégorie — affichage et validation opérateur."""

from __future__ import annotations

import re

from aria_core.capability_levels import (
    CATEGORY_ORDER,
    complete_level,
    format_summary,
    full_status,
    check_auto_completions,
    years_to_max_estimate,
)
from aria_core.locale import LANG_FR
from aria_core.memory import append_memory


def wants_capability(message: str) -> bool:
    """Incident #146 (12/07) : une question personnelle de l'opérateur ("...grâce à ta
    capacité à gérer plus qu'un humain plusieurs choses ?") a été aiguillée vers le
    rapport de progression "Indice ARIA" au lieu de la conversation générale --
    "capacit"/"compétence" matchaient en sous-chaîne libre n'importe quelle phrase
    contenant le mot courant "capacité", sans lien avec une demande de statut. Retirés :
    les ancres restantes (qi, indice aria, niveau/niveaux, level up, score aria,
    progression aria) sont déjà assez spécifiques pour couvrir le vrai cas d'usage
    ("montre ton niveau", "quel est ton indice") sans faux positif sur une phrase
    normale."""
    from aria_core.operator_conversational import wants_capability_improvement
    from aria_core.tweet_compose_workflow import is_tweet_operator_context

    if wants_capability_improvement(message):
        return False
    if is_tweet_operator_context(message):
        return False
    lower = message.lower()
    return bool(
        re.search(
            r"\bqi\b|indice aria|capability|niveau|niveaux|level up|levelup|"
            r"montre.*niveau|score aria|progression aria",
            lower,
        )
    )


def parse_level_command(message: str) -> tuple[str | None, str]:
    """Parse '/level up codage note…' or 'level up intelligence'."""
    lower = message.lower().strip()
    m = re.search(
        r"(?:/level|level)\s+(?:up|complete|valide|validate)\s+"
        r"(codage|social|intelligence|fiabilite|fiabilité|autonomie|business)"
        r"(?:\s+(.+))?",
        lower,
    )
    if not m:
        return None, ""
    cat = m.group(1).replace("fiabilité", "fiabilite")
    note = (m.group(2) or "").strip()
    return cat, note


async def execute_capability(message: str, lang: str = LANG_FR) -> tuple[str, dict]:
    cat, note = parse_level_command(message)
    if cat:
        check_auto_completions()
        result = complete_level(cat, note=note or "validé opérateur")
        if not result.get("ok"):
            err = (
                f"Impossible — {result.get('error', '?')} ({cat})"
                if lang == LANG_FR
                else f"Cannot level up — {result.get('error', '?')} ({cat})"
            )
            return err, result
        append_memory("capability", f"Level up {cat} → {result['new_level']}")
        prefix = (
            f"✅ {cat} → niveau {result['new_level']} | Indice global {result['global_index']}/{1000}\n\n"
            if lang == LANG_FR
            else f"✅ {cat} → level {result['new_level']} | Global index {result['global_index']}/1000\n\n"
        )
        return prefix + format_summary(lang), result

    check_auto_completions()
    status = full_status(lang)
    summary = format_summary(lang)
    if lang == LANG_FR:
        footer = (
            f"\n\n---\nPalier 1000 sur chaque axe ≈ {years_to_max_estimate()} ans de travail cumulé (estimation)."
        )
    else:
        footer = (
            f"\n\n---\nLevel 1000 on each axis ≈ {years_to_max_estimate()} years cumulative work (estimate)."
        )
    return summary + footer, status