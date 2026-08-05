#!/usr/bin/env bash
# devils-advocate-review.sh -- "Avocat du Diable" : critique architecturale
# asynchrone post-push (decision operateur explicite, 18/07, conception
# croisee avec Gemini -- voir CLAUDE.md "Automatismes en place"). Appele par
# le hook .git/hooks/pre-push (non versionne -- CE script est la logique
# versionnee, le hook n'est qu'un declencheur d'une ligne).
#
# Role : un modele qui relit le diff qui vient de partir sur main et redige
# une critique structuree (complexite inutile, limites a l'echelle,
# alternative radicale SI pertinente + plan de transition obligatoire).
# Ecrit un rapport, rien d'autre -- aucune execution, aucun acces au code,
# aucune commande git. La session suivante le lit et DOIT verifier chaque
# affirmation avant d'agir dessus (jamais gober -- meme discipline que toute
# revue croisee externe deja pratiquee dans ce projet).
#
# 04/08 -- ORIGINALEMENT un modele/lab DIFFERENT de celui qui ecrit le code
# (jamais le meme qui se juge lui-meme -- Gemini 3.1 Pro, jamais Claude).
# Doctrine explicitement RENVERSEE par decision operateur le meme jour, apres
# une comparaison directe (meme diff, meme prompt, Gemini vs Claude Fable 5)
# ou Fable 5 a trouve le meme angle mort SANS l'erreur factuelle de Gemini et
# a propose une architecture meilleure (voir devils-advocate-lib.sh, section
# "modele officialise"). Verdict operateur : la doctrine cross-lab s'annule
# devant une efficacite prouvee superieure -- garde cette note comme trace de
# la decision et de son inversion, jamais reecrite silencieusement.
#
# Ne bloque JAMAIS le push : tout le travail reel tourne en arriere-plan,
# detache. Ne se declenche QUE sur un push touchant refs/heads/main (jamais
# sur une branche temporaire claude/*-temp -- bruit et cout inutiles).
#
# 04/08 -- modele/prompt/appel API extraits dans devils-advocate-lib.sh
# (partage avec devils-advocate-precommit.sh, la verification SYNCHRONE
# demandee par l'operateur pour capter la critique AVANT un commit officiel,
# pas seulement apres un push deja parti) -- jamais une deuxieme copie qui
# pourrait diverger silencieusement.
#
# 04/08 (meme jour) -- gap reel trouve en direct (l'operateur : "je paye pour
# rien", appels payes jamais lus) : le rapport unique ecrase a CHAQUE push
# perdait silencieusement tout rapport intermediaire sur deux pushs
# rapproches, et le rappel de lecture (session-checkpoint.sh) ne suivait que
# le DERNIER etat. Confirme independamment par Gemini ET Claude Fable 5 sur
# le meme diff de test (comparaison directe, meme session) -- les deux
# convergent sur la meme proposition : une vraie file d'attente plutot qu'un
# fichier ecrase. Remplace REPORT_FILE par un repertoire PENDING_DIR (un
# fichier par push, jamais ecrase) ; "lu" devient un geste explicite
# (deplacement vers archived/, voir session-checkpoint.sh) plutot qu'un
# throttle qui perd silencieusement ce qui arrive entre deux lectures.
set -uo pipefail

REPO_DIR="/opt/aria"
PENDING_DIR="/opt/aria-data/architect-reports/pending"
REVIEW_LOG="/opt/aria-data/architect-review.log"
ENV_FILE="$REPO_DIR/vanguard/backend/.env"
ZERO_SHA="0000000000000000000000000000000000000000"

mkdir -p "$PENDING_DIR" 2>/dev/null || true

cd "$REPO_DIR" || exit 1
# shellcheck source=./devils-advocate-lib.sh
source "$REPO_DIR/scripts/devils-advocate-lib.sh"
MODEL="$DEVILS_ADVOCATE_MODEL"

# Le hook pre-push recoit sur stdin une ligne par ref poussee :
# <local ref> <local sha1> <remote ref> <remote sha1>
MAIN_LOCAL_SHA=""
MAIN_REMOTE_SHA=""
while read -r local_ref local_sha remote_ref remote_sha; do
  if [ "$remote_ref" = "refs/heads/main" ]; then
    MAIN_LOCAL_SHA="$local_sha"
    MAIN_REMOTE_SHA="$remote_sha"
  fi
done

[ -z "$MAIN_LOCAL_SHA" ] && exit 0          # main non concerne -- silencieux
[ "$MAIN_LOCAL_SHA" = "$ZERO_SHA" ] && exit 0  # suppression de branche

# Un fichier PAR push (nom = sha complet, jamais de collision) -- jamais
# ecrase par le push suivant, voir le commentaire de tete sur PENDING_DIR.
REPORT_FILE="$PENDING_DIR/${MAIN_LOCAL_SHA}.md"

if [ "$MAIN_REMOTE_SHA" = "$ZERO_SHA" ]; then
  DIFF_CONTENT=$(git show --format="" "$MAIN_LOCAL_SHA" 2>/dev/null)
else
  DIFF_CONTENT=$(git diff "$MAIN_REMOTE_SHA".."$MAIN_LOCAL_SHA" 2>/dev/null)
fi

[ -z "$DIFF_CONTENT" ] && exit 0  # diff vide (ex. simple move de ref)

# 05/08 -- exception validee par l'operateur ("oui je valide", issue du
# rapport 4d94019c) : un push ne touchant QUE .github/** (pure config CI/
# workflows, zero effet runtime sur le VPS) ne merite pas un appel paye --
# l'infrastructure de surveillance (CodeQL/Dependabot/uptime) doit pouvoir
# etre poussee immediatement sans attendre le seuil de 2000 lignes NI couter
# une revue. Des qu'UN fichier hors .github/ est dans le diff, la revue
# complete a lieu normalement.
if [ "$MAIN_REMOTE_SHA" != "$ZERO_SHA" ]; then
  NON_GITHUB_FILES=$(git diff --name-only "$MAIN_REMOTE_SHA".."$MAIN_LOCAL_SHA" 2>/dev/null | grep -cv "^\.github/" || true)
  [ "$NON_GITHUB_FILES" = "0" ] && exit 0
fi

DIFF_LEN=${#DIFF_CONTENT}
DIFF_TRUNCATED=""
if [ "$DIFF_LEN" -gt 60000 ]; then
  DIFF_CONTENT="${DIFF_CONTENT:0:60000}"
  DIFF_TRUNCATED="\n\n[... diff tronque a 60000 caracteres sur $DIFF_LEN, analyse partielle ...]"
fi

# Tout le travail reel est detache -- le push aboutit immediatement, sans
# jamais attendre l'appel API.
(
  # Cle lue UNIQUEMENT depuis le .env du conteneur au moment du besoin --
  # jamais gardee dans le shell host, jamais affichee/loggee.
  OR_KEY=$(grep '^OPENROUTER_API_KEY=' "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2-)
  if [ -z "$OR_KEY" ]; then
    {
      echo "# Avocat du Diable -- ECHEC DE GENERATION"
      echo ""
      echo "OPENROUTER_API_KEY introuvable dans $ENV_FILE au moment du push."
      echo "Genere le $(date -u +%Y-%m-%dT%H:%M:%SZ)."
    } > "$REPORT_FILE"
    echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) -- ECHEC cle OpenRouter absente ===" >> "$REVIEW_LOG"
    exit 0
  fi

  # Carte legere de docs/aria-learning-inbox/ (noms de fichiers seulement --
  # deja descriptifs par convention de nommage de ce projet -- jamais le
  # contenu integral, pour ne pas exploser le budget tokens/cout).
  INBOX_INDEX=$(ls "$REPO_DIR"/docs/aria-learning-inbox/*.md 2>/dev/null | xargs -n1 basename 2>/dev/null)
  [ -z "$INBOX_INDEX" ] && INBOX_INDEX="(aucune fiche deposee pour l'instant)"

  USER_DIFF_CONTENT="${DIFF_CONTENT}${DIFF_TRUNCATED}"
  RAW_RESPONSE=$(devils_advocate_call "$USER_DIFF_CONTENT" "$INBOX_INDEX" "$OR_KEY" 2>/tmp/devils-advocate-http-status.$$)
  HTTP_STATUS=$(grep -oE 'HTTP_STATUS:[0-9]+' /tmp/devils-advocate-http-status.$$ | cut -d: -f2)
  rm -f /tmp/devils-advocate-http-status.$$
  unset OR_KEY

  RESPONSE_CONTENT=""
  FINISH_REASON="inconnu"
  if [ "$HTTP_STATUS" = "200" ]; then
    RESPONSE_CONTENT=$(echo "$RAW_RESPONSE" | jq -r '.choices[0].message.content // empty' 2>/dev/null)
    FINISH_REASON=$(echo "$RAW_RESPONSE" | jq -r '.choices[0].finish_reason // "inconnu"' 2>/dev/null)
  fi

  {
    echo "# Avocat du Diable -- rapport de critique post-push"
    echo ""
    echo "> ATTENTION -- REGLE DE LECTURE OBLIGATOIRE : ce rapport vient d'un"
    echo "> agent IA EXTERNE (${MODEL}, via OpenRouter). Il peut halluciner"
    echo "> des problemes inexistants ou mal comprendre le contexte du"
    echo "> projet. Verifie CHAQUE affirmation technique contre le vrai code"
    echo "> avant d'ecrire le moindre correctif -- meme discipline que pour"
    echo "> toute revue croisee Gemini/ChatGPT dans ce projet. Ne jamais"
    echo "> agir sur une affirmation non verifiee."
    echo ">"
    echo "> Commit pousse sur main : ${MAIN_LOCAL_SHA} (precedent : ${MAIN_REMOTE_SHA})"
    echo "> Genere le $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo ""
    echo "---"
    echo ""
    if [ -n "$RESPONSE_CONTENT" ]; then
      if [ "$FINISH_REASON" = "length" ]; then
        echo "**ATTENTION -- reponse TRONQUEE (finish_reason=length, max_tokens atteint) -- incomplete.**"
        echo ""
      fi
      echo "$RESPONSE_CONTENT"
    else
      echo "**[ECHEC DE GENERATION DU RAPPORT]** -- HTTP status: ${HTTP_STATUS}, finish_reason: ${FINISH_REASON}."
      echo ""
      echo "Aucune critique n'a pu etre generee pour ce push. Voir ${REVIEW_LOG} pour le detail."
    fi
  } > "$REPORT_FILE"

  echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) -- push main ${MAIN_REMOTE_SHA}..${MAIN_LOCAL_SHA} -- HTTP ${HTTP_STATUS} ===" >> "$REVIEW_LOG"
) &
disown

exit 0
