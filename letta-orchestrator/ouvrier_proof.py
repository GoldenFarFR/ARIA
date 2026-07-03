"""Preuves disque/runtime — systématiques après chaque action ouvrier."""
from __future__ import annotations

import os
import re
import socket
import subprocess
from datetime import datetime
from pathlib import Path

import requests

ARIA_REPO_ROOT = Path(
    os.environ.get("ARIA_REPO_ROOT", str(Path.home() / "GitHub-Repos" / "ARIA"))
).resolve()
_VAULT_DIR = Path(os.environ.get("LOCALAPPDATA", "")) / "GoldenFar" / "vault"
_LOCAL_START = ARIA_REPO_ROOT / "vanguard" / "operator" / "start-acp-local.ps1"

SYSTEM_WATCH_KEYS: tuple[str, ...] = (
    "ARIA_RUNTIME",
    "ARIA_PROACTIVE_IDEAS",
    "LLM_PROVIDER",
    "ARIA_AUTONOMOUS",
    "TELEGRAM_BOT_USERNAME",
)

_SUBPROC = {"encoding": "utf-8", "errors": "replace", "text": True}


def proof_enabled() -> bool:
    return os.environ.get("ARIA_OUVRIER_PROOF", "1").strip().lower() not in (
        "",
        "0",
        "false",
        "no",
        "off",
    )


def vault_env_names() -> list[str]:
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


def local_api_status() -> str:
    try:
        with socket.create_connection(("127.0.0.1", 8000), timeout=1.5):
            return "✓ Bot/API local :8000 actif"
    except OSError:
        return f"✗ Bot/API local arrêté — lance : {_LOCAL_START}"


def health_snippet() -> str:
    try:
        h = requests.get("http://127.0.0.1:8000/api/health", timeout=3).json()
        acp = (h.get("aria_acp") or {}).get("provider_enabled")
        tg = (h.get("aria_telegram") or {}).get("configured")
        return f"health status={h.get('status')} acp={acp} telegram={tg}"
    except Exception:
        return "health :8000 indisponible"


def read_vault_key(key: str) -> str:
    for name in vault_env_names():
        path = _VAULT_DIR / name
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.strip().startswith(f"{key}="):
                return line.split("=", 1)[1].strip().strip('"')
    return ""


def _vault_lines_for_keys(keys: tuple[str, ...] | list[str]) -> list[str]:
    lines: list[str] = []
    for name in vault_env_names():
        path = _VAULT_DIR / name
        if not path.is_file():
            lines.append(f"  ✗ {name} — fichier absent")
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        mtime = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        lines.append(f"  • {path} (modifié {mtime})")
        for key in keys:
            found = ""
            for raw in text.splitlines():
                if raw.strip().startswith(f"{key}="):
                    found = raw.strip()
                    break
            if found:
                lines.append(f"    ✓ {found}")
            else:
                lines.append(f"    ✗ {key} absent")
    return lines


def build_system_proof(
    *,
    keys: tuple[str, ...] | list[str] | None = None,
    action: str = "",
    include_health: bool = True,
) -> str:
    watch = tuple(keys) if keys else SYSTEM_WATCH_KEYS
    title = f"PREUVE — {action}" if action else "PREUVE SYSTÈME (lecture disque + runtime) :"
    lines = [
        title,
        f"  horodatage : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"  {local_api_status()}",
    ]
    if include_health:
        lines.append(f"  {health_snippet()}")
    lines.append("")
    lines.extend(_vault_lines_for_keys(watch))
    return "\n".join(lines)


def compact_proof(keys: tuple[str, ...] | list[str] | None = None) -> str:
    watch = tuple(keys) if keys else ("ARIA_RUNTIME", "ARIA_PROACTIVE_IDEAS")
    parts = [datetime.now().strftime("%H:%M:%S"), local_api_status().replace("✓ ", "").replace("✗ ", "")]
    for key in watch:
        val = read_vault_key(key) or "(absent)"
        parts.append(f"{key}={val}")
    return "── PREUVE ── " + " | ".join(parts)


def proof_after_tool(name: str, args: dict, result: str) -> str:
    if "ERROR" in result[:80]:
        return ""
    if name == "patch_vault_env":
        key = str(args.get("key") or "")
        if key:
            return build_system_proof(keys=[key], action=f"patch_vault_env({key})")
    if name == "write_repo_file":
        rel = str(args.get("rel_path") or "")
        if rel and result.startswith("OK"):
            target = (ARIA_REPO_ROOT / rel).resolve()
            if target.is_file():
                mtime = datetime.fromtimestamp(target.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
                size = target.stat().st_size
                return (
                    f"PREUVE FICHIER — {rel}\n"
                    f"  ✓ {target}\n"
                    f"  taille={size} octets | modifié {mtime}"
                )
    if name == "append_journal":
        journal = ARIA_REPO_ROOT / "collegue-memoire" / "JOURNAL.md"
        if journal.is_file():
            tail = journal.read_text(encoding="utf-8", errors="replace").splitlines()[-3:]
            return "PREUVE JOURNAL — dernières lignes :\n" + "\n".join(f"  {ln}" for ln in tail)
    if name == "run_powershell":
        cmd = str(args.get("command") or "").lower()
        if "git commit" in cmd or "git add" in cmd:
            proc = subprocess.run(
                ["git", "-C", str(ARIA_REPO_ROOT), "log", "-1", "--oneline"],
                capture_output=True,
                timeout=15,
                **_SUBPROC,
            )
            line = (proc.stdout or proc.stderr or "").strip()
            if line:
                return f"PREUVE GIT — dernier commit :\n  {line}"
    return ""


def split_reply_proof(text: str) -> tuple[str, str]:
    """Sépare le corps de réponse des blocs PREUVE (affichage KART)."""
    raw = (text or "").strip()
    if not raw:
        return "", ""
    marker = re.search(r"\n\s*──\s*PREUVE\s*──", raw, re.IGNORECASE)
    if marker:
        return raw[: marker.start()].strip(), raw[marker.start() :].strip()

    body_lines: list[str] = []
    proof_lines: list[str] = []
    in_proof = False
    for line in raw.splitlines():
        if re.match(r"^\s*(?:──\s*)?PR[ÉE]UVE\b", line, re.IGNORECASE):
            in_proof = True
        if in_proof:
            proof_lines.append(line)
        else:
            body_lines.append(line)
    body = "\n".join(body_lines).strip()
    proof = "\n".join(proof_lines).strip()
    return body, proof


def attach_proof(text: str, proof: str, *, always_compact: bool = False) -> str:
    if not text:
        text = ""
    blocks: list[str] = []
    if proof and proof not in text:
        blocks.append(proof)
    if proof_enabled() and (always_compact or not proof):
        compact = compact_proof()
        if compact not in text and compact not in "\n".join(blocks):
            blocks.append(compact)
    if not blocks:
        return text
    return f"{text.rstrip()}\n\n" + "\n\n".join(blocks)