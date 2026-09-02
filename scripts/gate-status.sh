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

# Source des gates : le FICHIER MONTÉ, plus `docker inspect` (02/09).
# Pourquoi ce changement : --env-file recopiait chaque secret dans les
# métadonnées Docker, ce qui a fait fuiter une clé privée -- le conteneur lit
# désormais un montage lecture seule (vanguard/docker-entrypoint.py), donc
# `docker inspect` ne contient plus que la configuration Docker elle-même.
# Le filtre strict ci-dessous est INCHANGÉ et reste la vraie garantie : seul
# un booléen `ARIA_*_ENABLED=` peut sortir d'ici, jamais une valeur de secret.
ENV_FILE="${ARIA_ENV_FILE:-/opt/aria/vanguard/backend/.env}"

if [ ! -r "$ENV_FILE" ]; then
  echo "## État des gates ARIA : INDISPONIBLE"
  echo "fichier de configuration illisible ('$ENV_FILE') -- ne jamais se fier a CLAUDE.md seul pour l'etat des gates tant que c'est le cas."
  exit 0
fi

# On ne garde JAMAIS le fichier entier en variable : un grep direct, pour que
# rien d'autre qu'un booléen ne transite par ce script.
ENV_RAW="$(grep -E '^(ARIA_[A-Z0-9_]*ENABLED|ARIA_X402_SELLER_MAINNET)=' "$ENV_FILE" || true)"
GATES="$(printf '%s\n' "$ENV_RAW" | grep -E '^ARIA_[A-Z0-9_]*ENABLED=' | sort)"

# Divergence fichier/conteneur : le conteneur charge ce fichier AU DÉMARRAGE.
# Modifié depuis, il annonce donc un état que le process ne suit pas encore --
# angle mort que l'ancienne lecture via `docker inspect` ne détectait pas non plus,
# et qui vaut mieux affiché qu'implicite.
STALE_WARNING=""
STARTED_AT="$(docker inspect -f '{{.State.StartedAt}}' "$CONTAINER" 2>/dev/null || true)"
if [ -n "$STARTED_AT" ]; then
  START_EPOCH="$(date -d "$STARTED_AT" +%s 2>/dev/null || echo 0)"
  FILE_EPOCH="$(stat -c %Y "$ENV_FILE" 2>/dev/null || echo 0)"
  if [ "$START_EPOCH" -gt 0 ] && [ "$FILE_EPOCH" -gt "$START_EPOCH" ]; then
    STALE_WARNING="⚠️  Le fichier a été modifié APRÈS le démarrage du conteneur : les valeurs ci-dessous ne sont pas encore celles que le process applique (redéploiement requis)."
  fi
fi

echo "## État réel des gates ARIA (lu maintenant dans la configuration montée)"
[ -n "$STALE_WARNING" ] && { echo; echo "$STALE_WARNING"; }
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
