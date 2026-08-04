#!/bin/bash
# SessionStart hook -- injects the REAL current state of ARIA's *_ENABLED
# gates (scripts/gate-status.sh, read-only, docker inspect) as additional
# context at the start of every session (any source: startup, resume,
# clear, compact).
#
# Why: CLAUDE.md's own "Established facts" section has diverged from the
# real gate state three times already (04/08, see scripts/gate-status.sh's
# own header) -- each time because a session cited the doc instead of
# checking. This mechanizes "vérifier avant d'affirmer" instead of relying
# on each session remembering to do it by hand.
#
# Same JSON output pattern as session-compact-reminder.sh (stdout IS
# injected via hookSpecificOutput.additionalContext for SessionStart).
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${CLAUDE_PROJECT_DIR:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
GATE_STATUS="$ROOT/scripts/gate-status.sh"

if [ ! -x "$GATE_STATUS" ]; then
  exit 0  # script missing/not executable -- fail silent, never block session start
fi

OUTPUT="$("$GATE_STATUS" 2>&1)"
[ -z "$OUTPUT" ] && exit 0

jq -n --arg ctx "$OUTPUT" '{
  hookSpecificOutput: {
    hookEventName: "SessionStart",
    additionalContext: $ctx
  }
}' 2>/dev/null || printf '%s\n' "$OUTPUT"

exit 0
