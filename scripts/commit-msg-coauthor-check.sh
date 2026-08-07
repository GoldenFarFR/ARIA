#!/usr/bin/env bash
# Mechanized guard (08/07, operator-delegated "choi toi" -- see CLAUDE.md format
# router): every Claude-authored commit in this repo must carry BOTH
# Co-Authored-By lines (Claude + GoldenFarFR) so the work shows up in the
# operator's own GitHub activity -- an explicit, previously-manual-only
# instruction (memory: feedback_dual_coauthor_commits.md) that had already
# been forgotten once before it was written down.
#
# Auto-fixes rather than rejects: if the message already carries the Claude
# co-author line (i.e. this IS a Claude Code commit) but is missing the
# GoldenFarFR one, append it in place and let the commit through -- never
# blocks a commit, so automated cron-driven commits (research-log promotion,
# backlog promotion, etc., all running through headless `claude -p` in this
# same checkout) self-heal instead of silently shipping without the line.
# A commit with NEITHER line (an operator commit made directly, not through
# Claude Code) is left untouched -- not this hook's business.
set -euo pipefail

MSG_FILE="$1"
CLAUDE_LINE="Co-Authored-By: Claude <noreply@anthropic.com>"
OPERATOR_LINE="Co-Authored-By: GoldenFarFR <sylvain.rio.fr@gmail.com>"

if grep -qF "Co-Authored-By: Claude" "$MSG_FILE" && ! grep -qF "$OPERATOR_LINE" "$MSG_FILE"; then
    printf '%s\n' "$OPERATOR_LINE" >> "$MSG_FILE"
fi

exit 0
