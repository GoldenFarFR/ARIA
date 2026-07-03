"""
Crée ou met à jour ARIA-Critique (Letta-2) — agent méta sans outils.
Usage: .\\setup_letta2.py  (Letta :8283)
"""
from __future__ import annotations

import json
import sys

from aria_config import ARIA_REPO_ROOT, bridge_api_keys, resolve_models
from letta_api import create_agent, list_agents, update_agent, update_agent_memory_block

CRITIQUE_NAME = "ARIA-Critique"
CONFIG_PATH = ARIA_REPO_ROOT / "letta-orchestrator" / "letta2_config.json"
PERSONA_PATH = ARIA_REPO_ROOT / "letta-orchestrator" / "letta2_persona.md"
AGENTS_PATH = ARIA_REPO_ROOT / "letta-orchestrator" / "agents_config.json"


def main() -> None:
    bridge_api_keys()
    models = resolve_models()
    persona = PERSONA_PATH.read_text(encoding="utf-8")
    llm = models.get("grok") or models.get("core") or models["scout"]

    known = {a["name"]: a["id"] for a in list_agents()}
    if CRITIQUE_NAME in known:
        agent_id = known[CRITIQUE_NAME]
        print(f"Réutilise {CRITIQUE_NAME} -> {agent_id}")
        update_agent_memory_block(agent_id, "persona", persona)
        update_agent(agent_id, llm)
    else:
        agent_id = create_agent(
            CRITIQUE_NAME,
            llm,
            models["embedding"],
            "ARIA-Critique — méta-cognition, leçons aria-core (Letta-2)",
        )
        update_agent_memory_block(agent_id, "persona", persona)
        print(f"Créé {CRITIQUE_NAME} -> {agent_id}")

    CONFIG_PATH.write_text(json.dumps({"agent_id": agent_id}, indent=2), encoding="utf-8")

    agents_doc: dict = {}
    if AGENTS_PATH.is_file():
        try:
            agents_doc = json.loads(AGENTS_PATH.read_text(encoding="utf-8"))
        except Exception:
            agents_doc = {}
    agents_doc["critique"] = agent_id
    AGENTS_PATH.write_text(json.dumps(agents_doc, indent=2), encoding="utf-8")

    print(f"OK — {CONFIG_PATH}")
    print("Usage: .\\run-letta2-critique.ps1")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        sys.exit(f"[Erreur] {exc} — Lance .\\start-letta.ps1 puis .\\setup_letta2.py")