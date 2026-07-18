#!/usr/bin/env bash
# devils-advocate-review.sh -- "Avocat du Diable" : critique architecturale
# asynchrone post-push (decision operateur explicite, 18/07, conception
# croisee avec Gemini -- voir CLAUDE.md "Automatismes en place"). Appele par
# le hook .git/hooks/pre-push (non versionne -- CE script est la logique
# versionnee, le hook n'est qu'un declencheur d'une ligne).
#
# Role : un modele DIFFERENT de celui qui ecrit le code (jamais le meme qui
# se juge lui-meme -- ici DeepSeek R1 via OpenRouter, jamais Claude) relit le
# diff qui vient de partir sur main et redige une critique structuree
# (complexite inutile, limites a l'echelle, alternative radicale SI
# pertinente + plan de transition obligatoire). Ecrit un rapport, rien
# d'autre -- aucune execution, aucun acces au code, aucune commande git. La
# session suivante le lit et DOIT verifier chaque affirmation avant d'agir
# dessus (jamais gober -- meme discipline que toute revue croisee Gemini/
# ChatGPT deja pratiquee dans ce projet).
#
# Ne bloque JAMAIS le push : tout le travail reel tourne en arriere-plan,
# detache. Ne se declenche QUE sur un push touchant refs/heads/main (jamais
# sur une branche temporaire claude/*-temp -- bruit et cout inutiles).
set -uo pipefail

REPO_DIR="/opt/aria"
REPORT_FILE="/opt/aria-data/architect-report.md"
REVIEW_LOG="/opt/aria-data/architect-review.log"
ENV_FILE="$REPO_DIR/vanguard/backend/.env"
MODEL="deepseek/deepseek-r1"
ZERO_SHA="0000000000000000000000000000000000000000"

cd "$REPO_DIR" || exit 1

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

if [ "$MAIN_REMOTE_SHA" = "$ZERO_SHA" ]; then
  DIFF_CONTENT=$(git show --format="" "$MAIN_LOCAL_SHA" 2>/dev/null)
else
  DIFF_CONTENT=$(git diff "$MAIN_REMOTE_SHA".."$MAIN_LOCAL_SHA" 2>/dev/null)
fi

[ -z "$DIFF_CONTENT" ] && exit 0  # diff vide (ex. simple move de ref)

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

  SYSTEM_PROMPT=$(cat <<'PROMPT_EOF'
Tu es un Architecte Logiciel Senior et "l'Avocat du Diable" du projet ARIA
(agent IA autonome de trading/analyse crypto sur Base). Ton role n'est PAS de
valider le code qui t'est soumis ni de chercher des erreurs de syntaxe. Ton
unique objectif : trouver les limites, les angles morts architecturaux, et
proposer des ameliorations radicales (changements de paradigme) au code
fraichement pousse sur la branche main en production.

Le code fourni vient d'etre pousse de maniere autonome. Il fonctionne dans
l'etat actuel. Determine s'il va casser sous une charge/echelle 10x
superieure, ou s'il aurait fallu une approche differente depuis le depart.

REGLES D'ANALYSE :
1. Friction et complexite : ou la solution est-elle surcompliquee ? Detours
   logiques, redondances, duplication avec du code deja existant ailleurs
   dans le projet ?
2. Scalabilite et limites : projette ce code a une echelle superieure.
   Qu'est-ce qui casse en premier (memoire, latence, dependances
   circulaires, cout API) ?
3. Changement de paradigme (REGLE D'OR) : si tu proposes une refonte
   radicale, tu DOIS fournir un plan de migration progressif en etapes
   isolees, sans regression, sans interrompre le fonctionnement existant.
   Ne propose JAMAIS "efface tout et recommence" sans ce plan.

MEMOIRE PARTAGEE -- des noms de fiches de recherche deja deposees par
l'equipe te seront donnees (juste les noms, pas le contenu) : ne propose PAS
comme "nouvelle piste" un sujet qui a deja son propre nom de fichier, borne-
toi a le mentionner comme deja explore si pertinent.

FORMAT DE SORTIE EXIGE, STRICT, RIEN D'AUTRE AUTOUR :
[VULNERABILITE CACHEE] : (1-2 phrases, ce qui risque de casser a moyen terme)
[LA FAUSSE BONNE IDEE] : (un choix de conception recent qui semble marcher mais sous-optimal)
[L'ALTERNATIVE RADICALE] : (solution repensee depuis zero -- "aucune" si le code est deja solide, ne force jamais une critique artificielle)
[PLAN DE TRANSITION SECURISE] : (comment migrer en 3 etapes isolees sans casser l'existant -- omis si alternative radicale vide)

Si le diff est reellement solide sans angle mort serieux, dis-le honnetement
plutot que d'inventer une critique pour remplir le format.
PROMPT_EOF
)

  USER_CONTENT="[MEMOIRE PARTAGEE -- fiches deja deposees]
${INBOX_INDEX}

[DIFF POUSSE SUR MAIN]
${DIFF_CONTENT}${DIFF_TRUNCATED}"

  PAYLOAD=$(jq -n \
    --arg model "$MODEL" \
    --arg system "$SYSTEM_PROMPT" \
    --arg user "$USER_CONTENT" \
    '{
      model: $model,
      max_tokens: 4000,
      messages: [
        {role: "system", content: $system},
        {role: "user", content: $user}
      ]
    }')

  RESP_TMP=$(mktemp /tmp/architect-response.XXXXXX.json)
  HTTP_STATUS=$(curl -s -o "$RESP_TMP" -w "%{http_code}" \
    --max-time 120 \
    -X POST https://openrouter.ai/api/v1/chat/completions \
    -H "Authorization: Bearer $OR_KEY" \
    -H "Content-Type: application/json" \
    -d "$PAYLOAD")
  unset OR_KEY

  RESPONSE_CONTENT=""
  if [ "$HTTP_STATUS" = "200" ]; then
    RESPONSE_CONTENT=$(jq -r '.choices[0].message.content // empty' "$RESP_TMP" 2>/dev/null)
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
      echo "$RESPONSE_CONTENT"
    else
      echo "**[ECHEC DE GENERATION DU RAPPORT]** -- HTTP status: ${HTTP_STATUS}."
      echo ""
      echo "Aucune critique n'a pu etre generee pour ce push. Voir ${REVIEW_LOG} pour le detail."
    fi
  } > "$REPORT_FILE"

  echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) -- push main ${MAIN_REMOTE_SHA}..${MAIN_LOCAL_SHA} -- HTTP ${HTTP_STATUS} ===" >> "$REVIEW_LOG"
  rm -f "$RESP_TMP"
) &
disown

exit 0
