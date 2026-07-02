"""Wrapper acp-cli (@virtuals-protocol/acp-cli) — Windows .cmd + JSON."""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 90


def _npm_acp_cmd() -> Path | None:
    appdata = os.environ.get("APPDATA", "").strip()
    if not appdata:
        return None
    path = Path(appdata) / "npm" / "acp.cmd"
    return path if path.is_file() else None


def _npm_acp_js() -> Path | None:
    appdata = os.environ.get("APPDATA", "").strip()
    if not appdata:
        return None
    path = (
        Path(appdata)
        / "npm"
        / "node_modules"
        / "@virtuals-protocol"
        / "acp-cli"
        / "dist"
        / "bin"
        / "acp.js"
    )
    return path if path.is_file() else None


def resolve_acp_command() -> list[str]:
    """Commande argv pour subprocess (Windows : acp.cmd via cmd.exe)."""
    cmd = _npm_acp_cmd()
    if cmd:
        return ["cmd.exe", "/c", str(cmd)]
    js = _npm_acp_js()
    if js:
        node = shutil.which("node") or "node"
        return [node, str(js)]
    which = shutil.which("acp")
    if which:
        return [which]
    return []


def is_acp_available() -> bool:
    return bool(resolve_acp_command())


def run_acp(
    *args: str,
    timeout: int = _DEFAULT_TIMEOUT,
    json_mode: bool = True,
) -> tuple[int, str, str]:
    """Exécute acp-cli ; retourne (code, stdout, stderr)."""
    base = resolve_acp_command()
    if not base:
        return 127, "", "acp-cli introuvable (npm i -g @virtuals-protocol/acp-cli)"
    cmd = [*base]
    if json_mode:
        cmd.append("--json")
    cmd.extend(args)
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout après {timeout}s"
    except OSError as exc:
        return 1, "", str(exc)


def _parse_json(stdout: str) -> Any:
    text = (stdout or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def list_agents() -> tuple[list[dict], str | None]:
    code, out, err = run_acp("agent", "list")
    if code != 0:
        return [], err or out or f"exit {code}"
    data = _parse_json(out)
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)], None
    return [], "réponse agent list invalide"


def list_offerings() -> tuple[list[dict], str | None]:
    code, out, err = run_acp("offering", "list")
    if code != 0:
        return [], err or out or f"exit {code}"
    data = _parse_json(out)
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)], None
    return [], "réponse offering list invalide"


def job_history(job_id: str, *, chain_id: str = "8453") -> tuple[dict | None, str | None]:
    code, out, err = run_acp("job", "history", "--job-id", job_id, "--chain-id", chain_id)
    if code != 0:
        return None, err or out or f"exit {code}"
    data = _parse_json(out)
    if isinstance(data, dict):
        return data, None
    return None, "réponse job history invalide"


def provider_submit(
    job_id: str,
    deliverable: dict[str, Any] | str,
    *,
    chain_id: str = "8453",
) -> tuple[bool, str]:
    payload = deliverable if isinstance(deliverable, str) else json.dumps(deliverable, ensure_ascii=False)
    code, out, err = run_acp(
        "provider",
        "submit",
        "--job-id",
        job_id,
        "--deliverable",
        payload,
        "--chain-id",
        chain_id,
        json_mode=False,
    )
    if code == 0:
        return True, (out or "deliverable soumis")
    return False, err or out or f"exit {code}"


def browse_agents(query: str = "") -> tuple[list[dict], str | None]:
    args = ["browse"]
    if query.strip():
        args.append(query.strip())
    code, out, err = run_acp(*args)
    if code != 0:
        return [], err or out or f"exit {code}"
    data = _parse_json(out)
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)], None
    return [], None