"""
Session ouvrier Letta — handoff + inbox + agent ARIA-Ouvrier (copie conforme Cursor).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from aria_config import ARIA_REPO_ROOT
from letta_api import send_message

CONFIG_PATH = ARIA_REPO_ROOT / "letta-orchestrator" / "ouvrier_config.json"
WORKER_PATH = ARIA_REPO_ROOT / "collegue-memoire" / "sessions" / "ARIA-WORKER.md"
_SUBPROC = {"encoding": "utf-8", "errors": "replace", "text": True}
_VAULT_DIR = Path(os.environ.get("LOCALAPPDATA", "")) / "GoldenFar" / "vault"


def _run_ps(script: Path, *extra: str) -> str:
    if not script.is_file():
        return f"(absent: {script.name})"
    proc = subprocess.run(
        ["pwsh", "-NoProfile", "-File", str(script), *extra],
        cwd=str(ARIA_REPO_ROOT),
        capture_output=True,
        timeout=180,
        **_SUBPROC,
    )
    out = (proc.stdout or proc.stderr or "").strip()
    return out[-1500:] if len(out) > 1500 else out


def _patch_env_file(path: Path, key: str, value: str) -> str:
    if not path.is_file():
        lines = [f"{key}={value}\n"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(lines), encoding="utf-8")
        return f"créé {path.name} avec {key}={value}"
    text = path.read_text(encoding="utf-8", errors="replace")
    pat = re.compile(rf"^\s*{re.escape(key)}\s*=.*$", re.MULTILINE)
    line = f"{key}={value}"
    if pat.search(text):
        text = pat.sub(line, text, count=1)
        action = "mis à jour"
    else:
        if not text.endswith("\n"):
            text += "\n"
        text += f"{line}\n"
        action = "ajouté"
    path.write_text(text, encoding="utf-8")
    return f"{path.name}: {key}={value} ({action})"


def preflight_telegram_notifications(message: str) -> str:
    if not re.search(
        r"(?i)(notif|notification|spam|trop|couper|supprim|désactiv|desactiv|moins).{0,40}telegram"
        r"|telegram.{0,40}(notif|spam|trop|couper|supprim|désactiv|desactiv|moins)",
        message,
    ):
        return ""
    actions: list[str] = []
    for name in ("local.env", "production.env"):
        path = _VAULT_DIR / name
        if path.parent.is_dir():
            actions.append(_patch_env_file(path, "ARIA_PROACTIVE_IDEAS", "false"))
    actions.append(
        "Sources code: packages/aria-core/src/aria_core/proactive.py (founder_ping), "
        "heartbeat.py (founder_ping, portfolio_scan), aria_worker_queue.py, capability_gap.py"
    )
    actions.append("Prod Render: redeploy manuel après changement production.env")
    return "PRÉ-TRAITEMENT NOTIFS TELEGRAM (déjà exécuté — confirme à Sylvain, ne redemande pas):\n" + "\n".join(
        f"• {a}" for a in actions
    )


def _pending_worker_count() -> int:
    if not WORKER_PATH.is_file():
        return 0
    return WORKER_PATH.read_text(encoding="utf-8").count("[pending]")


def bootstrap(user_message: str, preflight_block: str) -> str:
    handoff = _run_ps(ARIA_REPO_ROOT / "local-sync" / "scripts" / "session-handoff.ps1")
    inbox = _run_ps(ARIA_REPO_ROOT / "download" / "triage-inbox.ps1")
    pending = _pending_worker_count()
    return f"""Contexte session (déjà synchronisé — ne redemande pas à l'opérateur de lancer quoi que ce soit) :

• Handoff : {handoff[:800]}
• File ouvrier : {pending} élément(s) [pending]
• Boîte download : {inbox}

Déduis l'intention de Sylvain. Exécute toi-même (outils). Pas de commandes à lui dicter.
Interdit de demander « tu veux que je… » si l'intention est claire (ex. couper notifs Telegram → patch_vault_env, pas de question).
Si tu doutes vraiment (prod destructif, secret), une seule question courte.

{preflight_block}

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

    preflight = preflight_telegram_notifications(args.message)
    if preflight and not re.search(r"(?i)^\s*(oui|ok|yes)\s*$", args.message):
        print("═══ ARIA-OUVRIER (direct) ═══", file=sys.stderr)
        print(
            "C'est fait — notifications proactive Telegram coupées.\n\n"
            f"{preflight}\n\n"
            "Si tu reçois encore des messages, ils viennent du backend Render (prod) : "
            "redeploy manuel après le changement production.env."
        )
        return
    preflight_block = preflight if preflight else "(aucun pré-traitement automatique)"
    prompt = bootstrap(args.message, preflight_block)
    print("═══ ARIA-OUVRIER (Letta) ═══", file=sys.stderr)
    reply = send_message(agent_id, prompt)
    if not reply:
        sys.exit("[Erreur] Aucune réponse Letta.")
    print(reply)


if __name__ == "__main__":
    main()