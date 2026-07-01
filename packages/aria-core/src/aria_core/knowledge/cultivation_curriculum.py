"""Curriculum de culture large — géo, macro, produit, code, crypto/token.

Chaque cycle se termine par une action ship (Kelly model), pas de théorie seule.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from aria_core.knowledge.curriculum_cooldown import cooldown_minutes_remaining
from aria_core.paths import data_dir

CULTIVATION_INTERVAL_MINUTES = 1440  # 1× / jour

_CULTIVATION_DOMAINS: list[tuple[str, str, str]] = [
    (
        "géopolitique",
        "Quel risque géopolitique pourrait impacter un launch token crypto cette année ?",
        "Synthétise en 3 bullets → propose une narrative X ou une section FAQ holding.",
    ),
    (
        "régulation",
        "MiCA / SEC : quelle contrainte est la plus critique pour une app crypto EU ?",
        "Liste 2 garde-fous produit → note dans truth-ledger ou mémoire entrepreneur.",
    ),
    (
        "macro",
        "Comment un cycle macro (taux, liquidité) influence-t-il les revenus d'une micro-app ?",
        "Choisis un pricing (freemium vs one-shot) pour la prochaine app.",
    ),
    (
        "product_studio",
        "Quelle leçon du modèle Kelly Claude (ship apps payantes) appliquer cette semaine ?",
        "Propose 1 micro-app livrable en <7 jours (web ou Play Store).",
    ),
    (
        "play_store",
        "Play Store : compte dev Google = 25 $ unique — quelle app utilitaire vaut ce coût ?",
        "Décris v0 Android (1 écran, 1 valeur) + stack (Kotlin / React Native).",
    ),
    (
        "crypto_token",
        "Quelle utility token crédible APRÈS un produit payant (pas hype seul) ?",
        "Lie 1 feature app existante à une future utility token.",
    ),
    (
        "code",
        "Quel outil open-source accélère le ship d'une app Android ou web cette semaine ?",
        "Ouvre un repo ou une issue GitHub avec scope <3 jours.",
    ),
    (
        "distribution",
        "Comment faire voter l'audience sur la prochaine app (X, Telegram) ?",
        "Prépare un poll 3 idées ou un thread building-in-public.",
    ),
]

_STATE_PATH = data_dir() / "cultivation_curriculum_state.json"


def _load_state() -> dict:
    if not _STATE_PATH.exists():
        return {"last_index": -1, "last_run": None, "cycles_without_ship": 0}
    try:
        return json.loads(_STATE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"last_index": -1, "last_run": None, "cycles_without_ship": 0}


def _save_state(state: dict) -> None:
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def generate_cultivation_message(lang: str = "fr") -> str | None:
    state = _load_state()
    wait = cooldown_minutes_remaining(state.get("last_run"), interval_minutes=CULTIVATION_INTERVAL_MINUTES)
    if wait > 0:
        return None

    idx = (int(state.get("last_index", -1)) + 1) % len(_CULTIVATION_DOMAINS)
    domain, question, ship_action = _CULTIVATION_DOMAINS[idx]
    state["last_index"] = idx
    state["last_run"] = datetime.now(timezone.utc).isoformat()
    state["cycles_without_ship"] = int(state.get("cycles_without_ship", 0)) + 1
    _save_state(state)

    if lang == "en":
        lines = [
            "🌐 Broad cultivation — study → ship (Kelly model)",
            f"Domain: {domain}",
            "",
            f"Study: {question}",
            "",
            f"Ship (mandatory): {ship_action}",
            "",
            "Rule: no study-only cycle — every unit ends with a deliverable "
            "(app, repo, poll, post, or logged decision).",
            "Play Store: Google developer account = $25 one-time (operator).",
        ]
        return "\n".join(lines)

    lines = [
        "🌐 Culture large — étudier → livrer (modèle Kelly)",
        f"Domaine : {domain}",
        "",
        f"Étude : {question}",
        "",
        f"Livrer (obligatoire) : {ship_action}",
        "",
        "Règle : pas de cycle théorie seule — chaque unité finit par un livrable "
        "(app, repo, poll, post ou décision loguée).",
        "Play Store : compte développeur Google = 25 $ unique (opérateur).",
    ]
    return "\n".join(lines)


def mark_ship_completed() -> None:
    """Réinitialise le compteur cycles sans livrable (appelé après vote app ou log revenu)."""
    state = _load_state()
    state["cycles_without_ship"] = 0
    _save_state(state)