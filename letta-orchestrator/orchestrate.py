"""
ARIA v2.4 — Orchestrateur Letta : routage hybride + cascade de résilience.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

from aria_config import (
    COMPLEX_HINTS,
    CONFIG_PATH,
    LETTA_URL,
    MODELS_PATH,
    MOYEN_HINTS,
    OLLAMA_CHAT_URL,
    QWEN_CLASSIFIER,
    SIMPLE_HINTS,
    bridge_api_keys,
    resolve_models,
)
from letta_api import send_message

CLASSIFICATION_PROMPT = """Tu es un classificateur de complexité. Sois CONSERVATEUR : la plupart des messages sont simple ou moyen.

Règles :
- simple : salutations, mémoire personnelle, questions courtes, une phrase
- moyen : explication, diagnostic, comparaison, une étape technique
- complexe : refactor multi-fichiers, architecture, migration lourde UNIQUEMENT

Réponds UNIQUEMENT : {{"complexity": "simple"}} ou {{"complexity": "moyen"}} ou {{"complexity": "complexe"}}

Tâche : "{task}"
"""

EMPTY_MARKERS = ("(l'agent n'a renvoyé aucun texte)", "")
ROUTING_MARKER = "ARIA_ROUTING_JSON="

AGENT_KEYS = ("scout", "grok", "core")
AGENT_LABELS = {
    "scout": "ARIA-Scout",
    "grok": "ARIA-Grok",
    "core": "ARIA-Core",
}


@dataclass
class RoutingReport:
    niveau: str
    niveau_source: str
    agent: str
    model: str
    escalades: List[str] = field(default_factory=list)
    attempts: List[dict] = field(default_factory=list)
    success: bool = False
    total_seconds: float = 0.0

    def to_dict(self) -> dict:
        return {
            "niveau": self.niveau,
            "niveau_source": self.niveau_source,
            "agent": self.agent,
            "model": self.model,
            "escalades": self.escalades,
            "attempts": self.attempts,
            "success": self.success,
            "total_seconds": round(self.total_seconds, 2),
        }


def charger_config() -> Dict[str, str]:
    if not CONFIG_PATH.exists():
        sys.exit("[Erreur] agents_config.json absent. Lance create_agents.py.")
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        sys.exit("[Erreur] agents_config.json corrompu.")


def charger_modeles() -> Dict[str, str]:
    if MODELS_PATH.exists():
        try:
            return json.loads(MODELS_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return resolve_models()


def classify_heuristic(task: str) -> Optional[str]:
    t = task.strip().lower()
    if any(h in t for h in COMPLEX_HINTS):
        return "complexe"
    if any(h in t for h in SIMPLE_HINTS) or (
        len(t) < 60 and not any(c in t for c in ("{", "}", "```"))
    ):
        return "simple"
    if any(h in t for h in MOYEN_HINTS):
        return "moyen"
    if len(t) < 20:
        return "simple"
    return None


def extract_json_from_text(text: str) -> dict:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("JSON introuvable")
    blob = text[start : end + 1].strip()
    try:
        return json.loads(blob)
    except json.JSONDecodeError:
        try:
            parsed = ast.literal_eval(blob)
            if isinstance(parsed, dict):
                return parsed
        except (SyntaxError, ValueError):
            pass
        match = re.search(
            r"""['"]?complexity['"]?\s*[:=]\s*['"]?(simple|moyen|complexe)['"]?""",
            blob,
            re.IGNORECASE,
        )
        if match:
            return {"complexity": match.group(1).lower()}
        raise


def classify_with_qwen(task: str) -> str:
    prompt = CLASSIFICATION_PROMPT.replace("{task}", task.replace('"', "'"))
    try:
        resp = requests.post(
            OLLAMA_CHAT_URL,
            json={
                "model": QWEN_CLASSIFIER,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "options": {
                    "temperature": 0.0,
                    "num_ctx": 2048,
                    "num_predict": 32,
                },
            },
            timeout=15,
        )
        resp.raise_for_status()
        content = resp.json()["message"]["content"].strip()
        parsed = extract_json_from_text(content)
        complexity = parsed.get("complexity", "").lower()
        return complexity if complexity in ("simple", "moyen", "complexe") else "moyen"
    except requests.exceptions.ConnectionError:
        print("[Avertissement] Ollama injoignable — routage moyen.", file=sys.stderr)
        return "moyen"
    except Exception as exc:
        print(f"[Avertissement] Classification Qwen ({exc}) — routage moyen.", file=sys.stderr)
        return "moyen"


def fast_mode() -> bool:
    return os.environ.get("ARIA_LETTA_FAST", "").lower() in ("1", "true", "yes", "on")


def should_escalade(
    from_key: str,
    to_key: str,
    models: dict,
    *,
    failed: bool = False,
) -> bool:
    """Cascade si échec, ou si l'agent suivant utilise un modèle différent."""
    if fast_mode():
        return False
    if failed:
        return True
    a, b = models.get(from_key), models.get(to_key)
    return bool(a and b and a != b)


def classify_task(task: str, forced: Optional[str] = None) -> Tuple[str, str]:
    if forced:
        return forced, "forcé"
    quick = classify_heuristic(task)
    if quick:
        return quick, "heuristique"
    if fast_mode():
        return "simple", "fast"
    return classify_with_qwen(task), "qwen"


def envoyer_message(
    agent_id: str,
    message: str,
    agent_key: str,
    model: str,
) -> Tuple[Optional[str], float]:
    agent_name = AGENT_LABELS[agent_key]
    horodatage = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    debut = time.monotonic()
    try:
        texte = send_message(agent_id, message)
        duree = time.monotonic() - debut
        print(f"[Log {horodatage}] {agent_name} | {duree:.2f}s", file=sys.stderr)
        if not texte or texte.lower() in EMPTY_MARKERS:
            return None, duree
        return texte, duree
    except Exception as exc:
        duree = time.monotonic() - debut
        print(f"[Erreur] {agent_name} : {exc}", file=sys.stderr)
        return None, duree


def print_routing_banner(report: RoutingReport, phase: str) -> None:
    esc = " → ".join(report.escalades) if report.escalades else "(aucune)"
    lines = [
        "",
        "═══ ARIA ROUTING ═══",
        f"Phase        : {phase}",
        f"Niveau       : {report.niveau.upper()} ({report.niveau_source})",
        f"Agent        : {report.agent}",
        f"Modèle       : {report.model}",
        f"Escalade     : {esc}",
        f"Durée totale : {report.total_seconds:.2f}s",
        "═══════════════════",
        "",
    ]
    for line in lines:
        print(line, file=sys.stderr)
    print(f"{ROUTING_MARKER}{json.dumps(report.to_dict(), ensure_ascii=False)}", file=sys.stderr)


def router_avec_cascade(
    config: dict,
    models: dict,
    niveau: str,
    niveau_source: str,
    message: str,
) -> RoutingReport:
    debut_total = time.monotonic()
    report = RoutingReport(
        niveau=niveau,
        niveau_source=niveau_source,
        agent="",
        model="",
    )
    reponse: Optional[str] = None
    niveau_courant = niveau

    print_routing_banner(report, "classification")

    if niveau_courant == "simple":
        key = "scout"
        report.agent = AGENT_LABELS[key]
        report.model = models.get(key, "?")
        print(f"[Routage] -> {report.agent}", file=sys.stderr)
        reponse, duree = envoyer_message(config[key], message, key, report.model)
        report.attempts.append({"agent": report.agent, "seconds": round(duree, 2), "ok": bool(reponse)})
        if not reponse and should_escalade("scout", "grok", models, failed=True):
            report.escalades.append(f"{AGENT_LABELS['scout']}→{AGENT_LABELS['grok']}")
            print("[Escalade] Scout -> Grok", file=sys.stderr)
            niveau_courant = "moyen"

    if niveau_courant == "moyen" and not reponse:
        key = "grok"
        report.agent = AGENT_LABELS[key]
        report.model = models.get(key, "?")
        report.niveau = "moyen"
        print(f"[Routage] -> {report.agent}", file=sys.stderr)
        reponse, duree = envoyer_message(config[key], message, key, report.model)
        report.attempts.append({"agent": report.agent, "seconds": round(duree, 2), "ok": bool(reponse)})
        if not reponse and should_escalade("grok", "core", models, failed=True):
            report.escalades.append(f"{AGENT_LABELS['grok']}→{AGENT_LABELS['core']}")
            print("[Escalade] Grok -> Core", file=sys.stderr)
            niveau_courant = "complexe"

    if niveau_courant == "complexe" and not reponse:
        key = "core"
        report.agent = AGENT_LABELS[key]
        report.model = models.get(key, "?")
        report.niveau = "complexe"
        print(f"[Routage] -> {report.agent}", file=sys.stderr)
        reponse, duree = envoyer_message(config[key], message, key, report.model)
        report.attempts.append({"agent": report.agent, "seconds": round(duree, 2), "ok": bool(reponse)})

    tried = {a["agent"] for a in report.attempts}
    if not reponse and AGENT_LABELS["scout"] not in tried:
        key = "scout"
        report.agent = AGENT_LABELS[key]
        report.model = models.get(key, "?")
        report.niveau = "simple"
        print("[Repli] -> ARIA-Scout", file=sys.stderr)
        reponse, duree = envoyer_message(config[key], message, key, report.model)
        report.attempts.append({"agent": report.agent, "seconds": round(duree, 2), "ok": bool(reponse)})

    report.total_seconds = time.monotonic() - debut_total
    report.success = bool(reponse)
    print_routing_banner(report, "résultat")

    if reponse:
        print("\n--- RÉPONSE ---")
        print(reponse)
        print("---------------")
    else:
        print("\n[Échec] Tous les agents ont échoué.", file=sys.stderr)

    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="ARIA Letta — orchestrateur multi-agents")
    parser.add_argument("--niveau", choices=["simple", "moyen", "complexe"])
    parser.add_argument("--message", required=True)
    args = parser.parse_args()

    bridge_api_keys()
    config = charger_config()
    models = charger_modeles()

    niveau, source = classify_task(args.message, args.niveau)
    router_avec_cascade(config, models, niveau, source, args.message)


if __name__ == "__main__":
    main()