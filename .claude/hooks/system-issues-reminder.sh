#!/bin/bash
# SessionStart hook -- surfaces the centralized system_issues registry
# (aria_core.system_issues, 11/08) at the start of every session, same
# proven pattern as signal-cascade-queue-reminder.sh (generalized: ANY
# watchdog/audit can open an issue here, not just the signal cascade).
#
# Why: explicit operator request ("je veut que se soit comme github si il y
# a un probleme tu a des notification dans une fichier et cest a toi de les
# fermer ou de les reparer") -- before this, each watchdog wrote findings
# into its OWN markdown log under its OWN directory, nothing surfaced them
# automatically. Read-only, fails silent (no VPS DB in a web/cloud session,
# sqlite3 missing, table not yet created).
set -uo pipefail

DB_PATH="${ARIA_DB_PATH:-/opt/aria-data/aria.db}"

[ -f "$DB_PATH" ] || exit 0
command -v sqlite3 >/dev/null 2>&1 || exit 0

COUNT="$(sqlite3 -readonly "$DB_PATH" \
  "SELECT COUNT(*) FROM system_issues WHERE status = 'open'" 2>/dev/null)"
[ -z "$COUNT" ] || [ "$COUNT" = "0" ] && exit 0

ROWS="$(sqlite3 -readonly -separator '|' "$DB_PATH" \
  "SELECT id, source, title, severity FROM system_issues WHERE status = 'open' \
   ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END ASC, \
   opened_at DESC LIMIT 10" 2>/dev/null)"
[ -z "$ROWS" ] && exit 0

LIST=""
while IFS='|' read -r id source title severity; do
  [ -z "$id" ] && continue
  LIST="${LIST}- #${id} [${severity}] (${source}) ${title}
"
done <<< "$ROWS"

CONTEXT="SYSTEM ISSUES -- REGISTRE CENTRAL OUVERT (${COUNT} au total, top 10, critique d'abord) :
${LIST}Chaque issue vient d'un watchdog/audit mecanique -- ferme-la (aria_core.system_issues.close_issue(id, raison)) si c'est un faux positif ou deja resolu, sinon corrige le probleme reel puis ferme-la avec le commit/fix comme raison. Jamais laisser une issue ouverte sans y toucher a chaque session."

jq -n --arg ctx "$CONTEXT" '{
  hookSpecificOutput: {
    hookEventName: "SessionStart",
    additionalContext: $ctx
  }
}' 2>/dev/null || printf '%s\n' "$CONTEXT"

exit 0
