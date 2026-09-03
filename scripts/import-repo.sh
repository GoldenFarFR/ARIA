#!/usr/bin/env bash
# Import a GoldenFarFR repository on demand (03/09, operator decision:
# "le repo officiel de travail soit aria et les autres ils doivent les importer
# si je leur demande d'y travailler").
#
# ARIA is the only permanently checked-out repository on this host. Everything
# else is pulled in when the operator actually asks for work on it, then can be
# dropped again -- keeping one obvious working tree instead of a drawer of
# half-stale clones.
#
# Why this script exists rather than a plain `gh repo clone`: `gh` is configured
# with `git_protocol: ssh`, and the scoped deploy keys on this host do not cover
# every repository. Those come back as "ERROR: Repository not found", which reads
# like the repo was deleted -- it is really an auth failure. This falls back to
# HTTPS through gh's credential helper, and never writes a token into .git/config.
#
# Usage:  ./scripts/import-repo.sh <repo-name> [--drop]
#         ./scripts/import-repo.sh --list
set -uo pipefail

WORKDIR=/opt/repos
OWNER=GoldenFarFR

usage() { echo "Usage: $0 <repo-name> [--drop] | --list"; exit 1; }
[ $# -ge 1 ] || usage

if [ "$1" = "--list" ]; then
    echo "Repositories available on $OWNER:"
    gh repo list "$OWNER" --limit 50 --json name,visibility,isArchived \
        --template '{{range .}}  {{.name}}{{if .isArchived}} (archived){{end}}{{"\n"}}{{end}}' 2>/dev/null \
        || { echo "  gh unavailable or not authenticated"; exit 1; }
    echo
    echo "Currently imported under $WORKDIR:"
    if [ -d "$WORKDIR" ] && [ -n "$(ls -A "$WORKDIR" 2>/dev/null)" ]; then
        for d in "$WORKDIR"/*/; do [ -d "$d/.git" ] && echo "  $(basename "$d")"; done
    else
        echo "  (none -- ARIA at /opt/aria is the permanent working tree)"
    fi
    exit 0
fi

REPO="$1"
DEST="$WORKDIR/$REPO"

if [ "${2:-}" = "--drop" ]; then
    [ -d "$DEST/.git" ] || { echo "Not imported: $REPO"; exit 1; }
    if [ -n "$(git -C "$DEST" status --porcelain 2>/dev/null)" ]; then
        echo "Refusing to drop $REPO: it has uncommitted changes." >&2
        git -C "$DEST" status --short >&2
        exit 1
    fi
    unpushed=$(git -C "$DEST" log --branches --not --remotes --oneline 2>/dev/null | wc -l)
    if [ "$unpushed" -gt 0 ]; then
        echo "Refusing to drop $REPO: $unpushed commit(s) not pushed to any remote." >&2
        exit 1
    fi
    rm -rf "$DEST" && echo "Dropped $REPO (was clean and fully pushed)."
    exit 0
fi

mkdir -p "$WORKDIR"

if [ -d "$DEST/.git" ]; then
    echo "Already imported, refreshing $REPO..."
    git -c credential.helper='!gh auth git-credential' -C "$DEST" fetch --all --quiet \
        && git -C "$DEST" status --short --branch | head -1
    exit $?
fi

echo "Importing $OWNER/$REPO into $DEST..."
if git -c credential.helper='!gh auth git-credential' \
       clone --quiet "https://github.com/$OWNER/$REPO.git" "$DEST" 2>/dev/null; then
    # Leave a neutral HTTPS remote: credentials are supplied per-command by gh,
    # never persisted next to the checkout.
    git -C "$DEST" remote set-url origin "https://github.com/$OWNER/$REPO.git"
    echo "Imported: $DEST ($(git -C "$DEST" branch --show-current))"
else
    echo "Import failed for $OWNER/$REPO." >&2
    echo "Check the name with: $0 --list" >&2
    exit 1
fi
