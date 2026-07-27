#!/bin/bash
# Safe .env viewer -- prints every KEY=VALUE line IN FULL, except lines whose
# variable name looks like a secret (same keyword list as rule 2 of
# .claude/hooks/block-secret-display.sh, duplicated here rather than sourced
# so this script has no runtime dependency on the hook's internals -- keep
# both lists in sync if either changes): those show only the value's first 4
# and last 4 characters (e.g. GROK_API_KEY=xai-...ZxZCN), a short value (<=10
# chars, where 4+4 would reveal nearly all of it) is fully masked instead.
#
# Operator-requested design (27/07): "je veux que chaque ligne du .env te
# renvoi tout mais pour les clef les 4 premiers et dernier characteres" --
# explicit, precise alternative to the previous all-or-nothing block (see
# memory feedback_never_display_secrets.md). Whitelisted BY EXACT PATH in
# block-secret-display.sh -- this is the only command allowed to read a
# .env file's raw content; any other command (cat/grep/printenv/...) on a
# .env file remains fully blocked, unchanged.
#
# Never sources/executes the file -- pure line-by-line text parsing, so a
# value containing shell-special characters can never run as code.

set -euo pipefail

FILE="${1:?usage: show-env-safe.sh <path-to-.env>}"

if [ ! -f "$FILE" ]; then
  echo "No such file: $FILE" >&2
  exit 1
fi

# Same keyword list as block-secret-display.sh rule 2 -- keep in sync.
SENSITIVE_RE='(TOKEN|SECRET|KEY|PASSWORD|PASS|AUTH|CREDENTIAL|PRIVATE|MNEMONIC|SIGNATURE|CERT)'

shopt -s nocasematch

while IFS= read -r line || [ -n "$line" ]; do
  # Blank lines and comments pass through untouched.
  if [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]]; then
    echo "$line"
    continue
  fi
  if [[ "$line" != *"="* ]]; then
    echo "$line"
    continue
  fi
  name="${line%%=*}"
  value="${line#*=}"
  if [[ "$name" =~ $SENSITIVE_RE ]]; then
    len=${#value}
    if [ "$len" -gt 10 ]; then
      echo "${name}=${value:0:4}...${value: -4}"
    else
      echo "${name}=***"
    fi
  else
    echo "$line"
  fi
done < "$FILE"
