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
DIFF_TRUNCATED=""
if [ "$DIFF_LEN" -gt 60000 ]; then
  DIFF_CONTENT="${DIFF_CONTENT:0:60000}"
  DIFF_TRUNCATED="

[... diff tronque a 60000 caracteres sur $DIFF_LEN, analyse partielle ...]"
fi

OR_KEY=$(grep '^OPENROUTER_API_KEY=' "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2-)
if [ -z "$OR_KEY" ]; then
  echo "OPENROUTER_API_KEY introuvable dans $ENV_FILE." >&2
  exit 1
fi

INBOX_INDEX=$(ls "$REPO_DIR"/docs/aria-learning-inbox/*.md 2>/dev/null | xargs -n1 basename 2>/dev/null)
[ -z "$INBOX_INDEX" ] && INBOX_INDEX="(aucune fiche deposee pour l'instant)"

STATUS_TMP=$(mktemp /tmp/devils-advocate-precommit-status.XXXXXX)
RAW_RESPONSE=$(devils_advocate_call "${DIFF_CONTENT}${DIFF_TRUNCATED}" "$INBOX_INDEX" "$OR_KEY" 2>"$STATUS_TMP")
HTTP_STATUS=$(grep -oE 'HTTP_STATUS:[0-9]+' "$STATUS_TMP" | cut -d: -f2)
rm -f "$STATUS_TMP"
unset OR_KEY

echo "-- diff analyse : ${DIFF_SOURCE}, ${DIFF_LEN} caracteres --" >&2

if [ "$HTTP_STATUS" != "200" ]; then
  echo "ECHEC -- HTTP status: ${HTTP_STATUS}." >&2
  echo "$RAW_RESPONSE" >&2
  exit 1
fi

FINISH_REASON=$(echo "$RAW_RESPONSE" | jq -r '.choices[0].finish_reason // "inconnu"' 2>/dev/null)
if [ "$FINISH_REASON" = "length" ]; then
  echo "ATTENTION -- reponse TRONQUEE (finish_reason=length, max_tokens atteint) -- incomplete." >&2
fi

echo "$RAW_RESPONSE" | jq -r '.choices[0].message.content // "ECHEC: reponse 200 sans contenu exploitable."'
