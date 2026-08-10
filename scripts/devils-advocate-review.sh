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
# 07/08 -- real gap found live (operator: "tu pousse trop souvent sous les
# 2000" -- a small isolated push, e.g. a one-file CI fix, still triggered a
# full paid call): the 2000-raw-line batching rule (CLAUDE.md) was PURE
# session discipline, never mechanized here -- nothing stopped a push from
# under the threshold from still costing a real API call. LAST_REVIEWED_MARKER
# fixes this the same way .last-deployed-ref already tracks the deploy
# threshold: the diff analyzed is no longer just "this push" but
# marker..HEAD (everything accumulated since the last push that ACTUALLY
# triggered a real call). Under threshold -> skip, marker untouched, so the
# next push naturally absorbs the missed diff instead of a second push
# silently starting a fresh, smaller-than-2000 window. Reaching the
# threshold -> normal call, marker advances to HEAD.
set -uo pipefail

REPO_DIR="/opt/aria"
PENDING_DIR="/opt/aria-data/architect-reports/pending"
REVIEW_LOG="/opt/aria-data/architect-review.log"
ENV_FILE="$REPO_DIR/vanguard/backend/.env"
ZERO_SHA="0000000000000000000000000000000000000000"
LAST_REVIEWED_MARKER="/opt/aria-data/architect-reports/last-reviewed-sha"
BATCH_THRESHOLD_LINES=8000

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
  LAST_PUSH_DIFF=$(git show --format="" "$MAIN_LOCAL_SHA" 2>/dev/null)
else
  LAST_PUSH_DIFF=$(git diff "$MAIN_REMOTE_SHA".."$MAIN_LOCAL_SHA" 2>/dev/null)
fi

[ -z "$LAST_PUSH_DIFF" ] && exit 0  # diff vide (ex. simple move de ref)

# 05/08 -- exception validee par l'operateur ("oui je valide", issue du
# rapport 4d94019c) : un push ne touchant QUE .github/** (pure config CI/
# workflows, zero effet runtime sur le VPS) ne merite pas un appel paye --
# l'infrastructure de surveillance (CodeQL/Dependabot/uptime) doit pouvoir
# etre poussee immediatement sans attendre le seuil de 2000 lignes NI couter
# une revue. Des qu'UN fichier hors .github/ est dans le diff, la revue
# complete a lieu normalement. Base sur CE push seul (jamais le cumul) --
# une infra de surveillance doit toujours pouvoir sortir immediatement.
if [ "$MAIN_REMOTE_SHA" != "$ZERO_SHA" ]; then
  NON_GITHUB_FILES=$(git diff --name-only "$MAIN_REMOTE_SHA".."$MAIN_LOCAL_SHA" 2>/dev/null | grep -cv "^\.github/" || true)
  [ "$NON_GITHUB_FILES" = "0" ] && exit 0
fi

# 07/08 -- mecanise le seuil de batching (voir commentaire de tete sur
# LAST_REVIEWED_MARKER) : le diff REELLEMENT analyse est marker..HEAD, pas
# juste ce dernier push, pour ne jamais perdre la couverture d'un push
# precedent qui n'avait pas atteint le seuil.
BASE_SHA="$MAIN_REMOTE_SHA"
if [ -f "$LAST_REVIEWED_MARKER" ]; then
  MARKER_SHA=$(cat "$LAST_REVIEWED_MARKER" 2>/dev/null | tr -d '[:space:]')
  if [ -n "$MARKER_SHA" ] && git cat-file -e "${MARKER_SHA}^{commit}" 2>/dev/null; then
    BASE_SHA="$MARKER_SHA"
  fi
fi

if [ "$BASE_SHA" = "$ZERO_SHA" ] || [ -z "$BASE_SHA" ]; then
  DIFF_CONTENT=$(git show --format="" "$MAIN_LOCAL_SHA" 2>/dev/null)
else
  DIFF_CONTENT=$(git diff "$BASE_SHA".."$MAIN_LOCAL_SHA" 2>/dev/null)
fi
[ -z "$DIFF_CONTENT" ] && DIFF_CONTENT="$LAST_PUSH_DIFF"

CUMULATIVE_LINES=0
if [ "$BASE_SHA" != "$ZERO_SHA" ] && [ -n "$BASE_SHA" ]; then
  CUMULATIVE_LINES=$(git diff --shortstat "$BASE_SHA".."$MAIN_LOCAL_SHA" 2>/dev/null \
    | grep -oE '[0-9]+ (insertion|deletion)' | grep -oE '[0-9]+' | awk '{s+=$1} END {print s+0}')
fi

if [ "$CUMULATIVE_LINES" -lt "$BATCH_THRESHOLD_LINES" ]; then
  echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) -- push main ${MAIN_REMOTE_SHA}..${MAIN_LOCAL_SHA} -- SKIP sous le seuil (${CUMULATIVE_LINES}/${BATCH_THRESHOLD_LINES} lignes cumulees depuis ${BASE_SHA:0:12}) ===" >> "$REVIEW_LOG"
  exit 0
fi

# Tout le travail reel est detache -- le push aboutit immediatement, sans
# jamais attendre l'appel API.
(
  # Cle lue UNIQUEMENT depuis le .env du conteneur au moment du besoin --
  # jamais gardee dans le shell host, jamais affichee/loggee.
  # 10/08 -- API Anthropic directe (ANTHROPIC_API_KEY), remplace OPENROUTER_API_KEY
  # apres un vrai incident credits OpenRouter epuises (HTTP 402).
  API_KEY=$(grep '^ANTHROPIC_API_KEY=' "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2-)
  if [ -z "$API_KEY" ]; then
    {
      echo "# Avocat du Diable -- ECHEC DE GENERATION"
      echo ""
      echo "ANTHROPIC_API_KEY introuvable dans $ENV_FILE au moment du push."
      echo "Genere le $(date -u +%Y-%m-%dT%H:%M:%SZ)."
    } > "$REPORT_FILE"
    echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) -- ECHEC cle Anthropic absente ===" >> "$REVIEW_LOG"
    exit 0
  fi

  # Carte legere de docs/aria-learning-inbox/ (noms de fichiers seulement --
  # deja descriptifs par convention de nommage de ce projet -- jamais le
  # contenu integral, pour ne pas exploser le budget tokens/cout).
  INBOX_INDEX=$(ls "$REPO_DIR"/docs/aria-learning-inbox/*.md 2>/dev/null | xargs -n1 basename 2>/dev/null)
  [ -z "$INBOX_INDEX" ] && INBOX_INDEX="(aucune fiche deposee pour l'instant)"

  # 10/08 -- condensation Haiku 4.5 (jamais une troncature brute) si le diff
  # depasse DEVILS_ADVOCATE_CONDENSE_THRESHOLD_CHARS -- voir devils-advocate-lib.sh.
  USER_DIFF_CONTENT=$(devils_advocate_diff_for_review "$DIFF_CONTENT" "$API_KEY" "${MAIN_LOCAL_SHA:0:12}")
  RAW_RESPONSE=$(devils_advocate_call "$USER_DIFF_CONTENT" "$INBOX_INDEX" "$API_KEY" 2>/tmp/devils-advocate-http-status.$$)
  HTTP_STATUS=$(grep -oE 'HTTP_STATUS:[0-9]+' /tmp/devils-advocate-http-status.$$ | cut -d: -f2)
  rm -f /tmp/devils-advocate-http-status.$$
  unset API_KEY

  RESPONSE_CONTENT=""
  FINISH_REASON="inconnu"
  COST_LINE="0 0 0.000000"
  if [ "$HTTP_STATUS" = "200" ]; then
    RESPONSE_CONTENT=$(echo "$RAW_RESPONSE" | jq -r '[.content[]? | select(.type == "text") | .text] | join("\n\n")' 2>/dev/null)
    STOP_REASON=$(echo "$RAW_RESPONSE" | jq -r '.stop_reason // "inconnu"' 2>/dev/null)
    FINISH_REASON="$STOP_REASON"
    COST_LINE=$(devils_advocate_cost "$RAW_RESPONSE")
    read -r IN_TOKENS OUT_TOKENS COST_USD <<< "$COST_LINE"
    devils_advocate_log_cost "${MAIN_LOCAL_SHA:0:12}" "$IN_TOKENS" "$OUT_TOKENS" "$COST_USD"
  fi

  {
    echo "# Avocat du Diable -- rapport de critique post-push"
    echo ""
    echo "> ATTENTION -- REGLE DE LECTURE OBLIGATOIRE : ce rapport vient d'un"
    echo "> agent IA EXTERNE (${MODEL}, via l'API Anthropic directe). Il peut halluciner"
    echo "> des problemes inexistants ou mal comprendre le contexte du"
    echo "> projet. Verifie CHAQUE affirmation technique contre le vrai code"
    echo "> avant d'ecrire le moindre correctif -- meme discipline que pour"
    echo "> toute revue croisee Gemini/ChatGPT dans ce projet. Ne jamais"
    echo "> agir sur une affirmation non verifiee."
    echo ">"
    echo "> Commit pousse sur main : ${MAIN_LOCAL_SHA} (precedent : ${MAIN_REMOTE_SHA})"
    if [ "$BASE_SHA" != "$MAIN_REMOTE_SHA" ]; then
      echo "> Diff REELLEMENT analyse (cumul, ${CUMULATIVE_LINES} lignes) : depuis ${BASE_SHA:0:12} -- au moins un push precedent etait sous le seuil de ${BATCH_THRESHOLD_LINES} lignes et a ete reporte ici."
    fi
    echo "> Genere le $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    if [ "$HTTP_STATUS" = "200" ]; then
      echo "> Cout reel de cet appel : \$${COST_USD} (${IN_TOKENS} tokens input, ${OUT_TOKENS} tokens output, tarifs Fable 5 verifies 10/08). Journal cumulatif : ${DEVILS_ADVOCATE_COST_LOG}."
    fi
    # 10/08 (soir) -- couverture reelle du diff ecrite ICI de facon MECANIQUE
    # (jamais dependante de si Fable 5 la mentionne dans sa prose) -- corrige
    # le gap trouve sur le rapport 8e01e6fb ou le vrai taux de couverture
    # (~6%) n'etait visible que si le modele choisissait de le citer.
    if [ -n "$DA_COVERAGE_NOTE" ]; then
      echo "> Couverture du diff (condensation par tranches) : ${DA_COVERAGE_NOTE}."
    fi
    echo ""
    echo "---"
    echo ""
    if [ -n "$RESPONSE_CONTENT" ]; then
      if [ "$FINISH_REASON" = "max_tokens" ]; then
        echo "**ATTENTION -- reponse TRONQUEE (stop_reason=max_tokens) -- incomplete.**"
        echo ""
      fi
      echo "$RESPONSE_CONTENT"
    else
      echo "**[ECHEC DE GENERATION DU RAPPORT]** -- HTTP status: ${HTTP_STATUS}, finish_reason: ${FINISH_REASON}."
      echo ""
      echo "Aucune critique n'a pu etre generee pour ce push. Voir ${REVIEW_LOG} pour le detail."
    fi
  } > "$REPORT_FILE"

  # Avance le marker seulement une fois le cumul REELLEMENT couvert (seuil
  # atteint, appel effectue) -- jamais sur un skip (voir le check plus haut),
  # sinon la couverture du diff manque silencieusement.
  echo "$MAIN_LOCAL_SHA" > "$LAST_REVIEWED_MARKER"

  echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) -- push main ${MAIN_REMOTE_SHA}..${MAIN_LOCAL_SHA} -- HTTP ${HTTP_STATUS} (cumul ${CUMULATIVE_LINES} lignes depuis ${BASE_SHA:0:12}) -- cout \$${COST_USD} (${IN_TOKENS} in / ${OUT_TOKENS} out) ===" >> "$REVIEW_LOG"
) &
disown

exit 0
