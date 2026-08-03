#!/bin/bash
# SessionStart hook, matcher "compact" -- reinjects critical guardrail reminders
# right after a context compaction, before the model's next turn.
#
# Why this exists: PostCompact is a real hook event, but its stdout is IGNORED for
# side effects (verified against code.claude.com/docs/en/hooks, 03/08) -- it can only
# log/monitor, never inject context. SessionStart with matcher "compact" is the
# actual mechanism whose stdout IS injected (source field == "compact" in that case).
# Kept as its own script (not folded into session-start.sh, which reads no stdin
# JSON today and handles a different concern -- venv bootstrap).
#
# Deliberately short (~20 lines of reminder text): this is a safety net for the
# highest-risk drift after compaction (losing track of financial guardrails or
# switching language), not a restatement of CLAUDE.md -- the file itself is always
# reloaded in full, this just re-anchors on what's cost the most when missed before.
set -uo pipefail

cat >/dev/null 2>&1 || true

CONTEXT=$(cat <<'CONTEXT_EOF'
RAPPEL POST-COMPACTION (contexte venant d'etre compacte) -- CLAUDE.md a ete relu integralement, mais ces points ont deja derape apres compaction dans cette session :
- Reste en francais, y compris le raisonnement visible -- jamais de derapage anglais.
- Capital reel : jamais de trade automatique sans validation Telegram, sauf les 4 exceptions NOMMEES et bornees dans "Regles absolues" (Sepolia/paper-trading/pilote 10-15$/transfert USDC) -- au-dela, validation humaine integrale.
- Ne jamais modifier permission_mode/wallet_guard/regles-uniques/config.toml sans "ok" explicite, meme pour "normaliser".
- Verifier avant d'affirmer -- un fait cite de memoire (gate, chiffre, etat de deploiement) doit etre reverifie, jamais suppose a partir d'un resume compacte.
- Si tu etais en plein commit/workflow/tache multi-etapes : confirme ou tu en etais avant de continuer, ne suppose pas.
CONTEXT_EOF
)

jq -n --arg ctx "$CONTEXT" '{
  hookSpecificOutput: {
    hookEventName: "SessionStart",
    additionalContext: $ctx
  }
}' 2>/dev/null || printf '%s\n' "$CONTEXT"

exit 0
