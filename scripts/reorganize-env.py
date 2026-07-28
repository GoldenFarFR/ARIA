#!/usr/bin/env python3
"""Reorganizes a .env file into 3 sections: secrets/API keys, boolean gates
(true/false), then everything else -- operator-requested (27/07): "ranger
les cle dun cote et les valeur false true de lautre".

Never touches the file's actual values -- pure line reordering. Always
writes a timestamped backup (.env.bak.<UTC ISO 8601 timestamp, colons
stripped for filesystem safety>) before overwriting, same doctrine as every
other .env-touching operation in this project (never overwrite without a
backup). Meant to be run by the OPERATOR directly on their own terminal --
not yet whitelisted in block-secret-display.sh, so a Claude Code session
cannot invoke it itself (same restriction as any other .env write); add it
there first if a future session needs to run it.

Same sensitive-name keyword list as show-env-safe.sh/block-secret-display.sh
(rule 2) -- keep in sync if either changes.
"""
from __future__ import annotations

import re
import shutil
import sys
from datetime import datetime, timezone

SENSITIVE_RE = re.compile(
    r"(TOKEN|SECRET|KEY|PASSWORD|PASS|AUTH|CREDENTIAL|PRIVATE|MNEMONIC|SIGNATURE|CERT)",
    re.IGNORECASE,
)


def _categorize(name: str, value: str) -> str:
    if SENSITIVE_RE.search(name):
        return "secrets"
    if value.strip().lower() in ("true", "false"):
        return "gates"
    return "other"


def main() -> None:
    if len(sys.argv) != 2:
        print("usage: reorganize-env.py <path-to-.env>", file=sys.stderr)
        sys.exit(1)

    path = sys.argv[1]
    with open(path, encoding="utf-8") as f:
        lines = f.read().splitlines()

    secrets: list[str] = []
    gates: list[str] = []
    other: list[str] = []
    leading_comments: list[str] = []
    pending_comment: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            pending_comment.append(line)
            continue
        if "=" not in line:
            # Malformed line -- preserve as-is in "other" rather than drop it.
            other.append(line)
            pending_comment = []
            continue
        name, _, value = line.partition("=")
        name = name.strip()
        bucket = _categorize(name, value)
        entry = "\n".join(pending_comment + [line]) if pending_comment else line
        {"secrets": secrets, "gates": gates, "other": other}[bucket].append(entry)
        pending_comment = []

    # Any trailing comments with no following KEY=VALUE line (e.g. a
    # section header at the file's very end) -- keep them, never drop text.
    if pending_comment:
        leading_comments = pending_comment

    ts = datetime.now(timezone.utc).isoformat().replace(":", "")
    backup_path = f"{path}.bak.{ts}"
    shutil.copy2(path, backup_path)

    sections = [
        ("# --- Secrets / API keys ---", secrets),
        ("# --- Gates (true/false) ---", gates),
        ("# --- Other values ---", other),
    ]
    out_lines: list[str] = []
    for header, entries in sections:
        if not entries:
            continue
        out_lines.append(header)
        out_lines.extend(entries)
        out_lines.append("")
    if leading_comments:
        out_lines.extend(leading_comments)

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out_lines).rstrip("\n") + "\n")

    print(f"Backup written to {backup_path}")
    print(f"Reorganized: {len(secrets)} secrets, {len(gates)} gates, {len(other)} other")


if __name__ == "__main__":
    main()
