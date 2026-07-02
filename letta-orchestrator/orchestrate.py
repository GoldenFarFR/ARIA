"""
ARIA v2.4 — Orchestrateur Letta : routage hybride + cascade de résilience.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

import requests

from aria_config import (
    COMPLEX_HINTS,
    CONFIG_PATH,
    LETTA_URL,
    OLLAMA_CHAT_URL,
    QWEN_CLASSIFIER,
    bridge_api_keys,
)
from letta_api import send_message

CLASSIFICATION_PROMPT = """Tu es un classificateur de complexité de tâches.
Classe la tâche dans EXACTEMENT une des trois catégories : simple, moyen ou complexe.
Réponds UNIQUEMENT avec ce JSON :
{{"complexity": "simple"}} ou {{"complexity": "moyen"}} ou {{"complexity": "complexe"}}

Tâche : "{task}"
"""

EMPTY_MARKERS = ("(l'agent n'a renvoyé aucun texte)", "")


def charger_config() -> Dict[str, str]:
    if not CONFIG_PATH.exists():
        sys.exit("[Erreur] agents_config.json absent. Lance create_agents.py.")
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        sys.exit("[Erreur] agents_config.json corrompu.")


def classify_heuristic(task: str) -> Optional[str]:
    t = task.strip().lower()
    if len(t) < 15 and not any(c in t for c in ("{", "}", "```")):
        return "simple"
    if any(h in t for h in COMPLEX_HINTS):
        return "complexe"
    return None


def extract_json_from_text(text: str) -> dict:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("JSON introuvable")
    return json.loads(text[start : end + 1])


def classify_with_qwen(task: str) -> str:
    prompt = CLASSIFICATION_PROMPT.replace("{task}", task.replace('"', "'"))
    try:
        resp = requests.post(
            OLLAMA_CHAT_URL,
            json={
                "model": QWEN_CLASSIFIER,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "options": {"temperature": 0.0},
            },
            timeout=30,
        )
        resp.raise_for_status()
        content = resp.json()["message"]["content"].strip()
        parsed = extract_json_from_text(content)
        complexity = parsed.get("complexity", "").lower()
        return complexity if complexity in ("simple", "moyen", "complexe") else "moyen"
    except requests.exceptions.ConnectionError:
        print("[Avertissement] Ollama injoignable — routage moyen.")
        return "moyen"
    except Exception as exc:
        print(f"[Avertissement] Classification Qwen ({exc}) — routage moyen.")
        return "moyen"


def classify_task(task: str) -> str:
    quick = classify_heuristic(task)
    return quick if quick else classify_with_qwen(task)


def envoyer_message(agent_id: str, message: str, agent_name: str) -> Optional[str]:
    horodatage = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    debut = time.monotonic()
    try:
        texte = send_message(agent_id, message)
        duree = time.monotonic() - debut
        print(f"[Log {horodatage}] {agent_name} | {duree:.2f}s")
        if not texte or texte.lower() in EMPTY_MARKERS:
            return None
        return texte
    except Exception as exc:
        print(f"[Erreur] {agent_name} : {exc}")
        return None


def router_avec_cascade(config: dict, niveau: str, message: str) -> None:
    reponse: Optional[str] = None

    if niveau == "simple":
        print("[Routage] -> ARIA-Scout")
        reponse = envoyer_message(config["scout"], message, "ARIA-Scout")
        if not reponse:
            print("[Escalade] Scout -> Grok")
            niveau = "moyen"

    if niveau == "moyen":
        print("[Routage] -> ARIA-Grok")
        reponse = envoyer_message(config["grok"], message, "ARIA-Grok")
        if not reponse:
            print("[Escalade] Grok -> Core")
            niveau = "complexe"

    if niveau == "complexe":
        print("[Routage] -> ARIA-Core")
        reponse = envoyer_message(config["core"], message, "ARIA-Core")

    if reponse:
        print("\n--- RÉPONSE ---")
        print(reponse)
        print("---------------")
    else:
        print("\n[Échec] Tous les agents ont échoué.")


def main() -> None:
    parser = argparse.ArgumentParser(description="ARIA Letta — orchestrateur multi-agents")
    parser.add_argument("--niveau", choices=["simple", "moyen", "complexe"])
    parser.add_argument("--message", required=True)
    args = parser.parse_args()

    bridge_api_keys()
    config = charger_config()

    niveau = args.niveau or classify_task(args.message)
    if not args.niveau:
        print(f"[Classification] {niveau.upper()}")

    router_avec_cascade(config, niveau, args.message)


if __name__ == "__main__":
    main()