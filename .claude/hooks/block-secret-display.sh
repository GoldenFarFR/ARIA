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
# Revised same day after two real bugs found by review (verified empirically,
# not just theorized, before fixing):
#   - the original `grep -q` detector only looked at the FIRST option token
#     right after "grep " -- `grep -i -q FOO` (a separate `-q` token) slipped
#     through undetected. Fixed to look for -q/-c ANYWHERE after "grep".
#   - `.env` reads were only guarded for cat/head/tail/less/more -- `grep
#     PATTERN .env` (without -q) prints the whole matching line, same leak
#     shape as the printenv incident. Added, with the same -q/-c exception
#     (a `grep -q` on a .env file is exactly the safe pattern used all over
#     this session, e.g. `grep -q "^VAR=" .env`).
#
# Scope, deliberately narrow (never blocks a legitimate `grep -q`/`grep -c`
# existence check, only the read side of a value):
#   1. `printenv`/`env` NOT combined with `grep -q`/`grep -c` anywhere in the
#      same command -- covers the bare form AND the piped form
#      (`printenv | grep X`), which is exactly the incident's shape.
#   2. `echo`/`printf` of a variable whose NAME suggests a secret (a
#      best-effort keyword list -- the REAL, exhaustive protection against
#      any name is rule 1 above, which never depends on naming).
#   3. `grep` (without -q/-c) on any `.env` file -- same leak shape as rule 1,
#      scoped to the file instead of the process environment.
#   4. `cat`/`head`/`tail`/`less`/`more`/`bat`/`nano`/`vim` on any `.env`
#      file -- these have no silent/count-only mode, always blocked.

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

# A "safe" grep -q/-c is present ANYWHERE in the command -- not just as the
# first option token right after "grep" (grep -i -q FOO must count too).
_has_safe_grep() {
  printf '%s' "$COMMAND" | grep -qE 'grep\b.*-[a-zA-Z]*[qc][a-zA-Z]*([[:space:]]|$)'
}

# 1) printenv/env listing variables -- unless the SAME command also pipes
# through grep -q/-c (those never print the matched content, only a
# status/count).
if printf '%s' "$COMMAND" | grep -qE '(^|[;&|]|[[:space:]])(printenv|env)([[:space:]]|$)'; then
  if ! _has_safe_grep; then
    block "prints environment variable VALUES in cleartext (printenv/env without grep -q/-c)"
  fi
fi

# 2) echo/printf of a variable whose name suggests a secret. Best-effort
# keyword list (never the primary defense -- rule 1 above already covers
# every name unconditionally); kept generous but avoids overly generic
# words (e.g. "URL" alone) that would false-positive on legitimate,
# non-secret variables (PUBLIC_SITE_URL, API_BASE_URL...).
if printf '%s' "$COMMAND" | grep -qiE '(echo|printf)[^|;&]*\$\{?[A-Z0-9_]*(TOKEN|SECRET|KEY|PASSWORD|PASS|AUTH|CREDENTIAL|PRIVATE|MNEMONIC|SIGNATURE|CERT)[A-Z0-9_]*\}?'; then
  block "echoes a variable whose name looks like a secret"
fi

# 3) grep (without -q/-c) directly on an .env file -- prints the whole
# matching line, same leak shape as rule 1.
if printf '%s' "$COMMAND" | grep -qiE '(^|[;&|]|[[:space:]])grep[[:space:]].*\.env([.'\''"[:space:]]|$)'; then
  if ! _has_safe_grep; then
    block "greps an .env file without -q/-c -- would print the matching line's VALUE"
  fi
fi

# 4) dumping an .env file's contents with a tool that has no silent mode.
if printf '%s' "$COMMAND" | grep -qiE '(^|[;&|]|[[:space:]])(cat|head|tail|less|more|bat|nano|vim)[[:space:]]+.*\.env([.'\''"[:space:]]|$)'; then
  block "would dump an .env file's contents"
fi

exit 0
