#!/bin/bash
# Read-only snapshot of ARIA's real *_ENABLED gates, straight from the live
# aria-api container config -- mechanizes the "verify before affirming"
# doctrine (CLAUDE.md, Règles absolues) instead of relying on each session's
# discipline to re-check before citing a gate's state.
#
# Born from a real, repeated failure mode (04/08): CLAUDE.md's own
# "Established facts" section has diverged from the real gate state THREE
# times already (bonding discovery, Polymarket paper, cadence #108) -- each
# time a session cited the doc instead of checking. This script is the fix:
# injected at every SessionStart (see .claude/hooks/gate-status-injector.sh),
# it prints the REAL current value, sourced from Docker's own container
# config (`docker inspect`, works even if the container is unhealthy/stopped,
# never requires a live `exec`).
#
# Strict scope, by design (operator-approved, 04/08):
#   - STRICT pattern filter (^ARIA_[A-Z0-9_]*ENABLED=) -- only boolean
#     feature-gate names ever printed. Never a full env dump: secrets
#     (API keys, tokens, private material) live in the SAME container env
#     and must never appear here, even by accident.
#   - Curated SENSITIVE_GATES list (below) always gets an explicit line,
#     even when the gate is ABSENT from the environment entirely -- absence
#     means "off by default", but a silent omission would look identical to
#     "checked and confirmed off", which is exactly the ambiguity this
#     script exists to remove.
#   - FAIL-CLOSED: any failure (docker unreachable, container missing) prints
#     a clear "unavailable" line and a non-zero-friendly message -- never a
#     silent empty output that could be mistaken for "zero gates enabled".
#   - READ-ONLY: `docker inspect` only, never `exec`/`run`, never writes
#     anything anywhere.
set -uo pipefail

CONTAINER="${1:-aria-api}"

# Curated list of capital-adjacent / explicitly-"VERIFY"-flagged gates (per
# CLAUDE.md's own "Règles absolues" and "Established facts" sections, 04/08).
# Kept SHORT and manually curated -- this is not "every gate that exists"
# (70+ of those), only the ones where a stale assumption has real
# consequences (real capital, a dormant-but-wired mechanism, or a gate
# CLAUDE.md explicitly says to re-verify rather than trust the doc).
SENSITIVE_GATES=(
  ARIA_AGENT_WALLET_PILOT_ENABLED
  ARIA_AGENT_WALLET_TRANSFER_ENABLED
  ARIA_X402_SELLER_ENABLED
  ARIA_X402_SELLER_MAINNET
  ARIA_BONDING_DISCOVERY_ENABLED
  ARIA_CABALSPY_SOURCING_ENABLED
  ARIA_POLYMARKET_PAPER_ENABLED
  ARIA_POLYMARKET_REAL_TRADING_ENABLED
  ARIA_DIRECTIVE_CHANNEL_ENABLED
  ARIA_PAPER_TRADING_ENABLED
  ARIA_SEPOLIA_AUTONOMOUS_ENABLED
  ARIA_SEPOLIA_SWAP_ENABLED
)

if ! command -v docker >/dev/null 2>&1; then
  echo "## État des gates ARIA : INDISPONIBLE"
  echo "docker introuvable dans cet environnement -- aucun etat de gate ne peut etre lu (fail-closed, jamais suppose)."
  exit 0
fi

ENV_RAW="$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$CONTAINER" 2>&1)"
STATUS=$?

if [ "$STATUS" -ne 0 ]; then
  echo "## État des gates ARIA : INDISPONIBLE"
  echo "docker inspect sur '$CONTAINER' a echoue (conteneur absent ou docker injoignable) -- ne jamais se fier a CLAUDE.md seul pour l'etat des gates tant que cette commande echoue."
  echo "Détail : $ENV_RAW" | head -3
  exit 0
fi

GATES="$(printf '%s\n' "$ENV_RAW" | grep -E '^ARIA_[A-Z0-9_]*ENABLED=' | sort)"

echo "## État réel des gates ARIA (conteneur '$CONTAINER', lu maintenant via docker inspect)"
echo
if [ -n "$GATES" ]; then
  echo "$GATES"
else
  echo "(aucun gate ARIA_*_ENABLED trouve dans l'environnement du conteneur)"
fi

echo
echo "### Gates sensibles (capital reel / a verifier, verbatim CLAUDE.md) -- toujours listes meme absents"
for gate in "${SENSITIVE_GATES[@]}"; do
  line="$(printf '%s\n' "$ENV_RAW" | grep -E "^${gate}=" || true)"
  if [ -n "$line" ]; then
    echo "$line"
  else
    echo "${gate}=absent (defaut OFF -- variable non definie dans le conteneur)"
  fi
done
