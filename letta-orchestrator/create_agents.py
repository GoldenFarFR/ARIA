"""
ARIA v2.4 — Création des 3 agents Letta (idempotent).
"""
from __future__ import annotations

import json
import sys

from aria_config import CONFIG_PATH, LETTA_URL, MODELS_PATH, bridge_api_keys, resolve_models
from letta_api import create_agent, list_agents


def main() -> None:
    if CONFIG_PATH.exists():
        print(f"Agents déjà créés : {CONFIG_PATH}")
        print("Supprime agents_config.json pour recréer.")
        sys.exit(0)

    bridge_api_keys()
    models = resolve_models()
    MODELS_PATH.write_text(json.dumps(models, indent=2), encoding="utf-8")
    print(f"Modèles : {json.dumps(models, ensure_ascii=False)}")

    try:
        known = {a["name"]: a["id"] for a in list_agents()}
    except Exception as exc:
        sys.exit(f"[Erreur Critique] Letta injoignable ({LETTA_URL}) : {exc}")

    agents_spec = (
        ("scout", "ARIA-Scout", models["scout"], "Agent léger : mémoire, tâches simples"),
        ("grok", "ARIA-Grok", models["grok"], "Agent principal : codage et raisonnement"),
        ("core", "ARIA-Core", models["core"], "Agent expert : tâches complexes"),
    )

    config: dict[str, str] = {}
    for key, name, model, desc in agents_spec:
        if name in known:
            config[key] = known[name]
            print(f"Réutilise {name} -> {known[name]}")
            continue
        print(f"Création {name} ({model})...")
        try:
            agent_id = create_agent(name, model, models["embedding"], desc)
        except Exception as exc:
            sys.exit(f"[Erreur] {name} : {exc}")
        config[key] = agent_id
        print(f"  -> {agent_id}")

    CONFIG_PATH.write_text(json.dumps(config, indent=2), encoding="utf-8")
    print(f"\nOK — config : {CONFIG_PATH}")


if __name__ == "__main__":
    main()