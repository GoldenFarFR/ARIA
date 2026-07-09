"""Curriculum de culture large — géo, macro, écosystème, code, crypto/token.

Aucun produit payant à livrer (ACP abandonné, Stripe retiré) : chaque cycle se termine
par une action concrète liée au track-record VC/trading ou à la veille écosystème,
jamais un app/produit à vendre.
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
        "Comment un cycle macro (taux, liquidité) influence-t-il le sizing des pronostics VC/trading ?",
        "Note l'impact macro sur le prochain cycle de pronostics (weekly_training).",
    ),
    (
        "track_record",
        "Quelle leçon tirer des derniers pronostics résolus (calibration, ratés) ?",
        "Propose une amélioration mesurable du moteur d'analyse — jamais un produit à vendre.",
    ),
    (
        "ecosystem",
        "Quelle tendance/outil de l'écosystème Base mérite d'être étudié cette semaine ?",
        "Résume une inspiration concrète pour la thèse VC (docs/strategie-aria-investissement.md).",
    ),
    (
        "crypto_token",
        "Quelle utility token crédible APRÈS un track-record prouvé (pas hype seul) ?",
        "Lie ce raisonnement au barème du pacte (docs/protocole-argent-reel.md).",
    ),
    (
        "code",
        "Quel outil open-source améliore la qualité du moteur d'analyse cette semaine ?",
        "Ouvre un repo ou une issue GitHub avec scope <3 jours.",
    ),
    (
        "distribution",
        "Quoi partager en public cette semaine sur l'avancée du track-record (X, Telegram) ?",
        "Prépare un thread building-in-public ancré sur des chiffres réels.",
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
            "🌐 Broad cultivation — study → act",
            f"Domain: {domain}",
            "",
            f"Study: {question}",
            "",
            f"Act (mandatory): {ship_action}",
            "",
            "Rule: no study-only cycle — every unit ends with a concrete artefact "
            "(repo, poll, post, or logged decision). No paid product to ship.",
        ]
        return "\n".join(lines)

    lines = [
        "🌐 Culture large — étudier → agir",
        f"Domaine : {domain}",
        "",
        f"Étude : {question}",
        "",
        f"Agir (obligatoire) : {ship_action}",
        "",
        "Règle : pas de cycle théorie seule — chaque unité finit par un artefact concret "
        "(repo, poll, post ou décision loguée). Aucun produit payant à livrer.",
    ]
    return "\n".join(lines)


def mark_ship_completed() -> None:
    """Réinitialise le compteur cycles sans livrable (appelé après vote app ou log revenu)."""
    state = _load_state()
    state["cycles_without_ship"] = 0
    _save_state(state)