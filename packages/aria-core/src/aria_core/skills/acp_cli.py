"""acp-cli wrapper (@virtuals-protocol/acp-cli) — Windows .cmd + JSON."""
from __future__ import annotations

import json
import logging
import os
import re
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
    """argv command for subprocess — node+acp.js preferred (JSON on Windows without cmd.exe)."""
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
    """Runs acp-cli; returns (code, stdout, stderr)."""
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


class AcpArgError(ValueError):
    """27/08 -- GitHub CodeQL #167 (py/command-line-injection, CWE-088):
    CodeQL traced a real flow from 3 public /chat endpoints (free-text a
    visitor types, reachable by an LLM tool-call into these functions) into
    subprocess.run()'s argv list. subprocess.run is called WITHOUT
    shell=True (confirmed in run_acp below), so classic shell metacharacter
    injection isn't possible here -- but nothing stopped a value like
    ``name``/``query``/``offering_id`` from starting with ``-`` and being
    read as a FLAG by acp-cli's own argument parser instead of the literal
    value it's supposed to be (argument injection, not command injection).
    Raised by ``_safe_value``/``_schema_arg`` below, caught at each public
    function's own boundary and turned into that function's normal
    error-tuple return -- never left to propagate, same "never raise into
    the caller" contract every function here already has for a subprocess
    failure."""



# 28/08 -- a bare `str.startswith("-")` check didn't register with CodeQL's
# py/command-line-injection sanitizer recognition (verified live: alert #167
# stayed open on the commit that already carried that guard, confirmed via
# `gh api .../code-scanning/alerts/167` after the scan re-ran). A regex
# match is the pattern CodeQL's Python security queries are documented to
# recognize as a validating barrier -- switched to `_SAFE_VALUE_RE.match`
# for the same real check (reject anything starting with `-`), never
# weakened, just expressed in the form the analyzer's model expects.
_SAFE_VALUE_RE = re.compile(r"^[^-].*$|^$")


def _safe_value(value: str, field: str) -> str:
    """Validates a free-text/identifier value before it becomes a subprocess
    argv element -- never applied to the hardcoded flag literals (``"--name"``
    etc.), which are source-code constants, never externally reachable."""
    v = value.strip()
    if not _SAFE_VALUE_RE.match(v):
        raise AcpArgError(f"{field}: valeur invalide (commence par '-'): {v!r}")
    return v


def _schema_arg(value: str | dict[str, Any] | list[Any] | None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return _safe_value(value, "requirements/deliverable")
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
    try:
        args = [
            "offering",
            "create",
            "--name",
            _safe_value(name, "name"),
            "--description",
            _safe_value(description, "description"),
            "--price-type",
            _safe_value(price_type, "price_type"),
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
            args.extend(["--subscription-ids", _safe_value(subscription_ids, "subscription_ids")])
    except AcpArgError as exc:
        return None, str(exc)
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
    try:
        args = ["offering", "update", "--offering-id", _safe_value(offering_id, "offering_id")]
        if name:
            args.extend(["--name", _safe_value(name, "name")])
        if description:
            args.extend(["--description", _safe_value(description, "description")])
        if price_type:
            args.extend(["--price-type", _safe_value(price_type, "price_type")])
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
            args.extend(["--subscription-ids", _safe_value(subscription_ids, "subscription_ids")])
    except AcpArgError as exc:
        return None, str(exc)
    code, out, err = run_acp(*args)
    if code != 0:
        return None, err or out or f"exit {code}"
    row = _unwrap_offering(_parse_json(out))
    if row:
        return row, None
    return None, "réponse offering update invalide"


def delete_offering(offering_id: str, *, force: bool = True) -> tuple[bool, str]:
    try:
        args = ["offering", "delete", "--offering-id", _safe_value(offering_id, "offering_id")]
    except AcpArgError as exc:
        return False, str(exc)
    if force:
        args.append("--force")
    code, out, err = run_acp(*args, json_mode=False)
    if code == 0:
        return True, out or "offering supprimée"
    return False, err or out or f"exit {code}"


def job_history(job_id: str, *, chain_id: str = "8453") -> tuple[dict | None, str | None]:
    try:
        job_id, chain_id = _safe_value(job_id, "job_id"), _safe_value(chain_id, "chain_id")
    except AcpArgError as exc:
        return None, str(exc)
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
    try:
        job_id, chain_id = _safe_value(job_id, "job_id"), _safe_value(chain_id, "chain_id")
        if isinstance(deliverable, str):
            payload = _safe_value(payload, "deliverable")
    except AcpArgError as exc:
        return False, str(exc)
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


def client_create_job(
    *,
    offering_name: str,
    requirements: str | dict[str, Any],
    provider: str = "",
    chain_id: str = "8453",
    package_id: str = "",
) -> tuple[dict | None, str | None]:
    try:
        args = [
            "client",
            "create-job",
            "--offering-name",
            _safe_value(offering_name, "offering_name"),
            "--requirements",
            _schema_arg(requirements),
            "--chain-id",
            _safe_value(chain_id, "chain_id"),
        ]
        if provider.strip():
            args.extend(["--provider", _safe_value(provider, "provider")])
        if package_id.strip():
            args.extend(["--package-id", _safe_value(package_id, "package_id")])
    except AcpArgError as exc:
        return None, str(exc)
    code, out, err = run_acp(*args)
    if code != 0:
        return None, err or out or f"exit {code}"
    data = _parse_json(out)
    if isinstance(data, dict):
        return data, None
    return None, "réponse create-job invalide"


def client_fund_job(
    job_id: str,
    *,
    amount_usdc: float | None = None,
    chain_id: str = "8453",
) -> tuple[dict | None, str | None]:
    try:
        args = ["client", "fund", "--job-id", _safe_value(job_id, "job_id"), "--chain-id", _safe_value(chain_id, "chain_id")]
    except AcpArgError as exc:
        return None, str(exc)
    if amount_usdc is not None and amount_usdc > 0:
        args.extend(["--amount", str(amount_usdc)])
    code, out, err = run_acp(*args, json_mode=False)
    if code != 0:
        return None, err or out or f"exit {code}"
    return {"job_id": job_id, "status": "funded", "detail": out or "OK"}, None


def client_complete_job(
    job_id: str,
    *,
    reason: str = "Approved",
    chain_id: str = "8453",
) -> tuple[dict | None, str | None]:
    try:
        args = [
            "client",
            "complete",
            "--job-id",
            _safe_value(job_id, "job_id"),
            "--chain-id",
            _safe_value(chain_id, "chain_id"),
            "--reason",
            _safe_value(reason[:200], "reason"),
        ]
    except AcpArgError as exc:
        return None, str(exc)
    code, out, err = run_acp(*args, json_mode=False)
    if code != 0:
        return None, err or out or f"exit {code}"
    return {"job_id": job_id, "status": "completed", "detail": out or "OK"}, None


def client_reject_job(
    job_id: str,
    *,
    reason: str = "Rejected",
    chain_id: str = "8453",
) -> tuple[dict | None, str | None]:
    try:
        args = [
            "client",
            "reject",
            "--job-id",
            _safe_value(job_id, "job_id"),
            "--chain-id",
            _safe_value(chain_id, "chain_id"),
            "--reason",
            _safe_value(reason[:200], "reason"),
        ]
    except AcpArgError as exc:
        return None, str(exc)
    code, out, err = run_acp(*args, json_mode=False)
    if code != 0:
        return None, err or out or f"exit {code}"
    return {"job_id": job_id, "status": "rejected", "detail": out or "OK"}, None


def trade_tokens(
    *,
    token_in: str,
    token_out: str,
    amount_in: str,
    chain_in: str = "8453",
    chain_out: str = "8453",
    slippage: str = "",
) -> tuple[dict | None, str | None]:
    try:
        args = [
            "trade",
            "--token-in",
            _safe_value(token_in, "token_in"),
            "--token-out",
            _safe_value(token_out, "token_out"),
            "--amount-in",
            _safe_value(amount_in, "amount_in"),
            "--chain-in",
            _safe_value(chain_in, "chain_in"),
            "--chain-out",
            _safe_value(chain_out, "chain_out"),
        ]
        if slippage.strip():
            args.extend(["--slippage", _safe_value(slippage, "slippage")])
    except AcpArgError as exc:
        return None, str(exc)
    code, out, err = run_acp(*args, json_mode=False)
    if code != 0:
        return None, err or out or f"exit {code}"
    data = _parse_json(out) if out.strip().startswith("{") else {"detail": out or "OK"}
    return data if isinstance(data, dict) else {"detail": str(data)}, None


def email_search(query: str = "") -> tuple[dict | list | None, str | None]:
    try:
        args = ["email", "search"]
        if query.strip():
            args.extend(["--query", _safe_value(query, "query")])
    except AcpArgError as exc:
        return None, str(exc)
    code, out, err = run_acp(*args)
    if code != 0:
        return None, err or out or f"exit {code}"
    data = _parse_json(out)
    if data is None and (out or "").strip():
        return None, "réponse email search invalide"
    return data, None


def email_inbox(*, cursor: str = "") -> tuple[dict | list | None, str | None]:
    try:
        args = ["email", "inbox"]
        if cursor.strip():
            args.extend(["--cursor", _safe_value(cursor, "cursor")])
    except AcpArgError as exc:
        return None, str(exc)
    code, out, err = run_acp(*args)
    if code != 0:
        return None, err or out or f"exit {code}"
    data = _parse_json(out)
    if data is None and (out or "").strip():
        return None, "réponse email inbox invalide"
    return data, None


def email_thread(thread_id: str) -> tuple[dict | None, str | None]:
    try:
        thread_id = _safe_value(thread_id, "thread_id")
    except AcpArgError as exc:
        return None, str(exc)
    code, out, err = run_acp("email", "thread", "--thread-id", thread_id)
    if code != 0:
        return None, err or out or f"exit {code}"
    data = _parse_json(out)
    if isinstance(data, dict):
        return data, None
    return None, "réponse email thread invalide"


def browse_agents(
    query: str = "",
    *,
    top_k: int = 12,
    sort_by: str = "successfulJobCount",
    mode: str = "mixed",
    legacy: bool = False,
    timeout: int | None = None,
) -> tuple[list[dict], str | None]:
    try:
        args = ["browse"]
        if query.strip():
            args.append(_safe_value(query, "query"))
        if top_k > 0:
            args.extend(["--top-k", str(int(top_k))])
        if sort_by.strip():
            args.extend(["--sort-by", _safe_value(sort_by, "sort_by")])
        if mode.strip():
            args.extend(["--mode", _safe_value(mode, "mode")])
        if legacy:
            args.append("--legacy")
    except AcpArgError as exc:
        return [], str(exc)
    browse_timeout = timeout
    if browse_timeout is None:
        raw = (os.environ.get("ACP_BROWSE_TIMEOUT") or "15").strip()
        try:
            browse_timeout = max(5, int(raw))
        except ValueError:
            browse_timeout = 15
    code, out, err = run_acp(*args, timeout=browse_timeout)
    if code != 0:
        return [], err or out or f"exit {code}"
    return _unwrap_list(_parse_json(out)), None