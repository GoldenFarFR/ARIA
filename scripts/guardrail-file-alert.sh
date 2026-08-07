#!/usr/bin/env bash
# guardrail-file-alert.sh -- mechanized tripwire on ARIA's own guardrail files
# (07/08, operator-delegated "choi toi" candidate #4, explicit follow-up
# confirmation: "choisi il faut pas que sa te bloque"). CLAUDE.md's absolute
# rule already says "never modify permission_mode/wallet_guard/regles-uniques/
# config.toml without explicit validation" -- this makes a staged change to
# any of them impossible to miss, WITHOUT ever technically blocking the
# commit: the operator was explicit that this must never lock out legitimate,
# already-approved work. Detection + durable logging only, never enforcement.
#
# Called from the .git/hooks/pre-commit stub alongside pre-commit-secret-scan.sh
# (that one fails closed on secrets; this one never does -- different jobs).
#
# Concrete targets resolved against the real repo (checked 07/08, not guessed
# from the CLAUDE.md prose alone):
#   - packages/aria-core/src/aria_core/wallet_guard.py   (real file)
#   - template-grok-cursor/.cursor/rules/regles-uniques.mdc (real file)
#   - .claude/settings.json                              (closest real proxy
#     for "permission_mode" -- no file literally named that exists in the repo)
#   - any tracked config.toml (glob, excluding *.example) -- no real one exists
#     yet (only template-grok-cursor/.grok/config.toml.example), covered so a
#     future real one is caught automatically without a hardcoded dead path.
set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR" || exit 0

ALERT_LOG="/opt/aria-data/guardrail-file-alerts.log"

STAGED="$(git diff --cached --name-only 2>/dev/null || true)"
[ -z "$STAGED" ] && exit 0

HITS=()
while IFS= read -r f; do
    [ -z "$f" ] && continue
    case "$f" in
        packages/aria-core/src/aria_core/wallet_guard.py) HITS+=("$f") ;;
        template-grok-cursor/.cursor/rules/regles-uniques.mdc) HITS+=("$f") ;;
        .claude/settings.json) HITS+=("$f") ;;
        *config.toml) [[ "$f" == *.example ]] || HITS+=("$f") ;;
    esac
done <<< "$STAGED"

[ "${#HITS[@]}" -eq 0 ] && exit 0

TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
{
    echo "=========================================="
    echo "⚠️  GUARDRAIL FILE CHANGE STAGED FOR COMMIT"
    echo "=========================================="
    for f in "${HITS[@]}"; do echo "   - $f"; done
    echo ""
    echo "CLAUDE.md rule: never modify without explicit operator 'ok', even to 'normalize'."
    echo "This alert never blocks the commit (operator decision, 07/08) -- verify the 'ok' was real."
} >&2

mkdir -p "$(dirname "$ALERT_LOG")" 2>/dev/null || true
{
    echo "[$TS] guardrail file(s) staged: ${HITS[*]}"
} >> "$ALERT_LOG" 2>/dev/null || true

exit 0
