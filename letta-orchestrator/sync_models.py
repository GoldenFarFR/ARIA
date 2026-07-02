"""Applique models_config.json sur les agents Letta existants (patch LLM)."""
from __future__ import annotations

import json
import sys

from aria_config import CONFIG_PATH, MODELS_PATH, bridge_api_keys, resolve_models
from letta_api import list_agents, update_agent

AGENT_NAMES = {
    "scout": "ARIA-Scout",
    "grok": "ARIA-Grok",
    "core": "ARIA-Core",
}


def main() -> None:
    bridge_api_keys()
    models = resolve_models()
    MODELS_PATH.write_text(json.dumps(models, indent=2), encoding="utf-8")
    print(f"models_config.json → {json.dumps(models, ensure_ascii=False)}")

    if not CONFIG_PATH.exists():
        sys.exit("[Erreur] agents_config.json absent — lance create_agents.py")

    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    by_name = {a["name"]: a for a in list_agents()}

    for key, label in AGENT_NAMES.items():
        agent_id = config.get(key)
        model = models.get(key)
        if not agent_id or not model:
            print(f"[skip] {key}")
            continue
        agent = by_name.get(label, {})
        llm_cfg = agent.get("llm_config") or {}
        current = llm_cfg.get("handle") or f"{llm_cfg.get('model_endpoint_type', '?')}/{llm_cfg.get('model', '?')}"
        if current == model:
            print(f"[ok] {label} déjà sur {model}")
            continue
        print(f"[patch] {label} : {current} → {model}")
        patched = update_agent(agent_id, model)
        got = (patched.get("llm_config") or {}).get("handle") or "?"
        print(f"        confirmé : {got}")

    print("OK — modèles synchronisés (sans 32b local).")


if __name__ == "__main__":
    main()