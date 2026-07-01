"""Curriculum d'exposition — questions d'entraînement épistémique pour l'opérateur."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from aria_core.knowledge.calibration_ledger import compute_stats
from aria_core.knowledge.curriculum_cooldown import cooldown_minutes_remaining
from aria_core.paths import data_dir

EXPOSURE_INTERVAL_MINUTES = 1440  # 1× / jour

_CURRICULUM_DOMAINS = [
    ("holding", "Quelle est la relation entre Aria Vanguard ZHC et DEXPulse ?"),
    ("zhc", "Qu'est-ce que le modèle Zero-Human Company en une phrase ?"),
    ("crypto", "Quel launchpad BASE recommande ARIA pour le jeton holding ?"),
    ("fiabilité", "Comment ARIA évite-t-elle d'inventer des revenus ?"),
    ("produit", "Quel est le moat produit de DEXPulse ?"),
    ("autonomie", "Quelle est la priorité autonome #1 d'ARIA cette semaine ?"),
]

_STATE_PATH = data_dir() / "curriculum_state.json"


def _load_state() -> dict:
    if not _STATE_PATH.exists():
        return {"last_index": -1, "last_run": None}
    try:
        return json.loads(_STATE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"last_index": -1, "last_run": None}


def _save_state(state: dict) -> None:
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def generate_curriculum_message(lang: str = "fr") -> str | None:
    state = _load_state()
    wait = cooldown_minutes_remaining(state.get("last_run"), interval_minutes=EXPOSURE_INTERVAL_MINUTES)
    if wait > 0:
        return None

    idx = (int(state.get("last_index", -1)) + 1) % len(_CURRICULUM_DOMAINS)
    domain, question = _CURRICULUM_DOMAINS[idx]
    state["last_index"] = idx
    state["last_run"] = datetime.now(timezone.utc).isoformat()
    _save_state(state)

    stats = compute_stats()
    if lang == "fr":
        lines = [
            "📚 Curriculum épistémique — exposition neuronale",
            f"Domaine : {domain}",
            "",
            f"1. {question}",
        ]
        for i, (_, q) in enumerate(_CURRICULUM_DOMAINS[(idx + 1) % len(_CURRICULUM_DOMAINS):(idx + 3)], 2):
            lines.append(f"{i}. {q}")
        lines.append("")
        lines.append(
            f"Réponds ou utilise /calibrate <affirmation> | vrai|faux | source"
        )
        if stats.get("avg_brier") is not None:
            lines.append(f"Score fiabilité (Brier) : {stats['avg_brier']:.3f}")
        return "\n".join(lines)

    lines = [
        "📚 Epistemic curriculum — neural exposure",
        f"Domain: {domain}",
        "",
        f"1. {question}",
    ]
    for i, (_, q) in enumerate(_CURRICULUM_DOMAINS[(idx + 1) % len(_CURRICULUM_DOMAINS):(idx + 3)], 2):
        lines.append(f"{i}. {q}")
    lines.append("")
    lines.append("Reply or use /calibrate <claim> | true|false | source")
    if stats.get("avg_brier") is not None:
        lines.append(f"Reliability score (Brier): {stats['avg_brier']:.3f}")
    return "\n".join(lines)