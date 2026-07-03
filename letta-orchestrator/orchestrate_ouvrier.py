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
from ouvrier_proof import (
    attach_proof,
    build_system_proof,
    local_api_status,
    read_vault_key,
    split_reply_proof,
    vault_env_names,
)
from ouvrier_acp_direct import try_acp_workflow_direct
from ouvrier_instant import instant_reply, is_simple_exchange
from ouvrier_runner import provider_label, run_ouvrier
from ouvrier_session import enrich_continuation, is_continuation, load_session, save_session, wants_opinion
from ouvrier_vision import build_image_context, direct_image_reply, wants_image_context
from ouvrier_trace import StepTimer, emit_final, emit_proof, is_verbose, set_verbose, trace, trace_block

CONFIG_PATH = ARIA_REPO_ROOT / "letta-orchestrator" / "ouvrier_config.json"
WORKER_PATH = ARIA_REPO_ROOT / "collegue-memoire" / "sessions" / "ARIA-WORKER.md"
_SUBPROC = {"encoding": "utf-8", "errors": "replace", "text": True}
_VAULT_DIR = Path(os.environ.get("LOCALAPPDATA", "")) / "GoldenFar" / "vault"


_ACP_CONTEXT_FILES = (
    "packages/aria-core/src/aria_core/skills/acp_product_launch_skill.py",
    "packages/aria-core/src/aria_core/skills/acp_offering_skill.py",
    "packages/aria-core/src/aria_core/knowledge/acp_offerings.yaml",
    "skills/core/acp_integration.ps1",
)


def display_ouvrier_output(raw: str) -> str:
    """Réponse utilisateur (›) séparée des traces moteur et de la preuve."""
    bundled = attach_proof(raw, "")
    body, proof = split_reply_proof(bundled)
    emit_final(body)
    emit_proof(proof)
    return body


def preflight_acp_context(message: str) -> str:
    """Injecte le workflow ACP du repo — uniquement pour demandes d'avis, pas création."""
    if not re.search(r"(?i)\bacp\b", message):
        return ""
    if re.search(
        r"(?i)(?:^|\s)(?:cr[ée]er|crée|lancer)\s+(?:un\s+)?(?:workflow|offre|produit)"
        r"|appeler\s+[a-z]"
        r"|réparer\s+offres?\s+acp|reparer\s+offres?\s+acp"
        r"|publier\s+produit\s+acp",
        message,
    ):
        return ""
    if not (
        wants_opinion(message)
        or re.search(r"(?i)tu en penses|ton avis|qu.en penses|tu en pense", message)
    ):
        return ""
    parts: list[str] = [
        "Contexte ACP (fichiers repo — donne TON avis concret, pas un plan) :",
    ]
    for rel in _ACP_CONTEXT_FILES:
        path = ARIA_REPO_ROOT / rel
        if not path.is_file():
            parts.append(f"• {rel} : absent")
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        excerpt = text[:1400].strip()
        if len(text) > 1400:
            excerpt += f"\n… ({len(text)} caractères au total)"
        parts.append(f"--- {rel} ---\n{excerpt}")
    parts.append(
        "Réponds en français : ce que le workflow apporte, ce qui manque, prochaine étape. "
        "Interdit : « je vais regarder le fichier » — tu as le contenu ci-dessus."
    )
    return "\n\n".join(parts)


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
    action = r"(?:supprim|couper|désactiv|desactiv|éteind|eteind|arrêt|arret|stop|moins|trop|spam)"
    if not re.search(
        rf"(?i){action}.{{0,50}}(?:notif|notification).{{0,50}}telegram"
        rf"|telegram.{{0,50}}(?:notif|notification).{{0,50}}{action}"
        rf"|(?:notif|notification).{{0,30}}{action}",
        message,
    ):
        return ""
    actions: list[str] = [local_api_status()]
    for name in vault_env_names():
        path = _VAULT_DIR / name
        if path.parent.is_dir():
            actions.append(_patch_env_file(path, "ARIA_PROACTIVE_IDEAS", "false"))
    actions.append(
        "Sources : proactive.py (founder_ping), heartbeat.py, aria_worker_queue, capability_gap"
    )
    actions.append(build_system_proof(keys=["ARIA_PROACTIVE_IDEAS"], action="mute notifs Telegram"))
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
    actions: list[str] = [local_api_status()]
    for name in vault_env_names():
        path = _VAULT_DIR / name
        if path.parent.is_dir():
            actions.append(_patch_env_file(path, "ARIA_PROACTIVE_IDEAS", "true"))
    actions.append("Clé SSOT : ARIA_PROACTIVE_IDEAS (local.env — runtime PC)")
    actions.append(build_system_proof(keys=["ARIA_PROACTIVE_IDEAS"], action="activation notifs Telegram"))
    return "PRÉ-TRAITEMENT ACTIVATION NOTIFS (déjà exécuté):\n" + "\n".join(
        f"• {a}" if not a.startswith("PREUVE") else a for a in actions
    )


def preflight_notification_status(message: str) -> str:
    if not re.search(
        r"(?i)(quel|quoi|liste|état|etat|encore).{0,30}(notif|notification)"
        r"|(notif|notification).{0,30}(active|actif|encore|reste)",
        message,
    ):
        return ""
    lines = ["État notifications Telegram ARIA (runtime PC local) :", local_api_status(), ""]
    for name in vault_env_names():
        val = read_vault_key("ARIA_PROACTIVE_IDEAS") or "(absent)"
        lines.append(f"• {name} → ARIA_PROACTIVE_IDEAS={val}")
    lines.extend(
        [
            "• founder_ping (heartbeat) : actif seulement si ARIA_PROACTIVE_IDEAS=true + LLM + bot",
            "• aria_worker_queue / capability_gap : notify_admin (si worker actif)",
            "• portfolio_scan heartbeat : notif si items portfolio > 0",
            "",
            build_system_proof(keys=["ARIA_PROACTIVE_IDEAS"], action="état notifs"),
        ]
    )
    return "\n".join(lines)


def preflight_preuve(message: str) -> str:
    if not re.search(
        r"(?i)(preuve|prouve|vérif|verif|confirme|sois?\s+sûr|sur\s+que|sans\s+preuve|lecture\s+disque|état\s+système|etat\s+systeme)",
        message,
    ):
        return ""
    # Preuve Telegram live → handler ping (évite lecture disque seule)
    if re.search(r"(?i)telegram|bot", message) and re.search(
        r"(?i)ping|envoi|message|vraiment|joignable",
        message,
    ):
        return ""
    return build_system_proof(action="demande opérateur") + (
        "\n\nPour preuve Telegram live : « ping telegram »."
        f"\nVault : {_VAULT_DIR}"
    )


def preflight_telegram_ping(message: str) -> str:
    wants_ping = bool(re.search(r"(?i)ping|confirmation|confirme|envoi|message", message))
    wants_preuve_tg = bool(
        re.search(r"(?i)preuve|prouve", message) and re.search(r"(?i)telegram|bot", message)
    )
    if not wants_ping and not wants_preuve_tg:
        return ""
    if not wants_preuve_tg and not re.search(r"(?i)telegram|bot|aria", message) and "ping" not in message.lower():
        return ""
    bridge_api_keys()
    token = read_vault_key("TELEGRAM_BOT_TOKEN")
    admin = read_vault_key("TELEGRAM_ADMIN_IDS") or read_vault_key("TELEGRAM_GROUP_ID")
    if not token or not admin:
        return "ERREUR: TELEGRAM_BOT_TOKEN ou TELEGRAM_ADMIN_IDS absent du vault."
    proactive = read_vault_key("ARIA_PROACTIVE_IDEAS") or "(absent)"
    if proactive.lower() == "false":
        notif_line = "notifs proactive OFF (ARIA_PROACTIVE_IDEAS=false)"
    elif proactive.lower() == "true":
        notif_line = "notifs proactive ON (ARIA_PROACTIVE_IDEAS=true)"
    else:
        notif_line = f"ARIA_PROACTIVE_IDEAS={proactive}"
    text = (
        "✅ Ping ARIA-Ouvrier — bot Telegram joignable.\n"
        f"État vault : {notif_line}."
    )
    chat_id = int(admin.split(",")[0].strip())
    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text},
        timeout=30,
    )
    if r.status_code != 200:
        return f"ERREUR Telegram API {r.status_code}: {r.text[:300]}"
    lines = [
        f"Ping envoyé sur Telegram (chat {chat_id}).",
        text,
        "",
        build_system_proof(keys=["ARIA_PROACTIVE_IDEAS"], action="ping Telegram"),
    ]
    return "\n".join(lines)


def _pending_worker_count() -> int:
    if not WORKER_PATH.is_file():
        return 0
    return WORKER_PATH.read_text(encoding="utf-8").count("[pending]")


def _needs_bootstrap(message: str) -> bool:
    if os.environ.get("ARIA_OUVRIER_BOOTSTRAP", "").strip().lower() in ("1", "true", "yes", "on"):
        return True
    return bool(
        re.search(
            r"(?i)worker|pending|handoff|download|inbox|aria-worker|file d'attente|triage",
            message,
        )
    )


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

    user_msg = args.message
    session = load_session()
    effective = enrich_continuation(user_msg) if is_continuation(user_msg) else user_msg
    image_block, image_path, image_analysis = build_image_context(user_msg, session)
    if image_block:
        effective = f"{image_block}\n\n{effective}"

    direct = (
        direct_image_reply(user_msg, image_analysis or "", image_path or "")
        if image_analysis and image_path
        else None
    )
    if direct:
        save_session(user_msg, display_ouvrier_output(direct), image_path=image_path)
        return

    if (
        is_simple_exchange(user_msg)
        and not is_continuation(user_msg)
        and not wants_image_context(user_msg, session)
    ):
        save_session(user_msg, display_ouvrier_output(instant_reply(user_msg)))
        return

    acp_direct = try_acp_workflow_direct(effective)
    if acp_direct:
        save_session(user_msg, display_ouvrier_output(acp_direct), image_path=image_path)
        return

    trace("pensee", f"Message Sylvain : {user_msg[:300]}")
    handlers = (
        ("mute", preflight_telegram_notifications),
        ("enable", preflight_telegram_activate),
        ("status", preflight_notification_status),
        ("ping", preflight_telegram_ping),
        ("preuve", preflight_preuve),
    )
    for tag, handler in handlers:
        with StepTimer(f"preflight {tag}"):
            direct = handler(user_msg)
        if direct:
            trace_block("preflight", tag, direct, max_lines=10)
        if direct and not re.search(r"(?i)^\s*(oui|ok|yes)\s*$", user_msg):
            print(f"--- ARIA-OUVRIER ({tag}) ---", file=sys.stderr)
            summaries = {
                "mute": "C'est fait — notifications proactive Telegram coupées (ARIA_PROACTIVE_IDEAS=false).",
                "enable": "C'est fait — notifications proactive Telegram activées (ARIA_PROACTIVE_IDEAS=true).",
                "status": direct.splitlines()[0] if direct else "État notifications.",
                "ping": "Ping Telegram envoyé — vérifie ton chat ARIA.",
                "preuve": "Preuve système ci-dessous (vault + runtime).",
            }
            summary = summaries.get(tag, direct.splitlines()[0] if direct else "OK")
            if is_verbose():
                print(summary)
                if tag in ("mute", "enable"):
                    print("Détails : trace [preflight] ci-dessus. Effet si start-acp-local.ps1 tourne.")
            elif tag in ("mute", "enable"):
                save_session(
                    user_msg,
                    display_ouvrier_output(f"{summary}\n\n{direct}"),
                    image_path=image_path,
                )
            else:
                save_session(user_msg, display_ouvrier_output(direct), image_path=image_path)
            return

    prompt = effective
    acp_block = preflight_acp_context(effective)
    if acp_block:
        prompt = f"{acp_block}\n\n{prompt}"
    elif wants_opinion(effective) or wants_opinion(str(load_session().get("last_user") or "")):
        prompt = (
            "Sylvain demande ton AVIS — lis le repo (read_repo_file) puis réponds.\n\n"
            + prompt
        )

    if _needs_bootstrap(user_msg):
        preflight_block = "(aucun pré-traitement automatique)"
        prompt = bootstrap(user_msg, preflight_block)
        if acp_block and acp_block not in prompt:
            prompt = f"{acp_block}\n\n{prompt}"
    engine = provider_label()
    print(f"--- ARIA-OUVRIER ({engine}) ---", file=sys.stderr)
    try:
        reply = run_ouvrier(prompt)
        save_session(user_msg, display_ouvrier_output(reply), image_path=image_path)
        return
    except Exception as exc:
        sys.exit(f"[Erreur] Ouvrier: {exc}")


if __name__ == "__main__":
    main()