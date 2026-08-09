#!/bin/bash
# SessionStart hook -- surfaces the signal cascade's pending triage queue
# (stage 4, signal_cascade_convergence.py) at the start of every session.
#
# Why: the operator design explicitly requires this queue be PERSISTENT
# (never a volatile notification) because Claude Code sessions are
# intermittent -- a candidate must survive between sessions until a human
# (Claude, at first) actually triages it with a reasoning, not just a
# yes/no. Without this hook, the queue would exist but nobody would ever
# think to check it. Read-only, fails silent (no VPS DB in a web/cloud
# session, sqlite3 missing, table not yet created).
set -uo pipefail

DB_PATH="${ARIA_DB_PATH:-/opt/aria-data/aria.db}"

[ -f "$DB_PATH" ] || exit 0
command -v sqlite3 >/dev/null 2>&1 || exit 0

ROWS="$(sqlite3 -readonly -separator '|' "$DB_PATH" \
  "SELECT symbol, contract, convergence_count FROM signal_cascade_triage_queue \
   WHERE status = 'pending' ORDER BY convergence_count DESC, queued_at ASC LIMIT 5" 2>/dev/null)"
[ -z "$ROWS" ] && exit 0

COUNT="$(sqlite3 -readonly "$DB_PATH" \
  "SELECT COUNT(*) FROM signal_cascade_triage_queue WHERE status = 'pending'" 2>/dev/null)"

LIST=""
while IFS='|' read -r symbol contract convergence; do
  [ -z "$contract" ] && continue
  LIST="${LIST}- ${symbol:-?} (${contract}) -- ${convergence} source(s) concordante(s)
"
done <<< "$ROWS"

CONTEXT="CASCADE DE SIGNAUX -- FILE DE TRIAGE EN ATTENTE (${COUNT} au total, top 5) :
${LIST}Pour trier : aria_core.signal_cascade_convergence.record_triage_decision(contract, chain, 'validated'|'rejected', raisonnement) -- le raisonnement est obligatoire (jamais juste oui/non)."

jq -n --arg ctx "$CONTEXT" '{
  hookSpecificOutput: {
    hookEventName: "SessionStart",
    additionalContext: $ctx
  }
}' 2>/dev/null || printf '%s\n' "$CONTEXT"

exit 0
