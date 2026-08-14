#!/usr/bin/env bash
# devils-advocate-backlog-review.sh -- review d'une LISTE D'IDEES DE BACKLOG (pas un
# diff de code), demande operateur explicite (14/08) : faire juger par l'Avocat du
# Diable (Fable 5) les ~70 idees issues d'un workflow multi-agents, avec mandat de
# proposer de nouvelles pistes et/ou reviser les existantes. Reutilise devils_advocate_call
# (meme modele/cout-tracking que review.sh/precommit.sh) mais avec un system prompt
# DEDIE -- le prompt "diff de code" partage (VULNERABILITE CACHEE / FAUSSE BONNE IDEE /
# ALTERNATIVE RADICALE) n'a pas de sens sur une liste de propositions non-code.
#
# Demande operateur explicite (14/08) : PAS de condensation Haiku ici, meme si le
# contenu depasse DEVILS_ADVOCATE_CONDENSE_THRESHOLD_CHARS -- Fable 5 recoit le texte
# complet, brut, jamais un resume intermediaire qui pourrait aplatir des nuances sur
# des propositions (contrairement a un diff de code ou la condensation structuree
# perd peu). Reponse imprimee sur stdout, lue et relayee integralement a l'operateur
# dans la meme conversation -- meme pattern que devils-advocate-precommit.sh.
set -uo pipefail

REPO_DIR="/opt/aria"
ENV_FILE="$REPO_DIR/vanguard/backend/.env"

if [ $# -lt 1 ]; then
  echo "Usage: $0 <fichier-contenant-les-idees.md>" >&2
  exit 1
fi
IDEAS_FILE="$1"
if [ ! -f "$IDEAS_FILE" ]; then
  echo "Fichier introuvable: $IDEAS_FILE" >&2
  exit 1
fi

cd "$REPO_DIR" || exit 1
# shellcheck source=./devils-advocate-lib.sh
source "$REPO_DIR/scripts/devils-advocate-lib.sh"

API_KEY=$(grep '^ANTHROPIC_API_KEY=' "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2-)
if [ -z "$API_KEY" ]; then
  echo "ANTHROPIC_API_KEY introuvable dans $ENV_FILE." >&2
  exit 1
fi

IDEAS_CONTENT=$(cat "$IDEAS_FILE")
INBOX_INDEX=$(ls "$REPO_DIR"/docs/aria-learning-inbox/*.md 2>/dev/null | xargs -n1 basename 2>/dev/null)
[ -z "$INBOX_INDEX" ] && INBOX_INDEX="(aucune fiche deposee pour l'instant)"

BACKLOG_SYSTEM_PROMPT=$(cat <<'PROMPT_EOF'
Tu es un Architecte Logiciel Senior et "l'Avocat du Diable" du projet ARIA
(agent IA autonome de trading/analyse crypto sur Base). On te soumet ici une
LISTE DE PROPOSITIONS DE BACKLOG (pas un diff de code) -- issue d'un
brainstorming multi-agents qui a lu du code reel dans differents domaines du
repo. Ton role n'est PAS de valider poliment la liste. Ton objectif :

1. Juger la SOLIDITE de chaque idee (ou groupe d'idees coherent) : laquelle
   est un vrai probleme verifiable dans le code, laquelle est superficielle,
   speculative, ou deja couverte ailleurs par un mecanisme existant que les
   agents auraient manque ?
2. Reperer les REDONDANCES ou CONTRADICTIONS entre idees (deux propositions
   qui se marchent dessus, ou une idee qui contredit une decision operateur
   deja actee).
3. PROPOSER DE NOUVELLES IDEES si un angle mort te semble evident au vu du
   contexte donne (jamais invente au-dela de ce qui est plausible depuis les
   informations fournies -- si tu n'as pas assez de contexte pour une
   nouvelle idee precise, dis-le plutot que d'en inventer une vague).
4. PROPOSER DE MODIFIER/FUSIONNER des idees existantes quand une meilleure
   formulation ou un meilleur perimetre te semble evident.
5. Prioriser : quelles idees meritent d'etre traitees EN PREMIER (risque
   reel/incident deja survenu) vs lesquelles peuvent attendre.

Tu disposes d'un vrai budget de raisonnement pour cette tache (effort eleve,
demande operateur explicite) -- utilise-le. Ne te contente pas d'un jugement
de surface en une phrase par idee : pour chaque idee qui merite un vrai avis
(pas les plus evidentes/mineures), va au fond -- quelle est la consequence
concrete si ce n'est jamais corrige, quel est le vrai cout d'implementation
relatif aux autres, y a-t-il une interaction avec une autre idee de la liste
que les agents individuels n'ont pas pu voir (chacun n'a lu qu'un seul
domaine, toi tu as la liste complete). C'est exactement ce que 12 agents
isoles ne pouvaient pas faire chacun de son cote.

Ne force jamais une critique artificielle pour remplir un format -- si une
idee est deja solide et bien scopee, dis-le honnetement.

MEMOIRE PARTAGEE -- des noms de fiches de recherche deja deposees par
l'equipe te seront donnees (juste les noms, pas le contenu) : ne propose PAS
comme "nouvelle piste" un sujet qui a deja son propre nom de fichier.

FORMAT DE SORTIE : libre mais structure (ex. par domaine ou par niveau de
priorite), en francais, avec pour chaque jugement fort une reference precise
au numero de l'idee concernee (#N) pour que la reponse soit exploitable
directement sans devoir deviner de quoi tu parles.
PROMPT_EOF
)

# Demande operateur (14/08) : exploiter la vraie puissance de Fable 5 sur cette tache
# d'approfondissement plutot que le calibrage "medium" standard des reviews de diff --
# override local, jamais la constante partagee (les 2 autres appelants ne changent pas).
# max_tokens reste a la valeur deja verifiee en conditions reelles (96000, cf. lib) --
# jamais augmentee a l'aveugle sans reverifier la vraie limite API du modele.
DEVILS_ADVOCATE_THINKING_EFFORT="high"

echo "==> Appel Fable 5 (${#IDEAS_CONTENT} caracteres, PAS de condensation -- demande operateur explicite, effort=high)" >&2

RAW_RESPONSE=$(devils_advocate_call "$IDEAS_CONTENT" "$INBOX_INDEX" "$API_KEY" "$BACKLOG_SYSTEM_PROMPT" 2>/tmp/devils-advocate-backlog-http-status.txt)
HTTP_STATUS=$(grep -o 'HTTP_STATUS:[0-9]*' /tmp/devils-advocate-backlog-http-status.txt | cut -d: -f2)
rm -f /tmp/devils-advocate-backlog-http-status.txt

if [ "$HTTP_STATUS" != "200" ]; then
  echo "Echec API (HTTP $HTTP_STATUS) :" >&2
  echo "$RAW_RESPONSE" >&2
  exit 1
fi

# 14/08 -- sauvegarde la reponse brute AVANT tout parsing, pour ne jamais reperdre un
# appel deja paye a cause d'un bug de parsing en aval (deja vecu : premier essai de ce
# script utilisait `.content[0].text`, mais content[0] est le bloc "thinking" quand le
# raisonnement est actif -- .text y est absent, jq -r renvoie silencieusement "null".
# Le vrai texte est un bloc separe de type "text" dans le tableau, filtre par TYPE
# jamais par INDEX -- meme pattern deja etabli dans devils-advocate-review.sh/
# devils-advocate-precommit.sh/devils-advocate-lib.sh, jamais reutilise ici avant ce fix).
RAW_RESPONSE_FILE=$(mktemp /tmp/devils-advocate-backlog-raw.XXXXXX.json)
echo "$RAW_RESPONSE" > "$RAW_RESPONSE_FILE"
echo "==> Reponse brute sauvegardee : $RAW_RESPONSE_FILE (garde-la jusqu'a confirmation d'un texte exploitable)" >&2

COST_LINE=$(devils_advocate_cost "$RAW_RESPONSE")
IN_TOK=$(echo "$COST_LINE" | awk '{print $1}')
OUT_TOK=$(echo "$COST_LINE" | awk '{print $2}')
COST_USD=$(echo "$COST_LINE" | awk '{print $3}')
devils_advocate_log_cost "backlog-review" "$IN_TOK" "$OUT_TOK" "$COST_USD" 2>/dev/null || true

echo "==> Cout reel : \$${COST_USD} (${IN_TOK} tok input / ${OUT_TOK} tok output)" >&2
FINAL_TEXT=$(echo "$RAW_RESPONSE" | jq -r '[.content[]? | select(.type == "text") | .text] | join("\n\n")')
if [ -z "$FINAL_TEXT" ] || [ "$FINAL_TEXT" = "null" ]; then
  echo "ECHEC PARSING -- reponse brute preservee dans $RAW_RESPONSE_FILE, inspecte-la avant de relancer un appel." >&2
  exit 1
fi
echo "$FINAL_TEXT"
rm -f "$RAW_RESPONSE_FILE"
