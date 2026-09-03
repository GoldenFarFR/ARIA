#!/bin/bash
# Claude Code Remote Control -- supervised launcher.
#
# Two nested safety nets, so an unattended VPS survives weeks without a human:
#   1. this internal loop restarts `claude remote-control` in place, keeping the
#      tmux session (and its scrollback) alive across a crash or a disconnect;
#   2. systemd restarts the whole unit if the tmux server itself dies
#      (Restart=always + StartLimitIntervalSec=0, i.e. no give-up threshold).
#
# It also degrades: if the CLI rejects our optional flags (version drift), it
# falls back to the bare invocation rather than looping on a startup error.

export HOME=/root
export PATH=/usr/local/bin:/usr/bin:/bin

LOG_DIR=/opt/aria-data/claude-remote
LOG="$LOG_DIR/launcher.log"
mkdir -p "$LOG_DIR"

log() { echo "[$(date -Is)] $*" >> "$LOG"; }

# Resolve the CLI without pinning a Node version: nvm's copy is the one kept
# up to date on this host, /usr/bin/claude is the system fallback. Pinning a
# path like .../v22.23.2/... would silently break on the next Node upgrade.
resolve_cli() {
    local candidate
    candidate=$(ls -d /root/.nvm/versions/node/*/bin/claude 2>/dev/null | sort -V | tail -1)
    if [ -x "$candidate" ]; then
        echo "$candidate"
    else
        echo /usr/bin/claude
    fi
}

cd /opt/aria || { log "FATAL: /opt/aria unreachable"; exit 1; }

# Overridable so this one script can back several independent persistent
# sessions (each its own tmux session + systemd unit), not just aria-vps.
SESSION_NAME="${SESSION_NAME:-aria-vps}"
backoff=5
short_runs=0

while true; do
    CLAUDE_BIN=$(resolve_cli)

    # `--continue` reattaches to the conversation this directory last used.
    # NOTE (03/09, corrected): the "~4h, then it errors out" claim that used to
    # be here was never actually verified -- its own introducing commit
    # (bf450354) said so explicitly ("Not verified: the first real restart").
    # Official docs (code.claude.com/docs/en/sessions) document no such cutoff:
    # local transcripts default to a 30-day retention (`cleanupPeriodDays`),
    # and the only time-based behavior is prompt-cache expiry (~1h idle, >100k
    # tokens), which changes the cost of the next request, never blocks resume.
    # We still try `--continue` first and fall through on failure below --
    # that fallback is correct regardless of what the real cutoff turns out to
    # be, so no logic here depends on the false "~4h" number.
    if [ "$short_runs" -ge 2 ]; then
        # Repeated instant exits: stop passing optional flags entirely.
        ARGS=(remote-control)
        log "degraded mode: launching with no optional flags"
    elif [ -n "$SKIP_CONTINUE" ]; then
        ARGS=(remote-control --name "$SESSION_NAME")
    else
        ARGS=(remote-control --name "$SESSION_NAME" --continue)
    fi

    log "starting: $CLAUDE_BIN ${ARGS[*]}"
    started=$(date +%s)

    # -e propagates the child exit code; without it `script` always returns 0.
    script -q -e -c "$CLAUDE_BIN ${ARGS[*]}" /dev/null
    code=$?

    ran=$(( $(date +%s) - started ))
    log "exited code=$code after ${ran}s"

    if [ "$ran" -ge 120 ]; then
        # Held for a while: a normal disconnect, not a fault. Retry fast, and
        # allow a reattach attempt again on the next round.
        backoff=5
        short_runs=0
        unset SKIP_CONTINUE
    else
        short_runs=$(( short_runs + 1 ))
        # An instant exit right after trying --continue is the expected
        # "nothing recorded here recently" error: retry immediately without it.
        if [ -z "$SKIP_CONTINUE" ] && [ "$short_runs" -eq 1 ]; then
            export SKIP_CONTINUE=1
            log "reattach unavailable, retrying with a fresh session"
            continue
        fi
        backoff=$(( backoff * 2 ))
        [ "$backoff" -gt 300 ] && backoff=300
    fi

    log "sleeping ${backoff}s before restart"
    sleep "$backoff"
done
