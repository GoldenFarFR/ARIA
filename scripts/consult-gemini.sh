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

SYSTEM_PROMPT='Tu es un second avis technique independant (Gemini), consulte manuellement par un operateur humain sur un projet de trading crypto autonome (ARIA, agent IA sur Base). Ton role : donner un avis honnete et direct sur le plan/la question soumise -- confirme ce qui tient a la lecture des faits fournis, challenge ce qui ne tient pas, propose des corrections concretes si necessaire. Ne sois jamais complaisant et ne valide jamais par defaut. Si le plan est deja solide, dis-le clairement plutot que d'inventer une critique pour remplir une reponse.'

PAYLOAD=$(jq -n \
  --arg model "$MODEL" \
  --arg system "$SYSTEM_PROMPT" \
  --arg user "$PROMPT_CONTENT" \
  --arg session_id "consult-gemini-manual-$$" \
  '{
    model: $model,
    max_tokens: 4000,
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

jq -r '.choices[0].message.content // "reponse vide"' "$RESP_TMP"
rm -f "$RESP_TMP"
