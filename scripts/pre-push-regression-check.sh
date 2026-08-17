#!/usr/bin/env bash
# pre-push-regression-check.sh -- mechanical regression gate, runs BEFORE
# devils-advocate-review.sh in the pre-push hook chain (17/08, operator
# request: neither session-checkpoint.sh's push-auto instruction nor
# devils-advocate-review.sh's Fable 5 call ever actually RUN pytest --
# both only ever asked the agent to remember to check, or reviewed
# architecture, never functional correctness. This closes that gap with a
# real, blocking, mechanical test run -- can't be forgotten or skipped
# under pressure the way a text instruction can.
#
# Scope: tests EVERYTHING accumulated since the last real VPS deployment
# (.claude/last-deployed-ref, the SAME marker session-checkpoint.sh already
# uses for its own 500-line push-auto counter) -- not just this push's own
# diff, so a regression introduced several pushes ago but never yet tested
# together still gets caught before it reaches prod.
#
# Targeted, not exhaustive: running the full suite (~577s) on every push
# would contradict the operator's own "push often, small batches" doctrine
# (17/08). Instead, maps each changed source module to every test file
# that actually mentions it (grep, not a naming-convention guess -- the
# convention isn't uniform, e.g. dexpaprika.py -> test_dexpaprika_client.py,
# not test_dexpaprika.py) plus test_coherence.py always (the project's
# central invariant gate).
#
# Never blocks on infrastructure failure (missing venv, no ref file yet) --
# only blocks on a REAL failing test, same "never fabricate/never fail
# unsafe" dome doctrine as the rest of this codebase's external clients.
set -uo pipefail

REPO_DIR="/opt/aria"
CORE_DIR="$REPO_DIR/packages/aria-core"
REF_FILE="$REPO_DIR/.claude/last-deployed-ref"
VENV_PYTEST="$CORE_DIR/.venv/bin/pytest"

cd "$REPO_DIR" 2>/dev/null || exit 0

if [ ! -f "$REF_FILE" ]; then
  echo "pre-push-regression-check: pas de marker last-deployed-ref -- skip (rien a comparer)"
  exit 0
fi
ref=$(tr -d '[:space:]' < "$REF_FILE")
[ -z "$ref" ] && exit 0
git cat-file -e "${ref}^{commit}" 2>/dev/null || { echo "pre-push-regression-check: ref inconnu -- skip"; exit 0; }

target=$(git rev-parse HEAD 2>/dev/null)
[ -z "$target" ] && exit 0

changed=$(git diff --name-only "$ref" "$target" -- 'packages/aria-core/src/aria_core/*.py' 2>/dev/null)
if [ -z "$changed" ]; then
  echo "pre-push-regression-check: aucun fichier Python source modifie depuis le dernier deploiement -- skip"
  exit 0
fi

if [ ! -x "$VENV_PYTEST" ]; then
  echo "pre-push-regression-check: venv pytest introuvable ($VENV_PYTEST) -- skip (jamais bloquant sur un env casse)"
  exit 0
fi

declare -A test_files
while IFS= read -r f; do
  module=$(basename "$f" .py)
  [ "$module" = "__init__" ] && continue
  while IFS= read -r t; do
    [ -n "$t" ] && test_files["$t"]=1
  done < <(grep -rlw "$module" "$CORE_DIR/tests" --include="test_*.py" 2>/dev/null)
done <<< "$changed"

coherence="$CORE_DIR/tests/test_coherence.py"
[ -f "$coherence" ] && test_files["$coherence"]=1

n_changed=$(echo "$changed" | grep -c .)
if [ "${#test_files[@]}" -eq 0 ]; then
  echo "pre-push-regression-check: AVERTISSEMENT -- $n_changed fichier(s) source modifie(s) sans test correspondant trouve (jamais bloquant pour cette raison seule)"
  exit 0
fi

echo "pre-push-regression-check: ${#test_files[@]} fichier(s) de test cible(s) pour $n_changed fichier(s) source modifie(s) depuis ${ref:0:12}"

if ! "$VENV_PYTEST" -q "${!test_files[@]}"; then
  echo ""
  echo "pre-push-regression-check: ECHEC -- au moins un test cible a echoue sur le cumul depuis ${ref:0:12}."
  echo "PUSH BLOQUE. Corrige le test avant de pousser."
  exit 1
fi

echo "pre-push-regression-check: OK -- tous les tests cibles passent."
exit 0
