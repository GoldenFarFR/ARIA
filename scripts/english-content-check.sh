#!/usr/bin/env bash
# english-content-check.sh -- mechanized tripwire for CLAUDE.md's own 23/07
# rule ("repo content en anglais: code/commentaires/docstrings/commit
# messages/CLAUDE.md/HANDOFF"). Built 11/08 after a real recurrence: three
# HANDOFF_AUTOMATISATION.md/HANDOFF_BLOCKSCOUT.md entries were written
# straight in French mid-session (the internal reasoning language leaking
# into repo content) and only caught because the operator noticed by eye.
#
# Scope deliberately narrow to the two places that have actually drifted:
# CLAUDE.md/docs/HANDOFF_*.md prose, and '#' comments in tracked .py/.sh
# files. Does NOT parse Python docstrings (unreliable via grep/diff alone,
# real risk of false positives) and does NOT touch product-facing strings
# (Telegram/vitrine copy is legitimately French) -- comments and HANDOFF
# prose are the only categories that caused a real incident so far.
#
# Alert-only, same doctrine as guardrail-file-alert.sh (07/08 operator
# decision: a mechanical check must never block a legitimate commit,
# especially one with real false-positive risk like a heuristic language
# detector). Called from the .git/hooks/pre-commit stub, always exits 0.
set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR" || exit 0

ALERT_LOG="/opt/aria-data/english-content-alerts.log"

STAGED="$(git diff --cached --name-only 2>/dev/null || true)"
[ -z "$STAGED" ] && exit 0

TARGETS=()
while IFS= read -r f; do
    [ -z "$f" ] && continue
    case "$f" in
        CLAUDE.md) TARGETS+=("$f") ;;
        docs/HANDOFF_*.md) TARGETS+=("$f") ;;
        *.py) TARGETS+=("$f") ;;
        *.sh) TARGETS+=("$f") ;;
    esac
done <<< "$STAGED"

[ "${#TARGETS[@]}" -eq 0 ] && exit 0

# Word-boundary French stopwords -- deliberately common function words that
# essentially never appear in English prose/comments, to keep false
# positives near zero. Accented characters alone are already a near-certain
# signal on their own (no legitimate English comment uses them).
STOPWORDS='\b(le|la|les|des|une|est|pour|dans|avec|jamais|toujours|donc|que|qui|sur|par|pas|etre|ainsi|cette|ces|leur|nous|vous|sont|ete|tout|sans|comme|mais|meme|chaque|aucun|aucune|apres|avant|entre|sous|quand)\b'
ACCENTS='[àâäéèêëïîôöùûüçÀÂÉÈÊËÏÎÔÖÙÛÜÇ]'

HITS=()
for f in "${TARGETS[@]}"; do
    [ -f "$f" ] || continue
    # Added lines only (diff '+' prefix stripped), quoted substrings removed
    # first so a legitimate verbatim operator quote never triggers a hit.
    while IFS= read -r line; do
        [ -z "$line" ] && continue
        clean="$(printf '%s' "$line" | sed -E 's/"[^"]*"//g; s/'"'"'[^'"'"']*'"'"'//g')"
        case "$f" in
            *.py|*.sh)
                # Only '#' comment lines -- code/strings are out of scope
                # (real product-facing French strings are legitimate there).
                printf '%s' "$clean" | grep -qE '^\s*#' || continue
                ;;
        esac
        has_accent=0
        printf '%s' "$clean" | grep -qP "$ACCENTS" 2>/dev/null && has_accent=1
        stopword_count=$(printf '%s' "$clean" | grep -oiE "$STOPWORDS" 2>/dev/null | wc -l | tr -d ' ')
        if [ "$has_accent" -eq 1 ] || [ "${stopword_count:-0}" -ge 3 ]; then
            HITS+=("$f: ${line:0:120}")
        fi
    done < <(git diff --cached -- "$f" | grep -E '^\+' | grep -vE '^\+\+\+' | sed -E 's/^\+//')
done

[ "${#HITS[@]}" -eq 0 ] && exit 0

TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
{
    echo "=========================================="
    echo "🇫🇷  POSSIBLE FRENCH TEXT IN REPO CONTENT (should be English)"
    echo "=========================================="
    for h in "${HITS[@]}"; do echo "   - $h"; done
    echo ""
    echo "CLAUDE.md rule (23/07): code/comments/docstrings/commit messages/CLAUDE.md/HANDOFF stay English."
    echo "Verbatim operator quotes in \"...\" are already excluded above -- a hit here is real prose."
    echo "This check never blocks the commit (same doctrine as guardrail-file-alert.sh) -- verify by eye."
} >&2

mkdir -p "$(dirname "$ALERT_LOG")" 2>/dev/null || true
{
    echo "[$TS] possible French content staged: ${#HITS[@]} line(s) across ${TARGETS[*]}"
} >> "$ALERT_LOG" 2>/dev/null || true

exit 0
