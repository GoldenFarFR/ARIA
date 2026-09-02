#!/usr/bin/env python3
"""Load configuration from a MOUNTED file instead of Docker's ``--env-file``.

Why this exists (02/09, written after a real leak): ``docker run --env-file``
copies every variable into the container's Docker metadata, where
``docker inspect`` returns them in clear text to anyone who reaches the Docker
daemon. A session running a broad ``grep`` over that output printed a private
key. The rule against displaying secrets was already written and had already
been broken twice in July -- so the correction is not more discipline, it is
removing the path.

Measured before the change (``scripts/secret-exposure-audit.py``):
``docker inspect`` exposed 62 secret values including 2 private keys. Mounting
the file read-only and parsing it here closes exactly that path.

**The limit, stated so nobody over-trusts this**: the variables still land in
``os.environ``, so ``/proc/<pid>/environ`` still exposes them to root on this
host. That is deliberate -- every consumer in ``aria_core`` reads
``os.environ``, and rewriting that is a refactor, not a fix. What changes is
that the ACCIDENTAL path is gone: ``docker inspect`` is what a session runs to
check a gate, and it now returns configuration only. Reading
``/proc/<pid>/environ`` takes deliberate intent, which is a different risk
class. Private keys are not addressed by this at all -- their answer is not
being on this machine (see CLAUDE.md's ``local acp-cli signing`` rule).

**Why a Python parser and not ``. /run/aria/env`` in the shell**: the two
parse differently, and the difference is a security bug, not a nuisance.
Docker takes everything after the first ``=`` literally; ``sh`` interprets
whitespace, ``$``, backticks and quotes -- so a value containing a backtick
would EXECUTE at boot. Measured on the real file before choosing: 202
variables, 0 quoted, 0 containing ``$`` or a backtick, but **1 containing a
space** -- which alone is enough to make ``.`` silently wrong. The parser
below reproduces Docker's rule exactly.

Never prints a variable name or value. The boot line reports a COUNT, which is
what tells an operator the mount worked without telling anyone what is in it.
"""
from __future__ import annotations

import os
import sys

ENV_MOUNT_PATH = "/run/aria/env"

# Escape hatch for local runs and CI, where no file is mounted and variables
# arrive via -e. Deliberately explicit: a silent start with no configuration
# would leave ARIA running with every external client unauthenticated, which
# is far worse than refusing to boot.
OPTIONAL_FLAG = "ARIA_ENV_FILE_OPTIONAL"


def load_env_file(path: str) -> int:
    """Apply Docker's own --env-file semantics. Returns how many were set.

    Docker's rules, reproduced deliberately rather than approximated:
      - blank lines and lines whose first non-space character is ``#`` are skipped
      - a line without ``=`` is skipped (Docker would pass it through from the
        host environment; here there is no host environment to inherit from)
      - everything after the FIRST ``=`` is the value, verbatim -- no quote
        stripping, no expansion, no escape handling
    """
    applied = 0
    with open(path, "r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.rstrip("\n").rstrip("\r")
            stripped = line.lstrip()
            if not stripped or stripped.startswith("#") or "=" not in line:
                continue
            name, _, value = line.partition("=")
            name = name.strip()
            if not name:
                continue
            # An explicit `-e NAME=...` on the docker run line wins. deploy.sh
            # relies on this for ARIA_HEARTBEAT_STANDBY, and it keeps a
            # one-off override possible without editing the mounted file.
            if name in os.environ:
                continue
            os.environ[name] = value
            applied += 1
    return applied


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("entrypoint: no command to exec", file=sys.stderr)
        return 2

    if os.path.exists(ENV_MOUNT_PATH):
        try:
            count = load_env_file(ENV_MOUNT_PATH)
        except OSError as exc:
            # Type only -- an OSError's message can carry the path, and the
            # path is not secret, but keeping the habit narrow costs nothing.
            print(f"entrypoint: cannot read mounted env ({type(exc).__name__})", file=sys.stderr)
            return 3
        print(f"entrypoint: loaded {count} variables from mounted env file", flush=True)
    elif os.environ.get(OPTIONAL_FLAG, "").strip().lower() in {"1", "true", "yes"}:
        print("entrypoint: no mounted env file, continuing (explicitly optional)", flush=True)
    else:
        print(
            f"entrypoint: {ENV_MOUNT_PATH} is missing and {OPTIONAL_FLAG} is not set -- "
            "refusing to boot unconfigured",
            file=sys.stderr,
        )
        return 4

    os.execvp(argv[1], argv[1:])
    return 0  # unreachable: execvp replaces this process


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
