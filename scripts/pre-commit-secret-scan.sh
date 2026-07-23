#!/usr/bin/env bash
# pre-commit-secret-scan.sh -- mechanical guard against committing a real
# secret (operator request, 23/07, after two prior leaks via this session's
# own Bash commands -- see feedback_never_display_secrets memory). Called by
# the .git/hooks/pre-commit stub (not versioned -- THIS script is the
# versioned logic, same pattern as scripts/devils-advocate-review.sh for
# pre-push).
#
# Scans ONLY the currently staged diff (gitleaks protect --staged), not the
# full repo history -- fast, and scoped to what's actually about to be
# committed. Config (.gitleaks.toml) allowlists two known false-positive
# sources verified by hand during the 23/07 audit: .secrets.baseline
# (SHA1 hashes, never raw values) and vanguard/backend/security_sim/corpus.py
# (deliberate fake attack payloads for our own security-sim tests).
#
# Fails CLOSED: blocks the commit if gitleaks finds a match, or if gitleaks
# itself is missing on this machine (a security guard that silently does
# nothing is worse than no guard -- better to block and surface the problem
# than let it pass unnoticed).
set -uo pipefail

REPO_DIR="/opt/aria"
GITLEAKS_BIN="${GITLEAKS_BIN:-/usr/local/bin/gitleaks}"

cd "$REPO_DIR" || exit 1

if [ ! -x "$GITLEAKS_BIN" ]; then
    echo "🚨 pre-commit secret scan: gitleaks not found at $GITLEAKS_BIN -- commit blocked." >&2
    echo "   Install it (see docs/HANDOFF_SECURITE.md) or set GITLEAKS_BIN to its path." >&2
    exit 1
fi

"$GITLEAKS_BIN" protect --staged --config "$REPO_DIR/.gitleaks.toml" --no-banner
status=$?

if [ "$status" -ne 0 ]; then
    echo "🚨 pre-commit secret scan: possible secret detected in staged changes -- commit blocked." >&2
    echo "   Review the finding above. If it's a genuine false positive, add a scoped" >&2
    echo "   allowlist entry to .gitleaks.toml (never bypass with --no-verify by default)." >&2
    exit 1
fi

exit 0
