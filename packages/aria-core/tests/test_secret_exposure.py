"""Mechanical proof that the tools a session runs cannot print a secret.

Why these tests exist (02/09): the rule "never display a secret" was already
written and had already been broken twice in July. It broke a third time when
a session ran ``docker inspect`` with a broad grep while checking a gate, and
printed a private key. A rule that depends on the agent remembering it is not
a control -- so this file turns the important half into something that fails
in CI instead of failing in production.

Design constraint that shapes every assertion below: **a failure message must
never contain the secret it caught.** A test that leaks the value while
reporting the leak would be worse than no test at all. So every check compares
values and reports only variable NAMES and counts.
"""
from __future__ import annotations

import ast
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
ENV_FILE = Path(os.environ.get("ARIA_ENV_FILE", "/opt/aria/vanguard/backend/.env"))

# Below this length a value is a flag ("true", "1"), not a credential. Keeping
# them would flood every check with false positives, since "true" appears in
# any output.
MIN_SECRET_LEN = 12


def _load_secret_values() -> dict[str, str]:
    """{name: value} for credential-looking variables. Never logged anywhere."""
    if not ENV_FILE.is_file():
        return {}
    out: dict[str, str] = {}
    for line in ENV_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, _, value = stripped.partition("=")
        name = name.strip()
        value = value.strip()
        if len(value) < MIN_SECRET_LEN:
            continue
        upper = name.upper()
        if any(
            token in upper
            for token in ("_KEY", "_SECRET", "_TOKEN", "PASSWORD", "PRIVATE", "MNEMONIC", "SEED")
        ):
            out[name] = value
    return out


def _leaked_names(blob: str, secrets: dict[str, str]) -> list[str]:
    """Names whose VALUE appears in blob. Returns names only, never values."""
    return sorted(name for name, value in secrets.items() if value and value in blob)


requires_env = pytest.mark.skipif(
    not ENV_FILE.is_file(),
    reason="no real env file here (CI); these are production-surface tests",
)


@requires_env
def test_gate_status_script_never_emits_a_secret_value():
    """The session-start hook runs this on EVERY session, so a regression in
    its filter would leak on every session rather than once. It reads the
    configuration file directly since 02/09 (Docker metadata no longer carries
    the variables), which makes its strict `ARIA_*_ENABLED=` filter the only
    thing standing between the file and the transcript."""
    secrets = _load_secret_values()
    assert secrets, "expected to load some credential-looking variables"

    script = REPO_ROOT / "scripts" / "gate-status.sh"
    assert script.is_file(), "scripts/gate-status.sh is missing"
    res = subprocess.run(
        ["bash", str(script)], capture_output=True, text=True, timeout=60,
    )
    blob = (res.stdout or "") + (res.stderr or "")

    leaked = _leaked_names(blob, secrets)
    assert not leaked, (
        f"gate-status.sh emitted the VALUE of {len(leaked)} secret(s): "
        f"{', '.join(leaked)} -- tighten its filter"
    )
    # Guard against the opposite failure: a filter so tight it prints nothing
    # would pass the leak check while silently blinding every session.
    assert "ENABLED=" in blob, "gate-status.sh returned no gate at all"


@requires_env
def test_secret_exposure_audit_reports_fingerprints_never_values():
    """The auditor reads every secret by construction. If IT leaks, the tool
    built to measure leaks becomes the leak."""
    secrets = _load_secret_values()
    script = REPO_ROOT / "scripts" / "secret-exposure-audit.py"
    assert script.is_file(), "scripts/secret-exposure-audit.py is missing"
    res = subprocess.run(
        ["python3", str(script)], capture_output=True, text=True, timeout=120,
    )
    blob = (res.stdout or "") + (res.stderr or "")

    leaked = _leaked_names(blob, secrets)
    assert not leaked, (
        f"the audit tool printed the VALUE of {len(leaked)} secret(s): {', '.join(leaked)}"
    )


def test_deploy_never_reverts_to_env_file():
    """`docker run --env-file` copies every variable into Docker's metadata,
    where `docker inspect` returns them in clear text. Measured on 02/09
    before the fix: 62 secret values exposed, 2 of them private keys. This is
    the invariant that keeps someone from "simplifying" the mount back."""
    raw = (REPO_ROOT / "vanguard" / "deploy.sh").read_text(encoding="utf-8")
    # Comments are stripped first: the fix's own comment names `--env-file` to
    # explain why never to return to it, and a substring match would flag the
    # warning as the offence. Same trap already hit once this session on an
    # identifier check -- match on what executes, never on what documents.
    deploy = "\n".join(
        line for line in raw.splitlines() if not line.lstrip().startswith("#")
    )
    assert "--env-file" not in deploy, (
        "vanguard/deploy.sh uses --env-file again -- that puts every secret into "
        "`docker inspect`. Mount the file read-only instead (see docker-entrypoint.py)."
    )
    assert "/run/aria/env:ro" in deploy, "the read-only config mount disappeared from deploy.sh"


def test_entrypoint_never_prints_an_environment_value():
    """AST-level, not substring: the entrypoint holds every secret in memory
    by construction, so what it is allowed to PRINT is the real boundary. It
    may print a count; it must never print a value or a variable name."""
    src = (REPO_ROOT / "vanguard" / "docker-entrypoint.py").read_text(encoding="utf-8")
    tree = ast.parse(src)

    banned_names = {"value", "name"}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        if node.func.id != "print":
            continue
        for arg in ast.walk(ast.Module(body=[ast.Expr(value=a) for a in node.args], type_ignores=[])):
            # A bare local named value/name reaching a print is the exact
            # mistake this guards; f-strings are walked too, since that is how
            # such a value would realistically travel.
            if isinstance(arg, ast.Name) and arg.id in banned_names:
                raise AssertionError(
                    f"docker-entrypoint.py prints `{arg.id}` -- it must only ever "
                    "report counts, never a variable name or its value"
                )
            if isinstance(arg, ast.Subscript):
                target = getattr(arg.value, "attr", None) or getattr(arg.value, "id", None)
                if target == "environ":
                    raise AssertionError("docker-entrypoint.py prints an os.environ lookup")
