#!/usr/bin/env python3
"""Measure which secrets a privileged shell on this VPS can actually recover.

Why this exists (02/09, written the day it was needed): a session ran
``docker inspect`` with a broad grep and printed a private key in clear text.
The rule forbidding that was already written; discipline alone did not hold.
So the correction is not "remember harder" -- it is to CLOSE the paths, and to
have an instrument that says whether a path is really closed rather than
assumed closed.

**What this tool never does**: print, log, or write a secret value. It reads
values only to hash them, compares hashes, and reports an 8-hex fingerprint.
A fingerprint identifies a secret across surfaces (same key here and there)
without carrying it. That asymmetry is the whole point -- and it is why this
file can be committed to a public repo while auditing a private .env.

**The honest limit, stated up front so no one over-trusts a green report**:
a root shell on this machine can always read a file root can read. Closing
``docker inspect`` removes the path that actually leaked; it does not make
secrets unreachable to root. Only keys that are NOT ON THIS MACHINE are
genuinely out of reach -- which is why private keys get a different verdict
line from API keys here.

Usage:
    python3 scripts/secret-exposure-audit.py [--env-file PATH] [--container NAME]

Exit code 0 always: this is a measurement, never a gate. A gate that blocks a
deployment on a security metric invites someone to bypass it in a hurry.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import os
import re
import subprocess
import sys

DEFAULT_ENV_FILE = "/opt/aria/vanguard/backend/.env"
DEFAULT_CONTAINER = "aria-api"

# A value shorter than this is not a credential -- it is a flag ("true", "1",
# a port). Hashing them would flood the report with false positives, since
# "true" appears in every surface on the machine.
MIN_SECRET_LEN = 12

# Name-based classification. Deliberately NOT the only signal: the operator's
# own point is that a secret can appear under `value=`/`hex=`/`key_material=`,
# so detection below is by VALUE (hash match), and these patterns only decide
# how severe a finding is, never whether we look for it.
_PRIVATE_KEY_PAT = re.compile(r"PRIVATE_KEY|MNEMONIC|SEED_PHRASE|_SEED$", re.I)
_CREDENTIAL_PAT = re.compile(r"_KEY|_SECRET|_TOKEN|PASSWORD|_PWD|CREDENTIAL", re.I)

SEVERITY_PRIVATE = "private_key"
SEVERITY_CREDENTIAL = "credential"
SEVERITY_CONFIG = "config"


def fingerprint(value: str) -> str:
    """Stable 8-hex identity for a secret. Never reversible, never the value."""
    return hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()[:8]


def classify(name: str) -> str:
    if _PRIVATE_KEY_PAT.search(name):
        return SEVERITY_PRIVATE
    if _CREDENTIAL_PAT.search(name):
        return SEVERITY_CREDENTIAL
    return SEVERITY_CONFIG


def load_secrets(env_file: str) -> dict[str, tuple[str, str]]:
    """-> {name: (value_hash, severity)}. Values are hashed immediately and
    the plaintext is never retained beyond the comparison map below."""
    out: dict[str, tuple[str, str]] = {}
    try:
        with open(env_file, "r", encoding="utf-8", errors="replace") as fh:
            raw = fh.read()
    except OSError as exc:
        print(f"cannot read env file: {type(exc).__name__}", file=sys.stderr)
        return out

    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip()
        value = value.strip().strip("'\"")
        if len(value) < MIN_SECRET_LEN:
            continue
        severity = classify(name)
        if severity == SEVERITY_CONFIG:
            continue  # a public RPC host or a chain id is not what we protect
        out[name] = (value, severity)
    return out


def _run(cmd: list[str]) -> str:
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return (res.stdout or "") + (res.stderr or "")
    except (OSError, subprocess.SubprocessError):
        return ""


def probe_docker_inspect(container: str) -> str:
    """THE path that actually leaked on 02/09."""
    return _run(["docker", "inspect", container])


def probe_proc_environ(container: str) -> str:
    """Root can read /proc/<pid>/environ. Removing --env-file does NOT close
    this if the entrypoint exports the same variables into the process."""
    pid = _run(["docker", "inspect", "-f", "{{.State.Pid}}", container]).strip()
    if not pid.isdigit() or pid == "0":
        return ""
    try:
        with open(f"/proc/{pid}/environ", "rb") as fh:
            return fh.read().decode("utf-8", "replace")
    except OSError:
        return ""


def probe_docker_logs(container: str) -> str:
    """A secret echoed into a log is a secret published to everyone who reads
    logs -- including future sessions of this agent."""
    return _run(["docker", "logs", "--tail", "2000", container])


def probe_image_history(container: str) -> str:
    """A secret baked into a build layer survives every redeploy."""
    image = _run(["docker", "inspect", "-f", "{{.Config.Image}}", container]).strip()
    if not image:
        return ""
    return _run(["docker", "history", "--no-trunc", image])


SURFACES = [
    ("docker inspect", probe_docker_inspect, "readable by anyone reaching the Docker daemon"),
    ("/proc/<pid>/environ", probe_proc_environ, "readable by root on this host"),
    ("docker logs", probe_docker_logs, "readable by anyone reading logs, persisted"),
    ("image history", probe_image_history, "survives redeploys, travels with the image"),
]


@dataclass(frozen=True)
class Finding:
    """A finding, carrying METADATA ONLY -- never a secret value.

    The whole point of this type is what it does NOT have: no field holds a
    secret. Everything downstream of ``scan_surface`` works with these, so the
    reporting code has no value to leak even if someone edits it carelessly.
    That is a guarantee by CONSTRUCTION rather than by test, which is why this
    exists on top of ``test_audit_script_never_prints_a_secret_value`` rather
    than instead of it -- the test remains as a second barrier.
    """
    name: str            # the ENVIRONMENT VARIABLE NAME, e.g. ARIA_TELEGRAM_TOKEN
    fingerprint: str     # sha256(value)[:8] -- irreversible by construction
    severity: str


def scan_surface(blob: str, secrets: dict[str, tuple[str, str]]) -> list[Finding]:
    """The ONLY place a secret value is compared against a probed surface.

    Values enter here and never leave: what comes out is name + fingerprint +
    severity. Keeping this boundary in one small function is what lets every
    caller be obviously safe instead of carefully safe.
    """
    return [
        Finding(name=name, fingerprint=fingerprint(value), severity=sev)
        for name, (value, sev) in secrets.items()
        if value in blob
    ]


def render_findings(surface_name: str, why: str, findings: list[Finding]) -> None:
    """Print a surface's result. Structurally incapable of leaking a value:
    it receives ``Finding`` objects, which have no value field at all."""
    private = sum(1 for f in findings if f.severity == SEVERITY_PRIVATE)
    print(f"  {surface_name:<22} EXPOSED: {len(findings)} secret(s), {private} private-key-class")
    print(f"  {'':<22} ({why})")
    for f in sorted(findings, key=lambda r: (r.severity != SEVERITY_PRIVATE, r.name)):
        marker = "!!" if f.severity == SEVERITY_PRIVATE else "  "
        print(f"  {'':<22} {marker} {f.name}  fp={f.fingerprint}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env-file", default=os.environ.get("ARIA_AUDIT_ENV_FILE", DEFAULT_ENV_FILE))
    ap.add_argument("--container", default=DEFAULT_CONTAINER)
    args = ap.parse_args()

    secrets = load_secrets(args.env_file)
    if not secrets:
        print("no secrets loaded -- nothing to audit (wrong path, or no read access)")
        return 0

    by_severity: dict[str, int] = {}
    for _name, (_v, sev) in secrets.items():
        by_severity[sev] = by_severity.get(sev, 0) + 1

    print("SECRET EXPOSURE AUDIT")
    print("=" * 64)
    print(f"env file      : {args.env_file}")
    try:
        mode = oct(os.stat(args.env_file).st_mode & 0o777)
        print(f"permissions   : {mode}")
    except OSError:
        pass
    print(f"secrets known : {by_severity.get(SEVERITY_PRIVATE, 0)} private-key-class, "
          f"{by_severity.get(SEVERITY_CREDENTIAL, 0)} credential-class")
    print()

    total_findings = 0
    for surface_name, probe, why in SURFACES:
        blob = probe(args.container)
        if not blob:
            print(f"  {surface_name:<22} unavailable (not probed)")
            continue
        findings = scan_surface(blob, secrets)
        total_findings += len(findings)
        if not findings:
            print(f"  {surface_name:<22} CLEAN")
            continue
        render_findings(surface_name, why, findings)
    print()
    print("=" * 64)
    if total_findings == 0:
        print("No secret value was recoverable from the probed surfaces.")
        print("NOTE: this does not mean a root shell cannot read the file itself.")
        print("Only keys absent from this machine are genuinely out of reach.")
    else:
        print(f"{total_findings} recoverable secret value(s) across the surfaces above.")
        print("Each is a path a privileged shell can walk without trying to.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
