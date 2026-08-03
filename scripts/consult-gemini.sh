#!/usr/bin/env bash
# consult-gemini.sh -- manual, on-demand second opinion (model behind MODEL
# below, originally Gemini 3.1 Pro, switched to Claude Fable 5 on 08/03 --
# filename kept as-is, it's the operator's established name for this tool).
# Item #65 (08/03), operator request: distinct from
# devils-advocate-review.sh's automatic post-push hook (which runs
# unattended on every push to main with a fixed "Devil's Advocate" role) --
# this one is invoked ONLY when the operator explicitly asks for a second
# opinion on a specific plan, mid-conversation, synchronous (the
# caller waits for the answer, never detached). Reuses the exact same
# verified pattern (auth, headers, OpenRouter) as the existing post-push
# mechanism -- same doctrine on why: a model and lab different from the one
# writing the code, never Claude judging itself.
#
# Usage: cat plan.txt | scripts/consult-gemini.sh
#        echo "some plan text" | scripts/consult-gemini.sh
#
# Reads the prompt from stdin, prints the model's raw response to stdout.
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
# 08/03 -- switched to Claude Fable 5 (operator decision) after a 2-test
# blind comparison (docs/HANDOFF_LLM.md) where it won both tests -- only
# model to link the v3 execution bug to a wider pattern, only one to catch
# the shared stop-geometry inconsistency, only one to land on the correct
# figure (2 consecutive losses, not 3) on an already-solved problem. Cost
# ~$0.28/call (~35x GLM) -- accepted explicitly for RARE use only (hard
# unblocks), never a Gemini replacement for routine second opinions.
# KNOWN RISK, not fully mitigated: a SEPARATE 9-model comparison (same
# HANDOFF file) found this same model returns EMPTY content on at least one
# long/complex prompt (HTTP 200, still billed $0.88) -- the script's own
# "reponse vide"/finish_reason guard below already surfaces this loudly
# rather than silently, but a wasted paid call on a hard, long prompt
# (exactly this script's use case) remains a real possibility to watch for.
set -uo pipefail

REPO_DIR="/opt/aria"
ENV_FILE="$REPO_DIR/vanguard/backend/.env"
MODEL="anthropic/claude-fable-5"

RAW_PROMPT="$(cat)"

if [ -z "$RAW_PROMPT" ]; then
  echo "Usage: cat plan.txt | scripts/consult-gemini.sh (or pipe text via stdin)" >&2
  exit 1
fi

# 08/03 -- operator feedback ("pourquoi tu met plein de symbole, c'est pas
# censé etre un prompt simple"): a raw `git diff`/`git show` carries format
# artifacts (leading +/- per line, @@ hunk headers, index/---/+++ metadata
# lines) that are NOT real file content -- they also directly caused false
# positives in the citation guard below (a comment wrapped across two diff
# lines keeps a stray "+" in the middle once flattened, breaking an
# otherwise-correct verbatim match). Stripped HERE, once, at the source --
# more robust than trying to normalize it away after the fact. Only strips a
# leading "+"/"-" in COLUMN 1 (a real diff's own convention), never mid-line;
# harmless on plain non-diff text, which essentially never starts lines with
# these markers. Diff metadata lines (diff --git/index/---/+++/@@) are
# dropped entirely -- pure noise, no information a citation would ever need.
PROMPT_CONTENT=$(
  awk '
    /^diff --git / { next }
    /^index [0-9a-f]+\.\.[0-9a-f]+/ { next }
    /^--- / { next }
    /^\+\+\+ / { next }
    /^@@ / { next }
    /^[+-]/ { print substr($0, 2); next }
    { print }
  ' <<<"$RAW_PROMPT"
)

# Key read ONLY from the container's .env at the moment of need -- never
# kept in the host shell, never displayed/logged (same doctrine as
# devils-advocate-review.sh).
OR_KEY=$(grep '^OPENROUTER_API_KEY=' "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2-)
if [ -z "$OR_KEY" ]; then
  echo "ERREUR: OPENROUTER_API_KEY introuvable dans $ENV_FILE" >&2
  exit 1
fi

SYSTEM_PROMPT=$(cat <<'PROMPT_EOF'
Tu es un second avis technique independant, consulte manuellement par un operateur humain sur un projet de trading crypto autonome (ARIA, agent IA sur Base). Ton role : donner un avis honnete et direct sur le plan/le code/la question soumise -- confirme ce qui tient a la lecture des faits fournis, challenge ce qui ne tient pas, propose des corrections concretes si necessaire. Ne sois jamais complaisant et ne valide jamais par defaut. Si le plan est deja solide, dis-le clairement plutot que d'inventer une critique pour remplir une reponse.

REGLE ABSOLUE -- CONSTAT VERIFIE vs HYPOTHESE (bidirectionnelle, jamais a sens unique) :
- Tu ne recois PEUT-ETRE qu'un extrait/diff, jamais forcement le code/dossier complet. N'invente jamais l'existence, l'absence, ou le comportement d'un element (une fonction, un fichier, un appelant) que tu n'as pas vu litteralement dans le texte fourni.
- Pour CHAQUE affirmation factuelle, etiquette-la explicitement :
  * [VERIFIE -- citation exacte : "..."] -- tu recopies MOT POUR MOT un fragment du texte fourni qui prouve ton affirmation (jamais paraphrase, jamais resume -- une citation verbatim, verifiable mecaniquement par un simple grep). Si le texte fourni est un diff avec des en-tetes de hunk (@@ -X,Y +A,B @@), tu peux AUSSI mentionner le numero de ligne approximatif en complement -- mais la citation verbatim reste obligatoire, jamais un numero de ligne seul. INTERDICTION ABSOLUE d'utiliser "..." ou "[...]" A L'INTERIEUR d'une citation [VERIFIE] pour en elider une partie (ex. "try: [...] except:") -- une citation tronquee par une ellipse n'est PAS verifiable mecaniquement (le texte source ne contient jamais litteralement "..."), donc mecaniquement indiscernable d'une hallucination. Si le fragment exact est long, cite-le en entier, ou decoupe-le en plusieurs citations [VERIFIE] distinctes et courtes -- jamais une ellipse.
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
# par un match litteral contre le texte REELLEMENT envoye. Une citation qui
# ne matche pas (hallucinee) declenche un avertissement visible plutot que
# d'etre prise sur parole.
#
# Les deux cotes sont NORMALISES (tout espace/tab/retour a la ligne reduit a
# un seul espace) avant comparaison -- un diff de code wrappe souvent une
# phrase de commentaire sur plusieurs lignes ; Gemini la recolle legitimement
# en une seule ligne dans sa citation (fidele, pas une hallucination), mais
# un grep -F strict la ratait a tort (faux positif reel, observe en
# conditions reelles le 08/03 -- 4 citations signalees a tort avant ce fix).
# Strips a leading git-diff marker (+/-/space, one column) from EVERY line
# BEFORE flattening -- a diff's own "+"/"-" prefix is a format artifact, not
# real file content, and Gemini correctly omits it when reconstructing a
# comment that a diff wrapped across two lines. Without this, the prefix
# survives flattening (e.g. "...window:  +        return None") and breaks
# an otherwise-correct citation match (observed for real on 08/03: still 6
# false positives after whitespace normalization alone, all traced to this
# exact cause). Only strips a marker in COLUMN 1, never mid-line (harmless
# for plain non-diff text, which rarely starts lines with +/-).
#
# Trailing-whitespace trim (08/03, 2nd real-test pass): a `<<<` here-string
# always appends a trailing newline to its input; `tr '\n\t\r' ' '` turns
# THAT newline into a trailing space, and command substitution `$(...)`
# strips trailing NEWLINES but never a trailing SPACE -- so both
# PROMPT_NORMALIZED and every citation_normalized below silently carried one
# extra trailing space. Harmless for a citation that ends mid-sentence, but
# fatal for one that ends right before punctuation (e.g. citing "...= 20"
# when the source reads "...= 20,") -- confirmed for real: this alone
# accounted for 3 of the 6 citations flagged as unverified on 08/03's second
# test pass. `sed -E 's/^ +| +$//g'` strips it back off on both sides.
PROMPT_NORMALIZED=$(sed -E 's/^[+-] ?//' <<<"$PROMPT_CONTENT" | tr '\n\t\r' '   ' | tr -s ' ' | sed -E 's/^ +| +$//g')

# Two extraction passes: Gemini sometimes wraps the citation in double
# quotes, sometimes in backticks (esp. for a code fragment), and sometimes
# adds a parenthetical filename between "citation exacte" and the colon
# (e.g. "[VERIFIE -- citation exacte (foo.py) : ...]") -- both accepted,
# never just the one literal form the system prompt happens to show as an
# example.
#
# `(?:[^"\\]|\\.)+` instead of a plain `[^"]+` (08/03, 2nd real-test pass):
# when the cited fragment itself contains a double quote (e.g. a Python
# kwarg like `source="direct_buy"`), Gemini escapes it (`\"`) inside its own
# citation string -- a naive `[^"]+` capture stops at that FIRST escaped
# quote, truncating the citation to garbage (observed for real: "source=\"
# instead of the real `source="direct_buy"`) and flagging a correct citation
# as unverified. The alternation treats any backslash-escaped character as
# one atomic unit that never ends the match, so an embedded `\"` is consumed
# rather than treated as the closing delimiter. The sed chain afterwards
# un-escapes the quote AND the whitespace escapes back to their literal form
# (08/03, 3rd real-test pass: a multi-line source fragment is sometimes cited
# with a literal two-character "\n" text marker instead of a real newline --
# e.g. "if not candles...:\n        return None" -- which survives the tr/
# squeeze normalization below untouched since tr only folds REAL newline
# bytes, not the two literal characters backslash+n. Converting these to a
# space BEFORE normalization makes the citation match the source text's own
# real line break, which normalization already flattens to a space) so the
# comparison below matches the real source text.
UNVERIFIED_CITATIONS=0
while IFS= read -r citation; do
  [ -z "$citation" ] && continue
  citation_normalized=$(tr '\n\t\r' '   ' <<<"$citation" | tr -s ' ' | sed -E 's/^ +| +$//g')
  if ! grep -qF -- "$citation_normalized" <<<"$PROMPT_NORMALIZED"; then
    UNVERIFIED_CITATIONS=$((UNVERIFIED_CITATIONS + 1))
  fi
done < <(
  {
    grep -oP '(?<=\[VERIFIE)[^:]{0,80}: *"\K(?:[^"\\]|\\.)+' <<<"$RESPONSE_CONTENT" | sed 's/\\"/"/g; s/\\n/ /g; s/\\t/ /g; s/\\r/ /g' | tr -s ' ' || true
    grep -oP '(?<=\[VERIFIE)[^:]{0,80}: *`\K(?:[^`\\]|\\.)+' <<<"$RESPONSE_CONTENT" | sed 's/\\`/`/g; s/\\n/ /g; s/\\t/ /g; s/\\r/ /g' | tr -s ' ' || true
  }
)

if [ "$UNVERIFIED_CITATIONS" -gt 0 ]; then
  echo "ATTENTION -- ${UNVERIFIED_CITATIONS} citation(s) marquee(s) [VERIFIE] n'ont PAS ete retrouvees telles quelles dans le texte envoye -- possible hallucination de citation, relis ces passages avec prudence avant d'agir dessus." >&2
fi

echo "$RESPONSE_CONTENT"
