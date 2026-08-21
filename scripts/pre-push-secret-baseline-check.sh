#!/usr/bin/env bash
# pre-push-secret-baseline-check.sh -- blocks a push that would turn the
# GitHub "Security -- secret scan" job red.
#
# Why this exists (21/08): the secret-scan workflow had been failing on EVERY
# push for seven hours, 18 consecutive runs, and nobody noticed -- not the
# agent, who has an explicit standing instruction to check this baseline
# before pushing and simply did not, and not the operator, until he opened
# the Actions tab by chance. Both causes were harmless false positives (the
# SPL Token Program address and the USDC mint, flagged as high-entropy base64
# like every base58 account address is), but a permanently red CI is worse
# than no CI at all: it becomes noise, and the day it reports a REAL secret
# nobody reacts.
#
# A text instruction was already in place and was not enough. This is the
# mechanical version of it -- same reasoning as pre-push-regression-check.sh,
# which exists because "remember to run the tests" was likewise not enough.
#
# Mirrors .github/workflows/secrets-scan.yml EXACTLY (same excludes, same
# baseline-diff comparison), so passing here means passing there. If that
# workflow changes, change this too.
#
# Never blocks on infrastructure trouble (detect-secrets missing, unreadable
# baseline): same dome doctrine as every other guard here -- fail open on
# tooling, closed only on a real finding.
set -uo pipefail

REPO_DIR="/opt/aria"
BASELINE="$REPO_DIR/.secrets.baseline"
PY="$REPO_DIR/packages/aria-core/.venv/bin/python"
SCANNER="$REPO_DIR/packages/aria-core/.venv/bin/detect-secrets"

cd "$REPO_DIR" 2>/dev/null || exit 0

[ -x "$SCANNER" ] || { echo "pre-push-secret-baseline: detect-secrets absent -- skip"; exit 0; }
[ -f "$BASELINE" ] || { echo "pre-push-secret-baseline: pas de baseline -- skip"; exit 0; }

SCAN_OUT="$(mktemp)"
trap 'rm -f "$SCAN_OUT"' EXIT

"$SCANNER" scan \
  --exclude-files '\.venv/' \
  --exclude-files 'node_modules/' \
  --exclude-files '__pycache__/' \
  --exclude-files '\.git/' \
  --exclude-files 'dist/' \
  --exclude-files 'build/' \
  --exclude-files '\.next/' \
  --exclude-files 'package-lock\.json' \
  --exclude-files '\.secrets\.baseline' \
  > "$SCAN_OUT" 2>/dev/null || {
    echo "pre-push-secret-baseline: le scan n'a pas abouti -- skip (jamais bloquer sur l'outil)"
    exit 0
  }

"$PY" - "$BASELINE" "$SCAN_OUT" <<'PYEOF'
import json, sys

try:
    baseline = json.load(open(sys.argv[1]))["results"]
    current = json.load(open(sys.argv[2]))["results"]
except Exception as exc:  # unreadable files are an infra problem, not a finding
    print(f"pre-push-secret-baseline: lecture impossible ({exc}) -- skip")
    sys.exit(0)

def hashes(results):
    return {(f, i["hashed_secret"]) for f, items in results.items() for i in items}

new = hashes(current) - hashes(baseline)
if not new:
    print("pre-push-secret-baseline: OK, rien de nouveau hors baseline")
    sys.exit(0)

print("")
print("PUSH BLOQUE -- detect-secrets trouve des entrees absentes du baseline audite :")
for fname, h in sorted(new):
    for item in current.get(fname, []):
        if item["hashed_secret"] == h:
            print(f"   {fname}:{item['line_number']}  [{item['type']}]")
print("")
print("Si c'est un VRAI secret : retire-le, puis fais tourner la rotation.")
print("Si c'est un faux positif (adresse publique, fixture de test), audite-le :")
print("   packages/aria-core/.venv/bin/detect-secrets scan --baseline .secrets.baseline \\")
print("     --exclude-files '\\.venv/' --exclude-files 'node_modules/' \\")
print("     --exclude-files '__pycache__/' --exclude-files '\\.git/' \\")
print("     --exclude-files 'dist/' --exclude-files 'build/' --exclude-files '\\.next/' \\")
print("     --exclude-files 'package-lock\\.json' --exclude-files '\\.secrets\\.baseline'")
print("   puis commit .secrets.baseline avec le reste.")
print("")
sys.exit(1)
PYEOF
