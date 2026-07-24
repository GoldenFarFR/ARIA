#!/bin/bash
# PreToolUse hook (Bash) -- mechanically blocks any command that would print a
# secret's VALUE in cleartext, instead of relying on discipline alone.
#
# Real, repeated incident: `docker exec aria-api printenv | grep -i
# ARIA_BRAIN` (missing `-q`) leaked a GitHub PAT into a session transcript on
# 24/07 -- the 3rd occurrence of this exact class of mistake despite already
# being a documented rule ("never display a secret's value, even via grep").
# A rule that keeps getting broken needs a mechanical block, not another
# reminder -- see memory feedback_never_display_secrets.md.
#
# Scope, deliberately narrow (never blocks a legitimate `grep -q`/`grep -c`
# existence check, only the read side of a value):
#   1. `printenv`/`env` NOT combined with `grep -q`/`grep -c` anywhere in the
#      same command -- covers the bare form AND the piped form
#      (`printenv | grep X`), which is exactly the incident's shape.
#   2. `echo`/`printf` of a variable whose NAME suggests a secret
#      (TOKEN/KEY/SECRET/PASSWORD/CREDENTIAL).
#   3. `cat`/`head`/`tail`/`less`/`more` on any `.env` file.

set -euo pipefail

INPUT="$(cat)"
COMMAND="$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null || true)"

if [ -z "$COMMAND" ]; then
  exit 0
fi

block() {
  echo "BLOCKED (block-secret-display.sh): $1" >&2
  echo "Command: $COMMAND" >&2
  echo "Fix: use 'grep -q <pattern> <file>' (silent, exit-code only) to check existence, never a form that prints the value." >&2
  exit 2
}

# 1) printenv/env listing variables -- unless the SAME command also pipes
# through grep -q/-c (those never print the matched content, only a
# status/count).
if printf '%s' "$COMMAND" | grep -qE '(^|[;&|]|[[:space:]])(printenv|env)([[:space:]]|$)'; then
  if ! printf '%s' "$COMMAND" | grep -qE 'grep[[:space:]]+-[a-zA-Z]*[qc]'; then
    block "prints environment variable VALUES in cleartext (printenv/env without grep -q/-c)"
  fi
fi

# 2) echo/printf of a variable whose name suggests a secret.
if printf '%s' "$COMMAND" | grep -qiE '(echo|printf)[^|;&]*\$\{?[A-Z0-9_]*(TOKEN|SECRET|KEY|PASSWORD|CREDENTIAL)[A-Z0-9_]*\}?'; then
  block "echoes a variable whose name looks like a secret"
fi

# 3) dumping an .env file's contents.
if printf '%s' "$COMMAND" | grep -qiE '(^|[;&|]|[[:space:]])(cat|head|tail|less|more)[[:space:]]+.*\.env([.'\''"[:space:]]|$)'; then
  block "would dump an .env file's contents"
fi

exit 0
