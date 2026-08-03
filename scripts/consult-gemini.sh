#!/usr/bin/env bash
# consult-gemini.sh -- manual, on-demand second opinion via Gemini 3.1 Pro
# (OpenRouter). Item #65 (08/03), operator request: distinct from
# devils-advocate-review.sh's automatic post-push hook (which runs
# unattended on every push to main with a fixed "Devil's Advocate" role) --
# this one is invoked ONLY when the operator explicitly asks for a Gemini
# second opinion on a specific plan, mid-conversation, synchronous (the
# caller waits for the answer, never detached). Reuses the exact same
# verified pattern (model, auth, headers) as the existing post-push
# mechanism -- same doctrine on why: a model and lab different from the one
# writing the code, never Claude judging itself.
#
# Usage: cat plan.txt | scripts/consult-gemini.sh
#        echo "some plan text" | scripts/consult-gemini.sh
#
# Reads the prompt from stdin, prints Gemini's raw response to stdout.
# Same "verify before acting" doctrine as the post-push report applies to
# whatever comes back -- an external model's opinion is a second opinion to
# check against the real code, never gospel.
#
# 08/03 -- amended after a 2-agent workflow audit of the first version
# (which itself followed a REAL false alarm on the first live use: Gemini
# asserted with full confidence that a trigger function was "missing",
# when in fact one existing function already handled both cases -- it
# simply hadn't seen the full file, only a diff). Fixes applied:
# 1. max_tokens raised 8000 -> 20000 (verified real ceiling: 65536, but a
#    lower practical cap keeps cost bounded) + finish_reason now read and
#    surfaced -- a silent truncation would otherwise contradict the
#    operator's explicit "exhaustive over concise" instruction unnoticed.
# 2. "[VERIFIE, ligne X]" replaced by a verbatim quote requirement -- a line
#    number is only ever knowable if the input happens to be a diff with
#    hunk headers; a verbatim substring is checkable on ANY plain text.
# 3. Two categories added (consistency with already-settled operator
#    decisions; testability/regression risk) per the workflow's finding
#    that the original 6 weren't exhaustive for this project's own context.
# 4. Mechanical citation guard (NEW, the workflow's own top recommendation):
#    every "[VERIFIE -- citation exacte: "...")]" is grep -F'd against the
#    actual prompt sent -- a citation that doesn't literally appear gets a
#    loud warning prefixed to the whole output, rather than trusting the
#    model's self-labeling on faith. A prompt-level instruction alone
#    cannot GUARANTEE honesty, only ask for it -- this is the mechanical
#    backstop the workflow flagged as the real gap.
set -uo pipefail

REPO_DIR="/opt/aria"
ENV_FILE="$REPO_DIR/vanguard/backend/.env"
MODEL="google/gemini-3.1-pro-preview"

PROMPT_CONTENT="$(cat)"

if [ -z "$PROMPT_CONTENT" ]; then
  echo "Usage: cat plan.txt | scripts/consult-gemini.sh (or pipe text via stdin)" >&2
  exit 1
fi

# Key read ONLY from the container's .env at the moment of need -- never
# kept in the host shell, never displayed/logged (same doctrine as
# devils-advocate-review.sh).
OR_KEY=$(grep '^OPENROUTER_API_KEY=' "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2-)
if [ -z "$OR_KEY" ]; then
  echo "ERREUR: OPENROUTER_API_KEY introuvable dans $ENV_FILE" >&2
  exit 1
fi

SYSTEM_PROMPT=$(cat <<'PROMPT_EOF'
Tu es un second avis technique independant (Gemini), consulte manuellement par un operateur humain sur un projet de trading crypto autonome (ARIA, agent IA sur Base). Ton role : donner un avis honnete et direct sur le plan/le code/la question soumise -- confirme ce qui tient a la lecture des faits fournis, challenge ce qui ne tient pas, propose des corrections concretes si necessaire. Ne sois jamais complaisant et ne valide jamais par defaut. Si le plan est deja solide, dis-le clairement plutot que d'inventer une critique pour remplir une reponse.

REGLE ABSOLUE -- CONSTAT VERIFIE vs HYPOTHESE (bidirectionnelle, jamais a sens unique) :
- Tu ne recois PEUT-ETRE qu'un extrait/diff, jamais forcement le code/dossier complet. N'invente jamais l'existence, l'absence, ou le comportement d'un element (une fonction, un fichier, un appelant) que tu n'as pas vu litteralement dans le texte fourni.
- Pour CHAQUE affirmation factuelle, etiquette-la explicitement :
  * [VERIFIE -- citation exacte : "..."] -- tu recopies MOT POUR MOT un fragment du texte fourni qui prouve ton affirmation (jamais paraphrase, jamais resume -- une citation verbatim, verifiable mecaniquement par un simple grep). Si le texte fourni est un diff avec des en-tetes de hunk (@@ -X,Y +A,B @@), tu peux AUSSI mentionner le numero de ligne approximatif en complement -- mais la citation verbatim reste obligatoire, jamais un numero de ligne seul.
  * [HYPOTHESE -- non visible dans le texte fourni] -- tu formules une inquietude plausible mais NON confirmee (ex: "il pourrait exister un autre point d'entree que je ne vois pas dans cet extrait -- a verifier directement dans le fichier complet").
  Ne jamais presenter une hypothese avec la meme assurance qu'un fait verifie -- c'est la difference entre une vraie faille et une fausse alerte.
- Cette regle s'applique aussi en sens inverse : ne conclus pas non plus qu'un risque est absent juste parce que tu ne le vois pas dans l'extrait fourni -- dis "non verifiable avec le contexte fourni" plutot que "aucun risque".

EXHAUSTIVITE > CONCISION -- consigne explicite de l'operateur : une reponse trop longue qui couvre tout est TOUJOURS preferee a une reponse courte qui rate quelque chose. Ne t'arrete jamais apres avoir trouve 1-2 points -- balaie systematiquement TOUTES les categories suivantes avant de conclure, meme pour dire "rien trouve ici" :
1. Logique/coherence : le raisonnement tient-il, y a-t-il une contradiction interne ?
2. Edge cases / donnees manquantes : que se passe-t-il sur une entree vide, nulle, extreme, concurrente ?
3. Securite : donnee sensible exposee, validation manquante, cote d'attaque ?
4. Performance/cout : appel reseau/DB evitable, boucle couteuse, cout recurrent sous-estime ?
5. Angles morts d'hypothese (voir regle ci-dessus) : que ne peux-tu PAS verifier avec ce qui t'a ete donne, et qui merite d'etre revérifie manuellement ?
6. Alternative/amelioration : y a-t-il une approche structurellement meilleure, meme si l'existant fonctionne ?
7. Coherence avec les decisions deja actees : le texte fourni mentionne-t-il ou contredit-il une regle/decision deja tranchee (si le contexte te la donne) -- signale-le sans jamais rouvrir un debat deja clos.
8. Testabilite / risque de regression : le changement propose est-il verifiable par un test, risque-t-il de casser un comportement existant non mentionne ?
Pour chaque categorie sans probleme trouve, dis-le explicitement plutot que de l'omettre silencieusement (l'absence de mention ne doit jamais etre confondue avec "categorie verifiee, rien trouve").

Termine toujours par un VERDICT FINAL tranche (deployer tel quel / amender d'abord / rejeter), avec la liste precise de ce qui bloque s'il y en a.
PROMPT_EOF
)

PAYLOAD=$(jq -n \
  --arg model "$MODEL" \
  --arg system "$SYSTEM_PROMPT" \
  --arg user "$PROMPT_CONTENT" \
  --arg session_id "consult-gemini-manual-$$" \
  '{
    model: $model,
    max_tokens: 20000,
    messages: [
      {role: "system", content: $system},
      {role: "user", content: $user}
    ],
    session_id: $session_id
  }')

RESP_TMP=$(mktemp /tmp/consult-gemini-response.XXXXXX.json)
HTTP_STATUS=$(curl -s -o "$RESP_TMP" -w "%{http_code}" \
  --max-time 120 \
  -X POST https://openrouter.ai/api/v1/chat/completions \
  -H "Authorization: Bearer $OR_KEY" \
  -H "Content-Type: application/json" \
  -H "HTTP-Referer: https://github.com/GoldenFarFR/aria-vanguard" \
  -H "X-OpenRouter-Title: ARIA Manual Gemini Consult" \
  -H "X-Title: ARIA Manual Gemini Consult" \
  -d "$PAYLOAD")
unset OR_KEY

if [ "$HTTP_STATUS" != "200" ]; then
  echo "ERREUR HTTP ${HTTP_STATUS} -- reponse brute :" >&2
  cat "$RESP_TMP" >&2
  rm -f "$RESP_TMP"
  exit 1
fi

FINISH_REASON=$(jq -r '.choices[0].finish_reason // "inconnu"' "$RESP_TMP")
RESPONSE_CONTENT=$(jq -r '.choices[0].message.content // "reponse vide"' "$RESP_TMP")
rm -f "$RESP_TMP"

if [ "$FINISH_REASON" = "length" ]; then
  echo "ATTENTION -- reponse TRONQUEE (finish_reason=length, max_tokens atteint) -- la reponse ci-dessous est incomplete, relance avec un contexte plus court ou augmente max_tokens dans ce script." >&2
fi

# Garde-fou mecanique (08/03, recommandation du workflow d'audit) : une
# consigne de prompt ne peut que DEMANDER l'honnetete, jamais la garantir --
# chaque citation "[VERIFIE -- citation exacte : "..."]" est verifiee ici
# par un grep -F litteral contre le texte REELLEMENT envoye. Une citation
# qui ne matche pas (hallucinee) declenche un avertissement visible plutot
# que d'etre prise sur parole.
UNVERIFIED_CITATIONS=0
while IFS= read -r citation; do
  if [ -n "$citation" ] && ! grep -qF -- "$citation" <<<"$PROMPT_CONTENT"; then
    UNVERIFIED_CITATIONS=$((UNVERIFIED_CITATIONS + 1))
  fi
done < <(grep -oP '(?<=\[VERIFIE -- citation exacte : ")[^"]+' <<<"$RESPONSE_CONTENT" || true)

if [ "$UNVERIFIED_CITATIONS" -gt 0 ]; then
  echo "ATTENTION -- ${UNVERIFIED_CITATIONS} citation(s) marquee(s) [VERIFIE] par Gemini n'ont PAS ete retrouvees telles quelles dans le texte envoye -- possible hallucination de citation, relis ces passages avec prudence avant d'agir dessus." >&2
fi

echo "$RESPONSE_CONTENT"
