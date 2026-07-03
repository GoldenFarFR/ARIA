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

import requests

from aria_config import ARIA_REPO_ROOT, bridge_api_keys
from ouvrier_runner import provider_label, run_ouvrier
from ouvrier_trace import StepTimer, is_verbose, set_verbose, trace, trace_block

CONFIG_PATH = ARIA_REPO_ROOT / "letta-orchestrator" / "ouvrier_config.json"
WORKER_PATH = ARIA_REPO_ROOT / "collegue-memoire" / "sessions" / "ARIA-WORKER.md"
_SUBPROC = {"encoding": "utf-8", "errors": "replace", "text": True}
_VAULT_DIR = Path(os.environ.get("LOCALAPPDATA", "")) / "GoldenFar" / "vault"
_LOCAL_START = ARIA_REPO_ROOT / "vanguard" / "operator" / "start-acp-local.ps1"


def _vault_env_names() -> list[str]:
    """PC local SSOT : local.env seulement (Sylvain 2026-07 — plus Render)."""
    runtime = os.environ.get("ARIA_RUNTIME", "").strip().lower()
    if not runtime:
        local_path = _VAULT_DIR / "local.env"
        if local_path.is_file():
            m = re.search(
                r"(?m)^\s*ARIA_RUNTIME\s*=\s*(\S+)",
                local_path.read_text(encoding="utf-8", errors="replace"),
            )
            runtime = (m.group(1) if m else "local").strip().lower()
        else:
            runtime = "local"
    if runtime in ("local", "pc", "desktop"):
        return ["local.env"]
    return ["local.env", "production.env"]


def _local_api_status() -> str:
    import socket

    try:
        with socket.create_connection(("127.0.0.1", 8000), timeout=1.5):
            return "✓ Bot/API local :8000 actif"
    except OSError:
        return f"✗ Bot/API local arrêté — lance : {_LOCAL_START}"


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


def _verify_env_key(key: str) -> str:
    """Preuve disque — relit le vault après patch (pas de confiance aveugle)."""
    lines = ["PREUVE (lecture disque vault) :", _local_api_status(), ""]
    for name in _vault_env_names():
        path = _VAULT_DIR / name
        if not path.is_file():
            lines.append(f"  ✗ {name} — fichier absent")
            continue
        found = ""
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if raw.strip().startswith(f"{key}="):
                found = raw.strip()
                break
        if found:
            lines.append(f"  ✓ {path} → {found}")
        else:
            lines.append(f"  ✗ {name} — {key} absent")
    return "\n".join(lines)


def preflight_telegram_notifications(message: str) -> str:
    action = r"(?:supprim|couper|désactiv|desactiv|éteind|eteind|arrêt|arret|stop|moins|trop|spam)"
    if not re.search(
        rf"(?i){action}.{{0,50}}(?:notif|notification).{{0,50}}telegram"
        rf"|telegram.{{0,50}}(?:notif|notification).{{0,50}}{action}"
        rf"|(?:notif|notification).{{0,30}}{action}",
        message,
    ):
        return ""
    actions: list[str] = [_local_api_status()]
    for name in _vault_env_names():
        path = _VAULT_DIR / name
        if path.parent.is_dir():
            actions.append(_patch_env_file(path, "ARIA_PROACTIVE_IDEAS", "false"))
    actions.append(
        "Sources : proactive.py (founder_ping), heartbeat.py, aria_worker_queue, capability_gap"
    )
    actions.append(_verify_env_key("ARIA_PROACTIVE_IDEAS"))
    return "PRÉ-TRAITEMENT NOTIFS TELEGRAM (déjà exécuté — confirme à Sylvain, ne redemande pas):\n" + "\n".join(
        f"• {a}" if not a.startswith("PREUVE") else a for a in actions
    )


def preflight_telegram_activate(message: str) -> str:
    enable = r"(?:activ|allum|réactiv|reactiv|remet|enable|on\b)"
    if not re.search(
        rf"(?i){enable}.{{0,50}}(?:notif|notification).{{0,50}}telegram"
        rf"|telegram.{{0,50}}(?:notif|notification).{{0,50}}{enable}"
        rf"|(?:notif|notification).{{0,30}}{enable}",
        message,
    ):
        return ""
    actions: list[str] = [_local_api_status()]
    for name in _vault_env_names():
        path = _VAULT_DIR / name
        if path.parent.is_dir():
            actions.append(_patch_env_file(path, "ARIA_PROACTIVE_IDEAS", "true"))
    actions.append("Clé SSOT : ARIA_PROACTIVE_IDEAS (local.env — runtime PC)")
    actions.append(_verify_env_key("ARIA_PROACTIVE_IDEAS"))
    return "PRÉ-TRAITEMENT ACTIVATION NOTIFS (déjà exécuté):\n" + "\n".join(
        f"• {a}" if not a.startswith("PREUVE") else a for a in actions
    )


def _read_vault_kv(key: str) -> str:
    for name in ("local.env", "production.env"):
        path = _VAULT_DIR / name
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.strip().startswith(f"{key}="):
                return line.split("=", 1)[1].strip().strip('"')
    return ""


def preflight_notification_status(message: str) -> str:
    if not re.search(
        r"(?i)(quel|quoi|liste|état|etat|encore).{0,30}(notif|notification)"
        r"|(notif|notification).{0,30}(active|actif|encore|reste)",
        message,
    ):
        return ""
    lines = ["État notifications Telegram ARIA (runtime PC local) :", _local_api_status(), ""]
    for name in _vault_env_names():
        val = ""
        path = _VAULT_DIR / name
        if path.is_file():
            m = re.search(r"(?m)^\s*ARIA_PROACTIVE_IDEAS\s*=\s*(\S+)", path.read_text(encoding="utf-8", errors="replace"))
            val = m.group(1) if m else "(absent)"
        lines.append(f"• {name} → ARIA_PROACTIVE_IDEAS={val}")
    lines.extend(
        [
            "• founder_ping (heartbeat) : actif seulement si ARIA_PROACTIVE_IDEAS=true + LLM + bot",
            "• aria_worker_queue / capability_gap : notify_admin (si worker actif)",
            "• portfolio_scan heartbeat : notif si items portfolio > 0",
            "",
            _verify_env_key("ARIA_PROACTIVE_IDEAS"),
        ]
    )
    return "\n".join(lines)


def preflight_preuve(message: str) -> str:
    if not re.search(
        r"(?i)(preuve|prouve|vérif|verif|confirme|sois?\s+sûr|sur\s+que|sans\s+preuve|lecture\s+disque)",
        message,
    ):
        return ""
    return _verify_env_key("ARIA_PROACTIVE_IDEAS") + (
        "\n\nCommande manuelle :\n"
        f'  Select-String -Path "{_VAULT_DIR}\\local.env" -Pattern ARIA_PROACTIVE_IDEAS'
    )


def preflight_telegram_ping(message: str) -> str:
    if not re.search(r"(?i)ping|confirmation|confirme", message):
        return ""
    if not re.search(r"(?i)telegram|bot|aria", message) and "ping" not in message.lower():
        return ""
    bridge_api_keys()
    token = _read_vault_kv("TELEGRAM_BOT_TOKEN")
    admin = _read_vault_kv("TELEGRAM_ADMIN_IDS") or _read_vault_kv("TELEGRAM_GROUP_ID")
    if not token or not admin:
        return "ERREUR: TELEGRAM_BOT_TOKEN ou TELEGRAM_ADMIN_IDS absent du vault."
    text = "✅ Ping confirmation ARIA-Ouvrier — connexion Telegram OK."
    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": int(admin.split(",")[0].strip()), "text": text},
        timeout=30,
    )
    if r.status_code != 200:
        return f"ERREUR Telegram API {r.status_code}: {r.text[:300]}"
    return f"Ping envoyé sur Telegram (chat {admin.split(',')[0].strip()}).\n{text}"


def _pending_worker_count() -> int:
    if not WORKER_PATH.is_file():
        return 0
    return WORKER_PATH.read_text(encoding="utf-8").count("[pending]")


def bootstrap(user_message: str, preflight_block: str) -> str:
    with StepTimer("session-handoff.ps1"):
        handoff = _run_ps(ARIA_REPO_ROOT / "local-sync" / "scripts" / "session-handoff.ps1")
    trace_block("bootstrap", "handoff", handoff, max_lines=6)
    with StepTimer("triage-inbox.ps1"):
        inbox = _run_ps(ARIA_REPO_ROOT / "download" / "triage-inbox.ps1")
    trace_block("bootstrap", "download", inbox, max_lines=4)
    pending = _pending_worker_count()
    trace("bootstrap", f"ARIA-WORKER [pending] = {pending}")
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
    parser = argparse.ArgumentParser(description="ARIA-Ouvrier direct")
    parser.add_argument("--message", required=True)
    parser.add_argument("--verbose", "-v", action="store_true", help="Afficher raisonnement et outils")
    parser.add_argument("--quiet", "-q", action="store_true", help="Masquer la trace")
    args = parser.parse_args()

    if args.quiet:
        set_verbose(False)
    elif args.verbose:
        set_verbose(True)

    if not CONFIG_PATH.is_file():
        sys.exit(
            "[Erreur] ouvrier_config.json absent. Lance: .\\setup_ouvrier.py"
        )
    trace("pensee", f"Message Sylvain : {args.message[:300]}")
    handlers = (
        ("preuve", preflight_preuve),
        ("mute", preflight_telegram_notifications),
        ("enable", preflight_telegram_activate),
        ("status", preflight_notification_status),
        ("ping", preflight_telegram_ping),
    )
    for tag, handler in handlers:
        with StepTimer(f"preflight {tag}"):
            direct = handler(args.message)
        if direct:
            trace_block("preflight", tag, direct, max_lines=10)
        if direct and not re.search(r"(?i)^\s*(oui|ok|yes)\s*$", args.message):
            print(f"--- ARIA-OUVRIER ({tag}) ---", file=sys.stderr)
            summaries = {
                "mute": "C'est fait — notifications proactive Telegram coupées (ARIA_PROACTIVE_IDEAS=false).",
                "enable": "C'est fait — notifications proactive Telegram activées (ARIA_PROACTIVE_IDEAS=true).",
                "status": direct.splitlines()[0] if direct else "État notifications.",
                "ping": direct.splitlines()[0] if direct else "Ping envoyé.",
                "preuve": "Preuve vault ci-dessous (lecture disque).",
            }
            summary = summaries.get(tag, direct.splitlines()[0] if direct else "OK")
            if is_verbose():
                print(summary)
                if tag in ("mute", "enable"):
                    print("Détails : trace [preflight] ci-dessus. Effet si start-acp-local.ps1 tourne.")
            elif tag == "mute":
                print(f"{summary}\n\n{direct}")
            elif tag == "enable":
                print(f"{summary}\n\n{direct}")
            else:
                print(direct)
            return

    preflight_block = "(aucun pré-traitement automatique)"
    prompt = bootstrap(args.message, preflight_block)
    engine = provider_label()
    print(f"--- ARIA-OUVRIER ({engine}) ---", file=sys.stderr)
    try:
        reply = run_ouvrier(prompt)
        print(reply)
        return
    except Exception as exc:
        sys.exit(f"[Erreur] Ouvrier: {exc}")


if __name__ == "__main__":
    main()