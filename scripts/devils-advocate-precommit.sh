#!/usr/bin/env bash
# devils-advocate-precommit.sh -- verification SYNCHRONE, AVANT un commit officiel
# (decision operateur explicite, 04/08 : "avant de commit tu lui envoi ta copie, il
# te fait un rapport, si il te retourne quelque chose tu reviens me le dire ici et
# on corrige, si sa correction et inutile ou qu'il n'y a pas tu commit officiellement").
#
# Distinct de devils-advocate-review.sh (async, post-push, ecrit un rapport que la
# PROCHAINE session doit penser a lire -- trou reel trouve en direct le meme jour,
# l'operateur payait des appels jamais lus). Celui-ci est appele MANUELLEMENT par
# la session Claude Code EN COURS, avant `git commit`, sur le diff des changements
# non commits -- reponse imprimee directement sur stdout, lue et relayee a
# l'operateur dans la MEME conversation, jamais un fichier a lire "plus tard".
#
# Meme modele/prompt que le hook async (devils-advocate-lib.sh, jamais une
# reimplementation qui pourrait diverger). Ne modifie rien, n'ecrit rien sur
# disque (sauf un fichier temporaire nettoye a la fin) -- aucune commande git.
set -uo pipefail

REPO_DIR="/opt/aria"
ENV_FILE="$REPO_DIR/vanguard/backend/.env"

cd "$REPO_DIR" || exit 1
# shellcheck source=./devils-advocate-lib.sh
source "$REPO_DIR/scripts/devils-advocate-lib.sh"

# Diff STAGED en priorite (ce qui va reellement partir dans `git commit`) --
# retombe sur le diff du working tree si rien n'est encore stage (cas frequent :
# la session verifie AVANT meme de faire `git add`).
DIFF_CONTENT=$(git diff --cached 2>/dev/null)
DIFF_SOURCE="staged"
if [ -z "$DIFF_CONTENT" ]; then
  DIFF_CONTENT=$(git diff 2>/dev/null)
  DIFF_SOURCE="working tree (rien de stage)"
fi

if [ -z "$DIFF_CONTENT" ]; then
  echo "Aucun diff a analyser (rien de modifie/stage)." >&2
  exit 1
fi

DIFF_LEN=${#DIFF_CONTENT}

# 10/08 -- API Anthropic directe (ANTHROPIC_API_KEY), remplace OPENROUTER_API_KEY
# apres un vrai incident credits OpenRouter epuises (HTTP 402).
API_KEY=$(grep '^ANTHROPIC_API_KEY=' "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2-)
if [ -z "$API_KEY" ]; then
  echo "ANTHROPIC_API_KEY introuvable dans $ENV_FILE." >&2
  exit 1
fi

INBOX_INDEX=$(ls "$REPO_DIR"/docs/aria-learning-inbox/*.md 2>/dev/null | xargs -n1 basename 2>/dev/null)
[ -z "$INBOX_INDEX" ] && INBOX_INDEX="(aucune fiche deposee pour l'instant)"

# 10/08 -- condensation Haiku 4.5 (jamais une troncature brute) si le diff
# depasse DEVILS_ADVOCATE_CONDENSE_THRESHOLD_CHARS -- voir devils-advocate-lib.sh.
USER_DIFF_CONTENT=$(devils_advocate_diff_for_review "$DIFF_CONTENT" "$API_KEY" "precommit-$(date -u +%Y%m%dT%H%M%S)")

STATUS_TMP=$(mktemp /tmp/devils-advocate-precommit-status.XXXXXX)
RAW_RESPONSE=$(devils_advocate_call "$USER_DIFF_CONTENT" "$INBOX_INDEX" "$API_KEY" 2>"$STATUS_TMP")
HTTP_STATUS=$(grep -oE 'HTTP_STATUS:[0-9]+' "$STATUS_TMP" | cut -d: -f2)
rm -f "$STATUS_TMP"
unset API_KEY

echo "-- diff analyse : ${DIFF_SOURCE}, ${DIFF_LEN} caracteres --" >&2

if [ "$HTTP_STATUS" != "200" ]; then
  echo "ECHEC -- HTTP status: ${HTTP_STATUS}." >&2
  echo "$RAW_RESPONSE" >&2
  exit 1
fi

STOP_REASON=$(echo "$RAW_RESPONSE" | jq -r '.stop_reason // "inconnu"' 2>/dev/null)
if [ "$STOP_REASON" = "max_tokens" ]; then
  echo "ATTENTION -- reponse TRONQUEE (stop_reason=max_tokens) -- incomplete." >&2
fi

read -r IN_TOKENS OUT_TOKENS COST_USD <<< "$(devils_advocate_cost "$RAW_RESPONSE")"
devils_advocate_log_cost "precommit-$(date -u +%Y%m%dT%H%M%S)" "$IN_TOKENS" "$OUT_TOKENS" "$COST_USD"
echo "-- cout reel : \$${COST_USD} (${IN_TOKENS} tokens input, ${OUT_TOKENS} tokens output) --" >&2

echo "$RAW_RESPONSE" | jq -r '[.content[]? | select(.type == "text") | .text] | join("\n\n") | if . == "" then "ECHEC: reponse 200 sans contenu exploitable." else . end'
