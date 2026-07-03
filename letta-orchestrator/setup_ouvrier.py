"""
Crée ou met à jour ARIA-Ouvrier (copie conforme Grok/Cursor) + outils Letta.
Usage: .\\setup_ouvrier.py  (Letta server doit tourner sur :8283)
"""
from __future__ import annotations

import json
import sys

from aria_config import ARIA_REPO_ROOT, bridge_api_keys, resolve_models
from letta_api import (
    add_tool_to_agent,
    create_ouvrier_agent,
    list_agents,
    update_agent_memory_block,
    upsert_tool,
)
from ouvrier_tool_sources import TOOL_SOURCES

OUVRIER_NAME = "ARIA-Ouvrier"
CONFIG_PATH = ARIA_REPO_ROOT / "letta-orchestrator" / "ouvrier_config.json"
PERSONA_PATH = ARIA_REPO_ROOT / "letta-orchestrator" / "ouvrier_persona.md"


def main() -> None:
    bridge_api_keys()
    models = resolve_models()
    persona = PERSONA_PATH.read_text(encoding="utf-8")

    tool_ids: dict[str, str] = {}
    for spec in TOOL_SOURCES:
        tid = upsert_tool(spec["name"], spec["source_code"], spec["description"])
        tool_ids[spec["name"]] = tid
        print(f"tool {spec['name']} -> {tid}")

    known = {a["name"]: a["id"] for a in list_agents()}
    if OUVRIER_NAME in known:
        agent_id = known[OUVRIER_NAME]
        print(f"Réutilise {OUVRIER_NAME} -> {agent_id}")
        update_agent_memory_block(agent_id, "persona", persona)
        print("Persona ouvrier mise à jour")
    else:
        llm = models.get("core") or models.get("grok") or models["scout"]
        agent_id = create_ouvrier_agent(
            OUVRIER_NAME,
            llm,
            models["embedding"],
            persona,
            list(tool_ids.values()),
        )
        print(f"Créé {OUVRIER_NAME} -> {agent_id}")

    for tid in tool_ids.values():
        try:
            add_tool_to_agent(agent_id, tid)
        except Exception as exc:
            if "already" not in str(exc).lower():
                print(f"[warn] add-tool {tid}: {exc}")

    CONFIG_PATH.write_text(
        json.dumps({"agent_id": agent_id, "tools": tool_ids}, indent=2),
        encoding="utf-8",
    )
    print(f"\nOK — {CONFIG_PATH}")
    print('Usage: .\\orchestrate-ouvrier.ps1 -Message "ce que tu veux, en français naturel"')


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        sys.exit(f"[Erreur] {exc} — Lance .\\start-letta.ps1 puis réessaie.")