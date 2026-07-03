"""
Session ouvrier Letta — handoff + inbox + agent ARIA-Ouvrier (copie conforme Cursor).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from aria_config import ARIA_REPO_ROOT
from letta_api import send_message

CONFIG_PATH = ARIA_REPO_ROOT / "letta-orchestrator" / "ouvrier_config.json"
WORKER_PATH = ARIA_REPO_ROOT / "collegue-memoire" / "sessions" / "ARIA-WORKER.md"


def _run_ps(script: Path, *extra: str) -> str:
    if not script.is_file():
        return f"(absent: {script.name})"
    proc = subprocess.run(
        ["pwsh", "-NoProfile", "-File", str(script), *extra],
        cwd=str(ARIA_REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=180,
    )
    out = (proc.stdout or proc.stderr or "").strip()
    return out[-1500:] if len(out) > 1500 else out


def _pending_worker_count() -> int:
    if not WORKER_PATH.is_file():
        return 0
    return WORKER_PATH.read_text(encoding="utf-8").count("[pending]")


def bootstrap(user_message: str) -> str:
    handoff = _run_ps(ARIA_REPO_ROOT / "local-sync" / "scripts" / "session-handoff.ps1")
    inbox = _run_ps(ARIA_REPO_ROOT / "download" / "triage-inbox.ps1")
    pending = _pending_worker_count()
    return f"""Contexte session (déjà synchronisé — ne redemande pas à l'opérateur de lancer quoi que ce soit) :

• Handoff : {handoff[:800]}
• File ouvrier : {pending} élément(s) [pending]
• Boîte download : {inbox}

Déduis l'intention de Sylvain. Exécute toi-même. Pas de commandes à lui dicter.
Si tu doutes, pose une question de confirmation courte avant d'agir.

Sylvain :
{user_message}
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="ARIA-Ouvrier via Letta")
    parser.add_argument("--message", required=True)
    args = parser.parse_args()

    if not CONFIG_PATH.is_file():
        sys.exit(
            "[Erreur] ouvrier_config.json absent. Lance: .\\setup_ouvrier.py"
        )
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    agent_id = cfg["agent_id"]

    prompt = bootstrap(args.message)
    print("═══ ARIA-OUVRIER (Letta) ═══", file=sys.stderr)
    reply = send_message(agent_id, prompt)
    if not reply:
        sys.exit("[Erreur] Aucune réponse Letta.")
    print(reply)


if __name__ == "__main__":
    main()