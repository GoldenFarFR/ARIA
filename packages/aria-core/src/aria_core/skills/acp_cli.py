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
    """Commande argv pour subprocess — node+acp.js préféré (JSON Windows sans cmd.exe)."""
    js = _npm_acp_js()
    if js:
        node = shutil.which("node") or "node"
        return [node, str(js)]
    cmd = _npm_acp_cmd()
    if cmd:
        return ["cmd.exe", "/c", str(cmd)]
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


def _unwrap_list(data: Any) -> list[dict]:
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if isinstance(data, dict):
        for key in ("data", "agents", "items", "results"):
            block = data.get(key)
            if isinstance(block, list):
                return [r for r in block if isinstance(r, dict)]
    return []


def list_agents() -> tuple[list[dict], str | None]:
    code, out, err = run_acp("agent", "list")
    if code != 0:
        return [], err or out or f"exit {code}"
    rows = _unwrap_list(_parse_json(out))
    if rows:
        return rows, None
    return [], "réponse agent list invalide"


def _unwrap_offering(data: Any) -> dict | None:
    if isinstance(data, dict):
        for key in ("data", "offering"):
            block = data.get(key)
            if isinstance(block, dict):
                return block
        if data.get("id") or data.get("name"):
            return data
    return None


def _schema_arg(value: str | dict[str, Any] | list[Any] | None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return json.dumps(value, ensure_ascii=False)


def list_subscriptions() -> tuple[list[dict], str | None]:
    code, out, err = run_acp("subscription", "list")
    if code != 0:
        return [], err or out or f"exit {code}"
    data = _parse_json(out)
    if data is None and (out or "").strip():
        return [], "réponse subscription list invalide"
    return _unwrap_list(data), None


def list_offerings() -> tuple[list[dict], str | None]:
    code, out, err = run_acp("offering", "list")
    if code != 0:
        return [], err or out or f"exit {code}"
    data = _parse_json(out)
    if data is None and (out or "").strip():
        return [], "réponse offering list invalide"
    return _unwrap_list(data), None


def create_offering(
    *,
    name: str,
    description: str,
    price_value: float,
    price_type: str = "fixed",
    sla_minutes: int = 5,
    requirements: str | dict[str, Any] | list[Any] | None = None,
    deliverable: str | dict[str, Any] | list[Any] | None = None,
    required_funds: bool = False,
    hidden: bool = False,
    subscription_ids: str = "",
) -> tuple[dict | None, str | None]:
    args = [
        "offering",
        "create",
        "--name",
        name.strip(),
        "--description",
        description.strip(),
        "--price-type",
        price_type,
        "--price-value",
        str(price_value),
        "--sla-minutes",
        str(int(sla_minutes)),
    ]
    req = _schema_arg(requirements)
    if req:
        args.extend(["--requirements", req])
    deliv = _schema_arg(deliverable)
    if deliv:
        args.extend(["--deliverable", deliv])
    args.append("--required-funds" if required_funds else "--no-required-funds")
    args.append("--hidden" if hidden else "--no-hidden")
    if subscription_ids.strip():
        args.extend(["--subscription-ids", subscription_ids.strip()])
    code, out, err = run_acp(*args)
    if code != 0:
        return None, err or out or f"exit {code}"
    row = _unwrap_offering(_parse_json(out))
    if row:
        return row, None
    return None, "réponse offering create invalide"


def update_offering(
    offering_id: str,
    *,
    name: str | None = None,
    description: str | None = None,
    price_value: float | None = None,
    price_type: str | None = None,
    sla_minutes: int | None = None,
    requirements: str | dict[str, Any] | list[Any] | None = None,
    deliverable: str | dict[str, Any] | list[Any] | None = None,
    required_funds: bool | None = None,
    hidden: bool | None = None,
    subscription_ids: str | None = None,
) -> tuple[dict | None, str | None]:
    args = ["offering", "update", "--offering-id", offering_id.strip()]
    if name:
        args.extend(["--name", name.strip()])
    if description:
        args.extend(["--description", description.strip()])
    if price_type:
        args.extend(["--price-type", price_type])
    if price_value is not None:
        args.extend(["--price-value", str(price_value)])
    if sla_minutes is not None:
        args.extend(["--sla-minutes", str(int(sla_minutes))])
    req = _schema_arg(requirements) if requirements is not None else ""
    if req:
        args.extend(["--requirements", req])
    deliv = _schema_arg(deliverable) if deliverable is not None else ""
    if deliv:
        args.extend(["--deliverable", deliv])
    if required_funds is True:
        args.append("--required-funds")
    elif required_funds is False:
        args.append("--no-required-funds")
    if hidden is True:
        args.append("--hidden")
    elif hidden is False:
        args.append("--no-hidden")
    if subscription_ids:
        args.extend(["--subscription-ids", subscription_ids.strip()])
    code, out, err = run_acp(*args)
    if code != 0:
        return None, err or out or f"exit {code}"
    row = _unwrap_offering(_parse_json(out))
    if row:
        return row, None
    return None, "réponse offering update invalide"


def delete_offering(offering_id: str, *, force: bool = True) -> tuple[bool, str]:
    args = ["offering", "delete", "--offering-id", offering_id.strip()]
    if force:
        args.append("--force")
    code, out, err = run_acp(*args, json_mode=False)
    if code == 0:
        return True, out or "offering supprimée"
    return False, err or out or f"exit {code}"


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
    return _unwrap_list(_parse_json(out)), None