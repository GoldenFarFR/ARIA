#!/usr/bin/env bash
# devils-advocate-lib.sh -- coeur PARTAGE (modele, prompt systeme, appel API) entre
# le hook async post-push (devils-advocate-review.sh, 18/07) et la verification
# SYNCHRONE pre-commit (devils-advocate-precommit.sh, 04/08 -- demande operateur
# explicite : capter la critique AVANT un commit officiel, pas seulement apres un
# push deja parti). Jamais une deuxieme copie du prompt/modele qui pourrait diverger
# silencieusement -- les deux appelants sourcent CE fichier.
#
# 04/08 -- modele officialise sur Claude Fable 5 (decision operateur explicite),
# remplace Gemini 3.1 Pro. Casse deliberement la doctrine d'origine ("jamais le
# meme labo qui se juge lui-meme") -- l'operateur a tranche apres une comparaison
# directe, meme diff, meme prompt Avocat du Diable, deux modeles : Fable 5 a
# trouve le meme angle mort architectural que Gemini SANS son erreur factuelle
# (Gemini pretendait a tort que le header du rapport etait lui-meme genere/
# fragile face a une hallucination de format -- faux, c'est du bash deterministe),
# et a propose une architecture concretement meilleure (file pending/archived,
# voir devils-advocate-review.sh). Verdict operateur explicite : "cette doctrine
# n'est pas viable, on a prouve que fable est tellement efficace que la doctrine
# s'annule". Distinct de la gouvernance "usage rare" de scripts/consult-gemini.sh
# (second avis MANUEL, sur vrai blocage) -- ce hook-ci reste automatique, sur
# CHAQUE push vers main, cout accepte explicitement par l'operateur a cette
# frequence (~0,28$/push, ~3,5x Gemini) A CONDITION de batcher les push (ne pas
# pousser un commit isole par petit correctif -- meme jour, meme decision).
#
# Format de payload Fable 5 (LEÇON DEJA CONNUE dans ce projet, consult-gemini.sh
# 03/08) : le raisonnement interne ("thinking") est TOUJOURS actif sur Fable 5 et
# partage le meme budget max_tokens que la reponse visible -- sur un diff
# long/complexe (le cas typique de cet Avocat du Diable), un max_tokens trop bas
# (l'ancien 4000, calibre pour Gemini) produit une reponse VIDE, facturee quand
# meme. 96000 + budget_tokens=32000 laisse 64000 tokens a la reponse visible,
# meme marge que la config deja verifiee en conditions reelles par
# consult-gemini.sh -- jamais reinventee ici.
#
# 10/08 -- bascule OpenRouter -> API Anthropic directe (decision operateur
# explicite) : OpenRouter avait deja produit un vrai incident (credits
# epuises, HTTP 402, Avocat du Diable silencieusement mort pendant cette
# fenetre) -- l'API Anthropic directe retire cette dependance tierce, meme
# modele (Fable 5), memes controles cote appelant (cle jamais gardee dans le
# shell host, jamais affichee/loggee). Nom de modele SANS le prefixe
# "anthropic/" propre a OpenRouter -- l'API Anthropic directe utilise l'ID nu.
#
# 10/08 -- premier test reel en direct : "thinking.type: enabled" +
# "budget_tokens" (calque OpenRouter) rejete en HTTP 400 par l'API pour ce
# modele -- message d'erreur explicite : Fable 5 exige "thinking.type:
# adaptive" + "output_config.effort", pas le format extended-thinking
# classique. Corrige apres verification live (jamais suppose).
DEVILS_ADVOCATE_MODEL="claude-fable-5"
DEVILS_ADVOCATE_MAX_TOKENS=96000
DEVILS_ADVOCATE_THINKING_EFFORT="medium"

# 10/08 -- suivi de cout reel par appel (demande operateur explicite, apres
# l'incident "je paye pour rien" du 04/08 deja documente plus haut -- cette
# fois le suivi est MECANIQUE, pas une estimation deduite apres coup).
# Tarifs verifies en direct (platform.claude.com/docs/en/about-claude/pricing,
# 10/08, jamais devine) -- Claude Fable 5 : $10/MTok input base, $50/MTok
# output (le "thinking" facture comme de l'output, deja implicite dans
# usage.output_tokens de l'API Anthropic -- aucun champ separe a sommer).
DEVILS_ADVOCATE_INPUT_USD_PER_MTOK="10"
DEVILS_ADVOCATE_OUTPUT_USD_PER_MTOK="50"

# 10/08 -- condensation Haiku 4.5 (voir devils_advocate_condense plus bas) --
# tarifs verifies en direct le meme jour, meme source. $1/MTok input,
# $5/MTok output -- ~10x moins cher que Fable 5, coherent avec son role de
# pre-passe rapide/bon marche plutot que d'analyse.
DEVILS_ADVOCATE_CONDENSE_MODEL="claude-haiku-4-5"
DEVILS_ADVOCATE_CONDENSE_INPUT_USD_PER_MTOK="1"
DEVILS_ADVOCATE_CONDENSE_OUTPUT_USD_PER_MTOK="5"

# $1 = reponse JSON brute Anthropic, $2 = prix input $/MTok (defaut Fable 5),
# $3 = prix output $/MTok (defaut Fable 5) -- ecrit "input_tokens output_tokens
# cost_usd" sur stdout, espace-separe ("0 0 0.000000" si le JSON n'a pas de
# bloc usage exploitable, jamais un echec dur -- le suivi de cout ne doit
# jamais bloquer le rapport lui-meme).
devils_advocate_cost() {
  local raw_response="$1"
  local price_in="${2:-$DEVILS_ADVOCATE_INPUT_USD_PER_MTOK}"
  local price_out="${3:-$DEVILS_ADVOCATE_OUTPUT_USD_PER_MTOK}"
  local in_tok out_tok
  in_tok=$(echo "$raw_response" | jq -r '.usage.input_tokens // 0' 2>/dev/null)
  out_tok=$(echo "$raw_response" | jq -r '.usage.output_tokens // 0' 2>/dev/null)
  [[ "$in_tok" =~ ^[0-9]+$ ]] || in_tok=0
  [[ "$out_tok" =~ ^[0-9]+$ ]] || out_tok=0
  local cost
  cost=$(awk -v i="$in_tok" -v o="$out_tok" -v pi="$price_in" -v po="$price_out" \
    'BEGIN { printf "%.6f", (i * pi / 1000000) + (o * po / 1000000) }')
  echo "$in_tok $out_tok $cost"
}

# Journal append-only dedie au cout (distinct de architect-review.log, qui
# reste un log d'EVENEMENTS -- celui-ci est une serie chiffree, une ligne par
# appel reellement facture, pensee pour etre sommee/tracee dans le temps).
DEVILS_ADVOCATE_COST_LOG="/opt/aria-data/architect-reports/cost-log.csv"

# $1 = sha court ou identifiant de fenetre, $2 = input_tokens, $3 = output_tokens, $4 = cost_usd
devils_advocate_log_cost() {
  local label="$1" in_tok="$2" out_tok="$3" cost="$4"
  mkdir -p "$(dirname "$DEVILS_ADVOCATE_COST_LOG")" 2>/dev/null || true
  if [ ! -f "$DEVILS_ADVOCATE_COST_LOG" ]; then
    echo "timestamp_utc,label,input_tokens,output_tokens,cost_usd" > "$DEVILS_ADVOCATE_COST_LOG"
  fi
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ),${label},${in_tok},${out_tok},${cost}" >> "$DEVILS_ADVOCATE_COST_LOG"
}

devils_advocate_system_prompt() {
  cat <<'PROMPT_EOF'
Tu es un Architecte Logiciel Senior et "l'Avocat du Diable" du projet ARIA
(agent IA autonome de trading/analyse crypto sur Base). Ton role n'est PAS de
valider le code qui t'est soumis ni de chercher des erreurs de syntaxe. Ton
unique objectif : trouver les limites, les angles morts architecturaux, et
proposer des ameliorations radicales (changements de paradigme) au code
fraichement modifie.

Le code fourni vient d'etre modifie. Il fonctionne dans l'etat actuel.
Determine s'il va casser sous une charge/echelle 10x superieure, ou s'il
aurait fallu une approche differente depuis le depart.

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
}

# $1 = contenu du diff, $2 = index memoire partagee (noms de fichiers), $3 = cle API Anthropic,
# $4 = system prompt optionnel (14/08, review de backlog non-code) -- vide/omis retombe sur
# devils_advocate_system_prompt (comportement historique inchange pour les 2 appelants existants).
# Ecrit la reponse JSON brute de l'API Anthropic (Messages API) sur stdout, le
# statut HTTP sur stderr (prefixe "HTTP_STATUS:") -- laisse l'appelant decider
# comment reagir a un echec.
devils_advocate_call() {
  local diff_content="$1" inbox_index="$2" api_key="$3" system_prompt_override="${4:-}"
  local user_content system_prompt payload resp_tmp http_status

  system_prompt="${system_prompt_override:-$(devils_advocate_system_prompt)}"
  user_content="[MEMOIRE PARTAGEE -- fiches deja deposees]
${inbox_index}

[DIFF]
${diff_content}"

  payload=$(jq -n \
    --arg model "$DEVILS_ADVOCATE_MODEL" \
    --arg system "$system_prompt" \
    --arg user "$user_content" \
    --argjson max_tokens "$DEVILS_ADVOCATE_MAX_TOKENS" \
    --arg effort "$DEVILS_ADVOCATE_THINKING_EFFORT" \
    '{model: $model, max_tokens: $max_tokens, system: $system, thinking: {type: "adaptive"}, output_config: {effort: $effort}, messages: [{role: "user", content: $user}]}')

  resp_tmp=$(mktemp /tmp/devils-advocate-response.XXXXXX.json)
  http_status=$(curl -s -o "$resp_tmp" -w "%{http_code}" \
    --max-time 300 \
    -X POST https://api.anthropic.com/v1/messages \
    -H "x-api-key: $api_key" \
    -H "anthropic-version: 2023-06-01" \
    -H "content-type: application/json" \
    -d "$payload")

  cat "$resp_tmp"
  echo "HTTP_STATUS:${http_status}" >&2
  rm -f "$resp_tmp"
}

# 10/08 -- condensation par Haiku 4.5 au lieu d'une troncature brute --
# demande operateur explicite ("il suffirait que tu envoie un resume toi
# meme condense au lieu de 10k brut non ?") apres avoir releve qu'une simple
# troncature a 60000 caracteres perd silencieusement la fin de tout diff
# au-dela de ~2000-2500 lignes (mesure reelle), un angle mort qui grandirait
# avec le seuil de batching (BATCH_THRESHOLD_LINES) au lieu de le suivre.
# Un modele rapide/bon marche condense le diff COMPLET en une synthese
# structuree (fichiers/fonctions touches, nature de chaque changement) au
# lieu de couper la fin -- Fable 5 recoit alors une vue sur TOUT le diff,
# jamais juste son debut.
# 10/08 (raffine le meme jour, demande operateur "essaye de voir le max sans
# casser la qualite") -- une cible de sortie FIXE (20000 caracteres) fait
# grossir le ratio de compression sans limite avec la taille du diff d'entree
# (8:1 vers 140-170k caracteres deja verifie en conditions reelles -> 33:1 a
# 670k/10000 lignes) : plus le batch est gros, plus chaque fichier touche
# recoit un resume proportionnellement plus maigre, un vrai risque de perte
# de detail jamais mesure formellement (pas de juge de qualite construit --
# honnete plutot que d'inventer un score). La cible SCALE desormais avec la
# taille reelle du diff (1/8e, plancher 15000, plafond 45000) pour garder un
# ratio de compression borne (~8:1 au plancher, jamais pire que ~12:1 au
# plafond) au lieu de degrader sans limite -- decouple le seuil de batching
# (BATCH_THRESHOLD_LINES) de la qualite de la condensation, les deux ne
# doivent plus se renforcer negativement (angle mort releve par l'Avocat du
# Diable lui-meme sur son propre diff de migration, cf. HANDOFF_AUTOMATISATION.md).
DEVILS_ADVOCATE_CONDENSE_THRESHOLD_CHARS=60000
DEVILS_ADVOCATE_CONDENSE_TARGET_RATIO=8
DEVILS_ADVOCATE_CONDENSE_TARGET_FLOOR_CHARS=15000
DEVILS_ADVOCATE_CONDENSE_TARGET_CEILING_CHARS=45000

# $1 = longueur du diff brut en caracteres -- ecrit la cible de sortie (chars)
devils_advocate_condense_target_chars() {
  local len="$1"
  awk -v l="$len" -v r="$DEVILS_ADVOCATE_CONDENSE_TARGET_RATIO" \
      -v floor="$DEVILS_ADVOCATE_CONDENSE_TARGET_FLOOR_CHARS" -v ceil="$DEVILS_ADVOCATE_CONDENSE_TARGET_CEILING_CHARS" \
    'BEGIN { t = l / r; if (t < floor) t = floor; if (t > ceil) t = ceil; printf "%d", t }'
}

# $1 = cible de sortie en caracteres (calculee par devils_advocate_condense_target_chars)
devils_advocate_condense_system_prompt() {
  local target_chars="$1"
  cat <<PROMPT_EOF
Tu condenses un diff git TROP LONG pour etre envoye en entier a un modele
d'analyse architecturale. Ta sortie remplace le diff complet -- elle doit
donc couvrir TOUT le diff (jamais seulement le debut), jamais une simple
troncature.

Pour CHAQUE fichier touche : nom du fichier, nature du changement (nouveau
fichier / fichier supprime / fonction ajoutee-modifiee-supprimee / constante
ou config changee / pur renommage), et pour les changements de LOGIQUE
(pas le pur formatage), un resume fidele de ce qui a change et pourquoi si
un commentaire l'explique dans le diff. Cite les extraits de code
REELLEMENT significatifs (nouvelle fonction cle, changement de comportement)
mot pour mot si courts (<15 lignes), sinon resume-les fidelement sans
inventer de detail absent du diff. Ne saute AUCUN fichier, meme pour dire
juste "renommage sans changement de logique".

Cible ~${target_chars} caracteres en sortie. Rien d'autre autour -- pas de
preambule, pas de conclusion generale.
PROMPT_EOF
}

# $1 = diff complet, $2 = cle API Anthropic, $3 = cible de sortie en
# caracteres -- ecrit la reponse JSON brute Anthropic sur stdout (meme
# format que devils_advocate_call, pour reutiliser devils_advocate_cost
# dessus), le statut HTTP sur stderr.
devils_advocate_condense() {
  local diff_content="$1" api_key="$2" target_chars="$3"
  local system_prompt payload resp_tmp http_status

  system_prompt=$(devils_advocate_condense_system_prompt "$target_chars")
  payload=$(jq -n \
    --arg model "$DEVILS_ADVOCATE_CONDENSE_MODEL" \
    --arg system "$system_prompt" \
    --arg user "$diff_content" \
    --argjson max_tokens 16000 \
    '{model: $model, max_tokens: $max_tokens, system: $system, messages: [{role: "user", content: $user}]}')

  resp_tmp=$(mktemp /tmp/devils-advocate-condense.XXXXXX.json)
  http_status=$(curl -s -o "$resp_tmp" -w "%{http_code}" \
    --max-time 120 \
    -X POST https://api.anthropic.com/v1/messages \
    -H "x-api-key: $api_key" \
    -H "anthropic-version: 2023-06-01" \
    -H "content-type: application/json" \
    -d "$payload")

  cat "$resp_tmp"
  echo "HTTP_STATUS:${http_status}" >&2
  rm -f "$resp_tmp"
}

# 10/08 (soir) -- gap reel trouve par l'Avocat du Diable SUR SON PROPRE
# fonctionnement (rapport 8e01e6fb, root cause verifiee via WebSearch avant
# tout correctif, jamais gobee telle quelle) : le repli brut ci-dessous
# plafonnait a DEVILS_ADVOCATE_CONDENSE_TARGET_CEILING_CHARS (45000) quel
# que soit len -- sur un diff cumule de 720341 caracteres, Fable 5 ne voyait
# reellement que ~6% du batch. Cause racine confirmee : Haiku 4.5 a une
# fenetre de 200k tokens ; un diff de 720341 caracteres tombe autour de
# 180-240k tokens selon le ratio reel, assez pour depasser la fenetre et
# produire le HTTP 400 observe -- un simple retry n'aurait RIEN corrige
# (meme cause, meme echec a coup sur). D'ou le decoupage par tranches
# ci-dessous : chaque tranche reste tres en-dessous de 200k tokens meme au
# ratio le plus defavorable (3 car/tok -> 40k tokens pour 120000 caracteres),
# jamais plus un seul appel monolithique sur un diff de cette taille.
DEVILS_ADVOCATE_CHUNK_MAX_CHARS=120000

# $1 = diff complet -- decoupe en tranches sur les frontieres de fichiers
# ("diff --git", jamais un fichier coupe en deux) sans depasser
# DEVILS_ADVOCATE_CHUNK_MAX_CHARS par tranche (sauf un unique fichier deja
# plus gros a lui seul, garde entier dans sa propre tranche -- rare, reste
# un cas degrade connu, jamais silencieux vu le retry+marqueur d'echec plus
# bas). Ecrit chaque tranche dans un fichier temporaire distinct sous /tmp,
# imprime la LISTE des chemins sur stdout (un par ligne).
devils_advocate_split_diff_by_file() {
  local diff_content="$1"
  local tmp_prefix
  tmp_prefix=$(mktemp -u /tmp/devils-advocate-chunk.XXXXXX)
  # Bin-packing PAR FICHIER COMPLET, pas ligne a ligne : la taille d'un
  # fichier n'est connue qu'une fois vu son debut ET sa fin (prochaine ligne
  # "diff --git", ou fin de flux) -- decider AVANT (sur la seule ligne
  # d'en-tete, ~30 car.) sous-estime systematiquement le risque de
  # depassement pour tout fichier au corps volumineux (bug reel trouve et
  # corrige en testant ce script avant tout usage reel : un fichier de
  # 150000 caracteres pouvait s'ajouter par-dessus un buffer deja a 90000,
  # produisant une tranche a 240000 caracteres, 2x le plafond vise).
  echo "$diff_content" | awk -v prefix="$tmp_prefix" -v max_chars="$DEVILS_ADVOCATE_CHUNK_MAX_CHARS" '
    function flush_tranche() {
      if (buf_len > 0) {
        n++
        outfile = prefix "." n
        printf "%s", buf > outfile
        close(outfile)
        print outfile
      }
      buf = ""
      buf_len = 0
    }
    function commit_current_file() {
      if (cur_len > 0) {
        if (buf_len > 0 && buf_len + cur_len > max_chars) {
          flush_tranche()
        }
        buf = buf cur
        buf_len += cur_len
      }
      cur = ""
      cur_len = 0
    }
    /^diff --git / { commit_current_file() }
    {
      cur = cur $0 "\n"
      cur_len += length($0) + 1
    }
    END {
      commit_current_file()
      flush_tranche()
    }
  '
}

# $1 = chemin du fichier de tranche, $2 = cle API, $3 = label de cout,
# $4 = index de tranche -- condense CETTE tranche avec 1 retry sur echec
# HTTP (couvre les echecs transitoires reels deja vecus, ex. 402 credits
# epuises) ; si les 2 tentatives echouent, retourne un marqueur explicite
# + la liste des fichiers de CETTE tranche (grep mecanique, jamais perdue)
# au lieu d'un silence. Ecrit "1" ou "0" (succes de la tranche) sur le
# descripteur 3 pour que l'appelant compte la couverture reelle sans
# parser du texte libre.
devils_advocate_condense_chunk() {
  local chunk_file="$1" api_key="$2" cost_label="$3" chunk_idx="$4"
  local chunk_content chunk_len target_chars attempt condense_resp condense_status
  chunk_content=$(cat "$chunk_file")
  chunk_len=${#chunk_content}
  target_chars=$(devils_advocate_condense_target_chars "$chunk_len")

  for attempt in 1 2; do
    condense_resp=$(devils_advocate_condense "$chunk_content" "$api_key" "$target_chars" 2>/tmp/devils-advocate-condense-status.$$)
    condense_status=$(grep -oE 'HTTP_STATUS:[0-9]+' /tmp/devils-advocate-condense-status.$$ | cut -d: -f2)
    rm -f /tmp/devils-advocate-condense-status.$$
    [ "$condense_status" = "200" ] && break
  done

  local file_list
  file_list=$(echo "$chunk_content" | grep -oE '^diff --git a/\S+ b/\S+' | sed -E 's#^diff --git a/\S+ b/##')

  if [ "$condense_status" != "200" ]; then
    >&3 echo "0"
    echo "[TRANCHE ${chunk_idx} NON CONDENSEE apres 2 tentatives (dernier HTTP ${condense_status}) -- ${chunk_len} caracteres non resumes. Fichiers de cette tranche (non couverts par cette revue, disponibles via git show) :
${file_list}]"
    return 0
  fi

  local condensed_text
  condensed_text=$(echo "$condense_resp" | jq -r '[.content[]? | select(.type == "text") | .text] | join("\n\n")')
  read -r c_in c_out c_cost <<< "$(devils_advocate_cost "$condense_resp" "$DEVILS_ADVOCATE_CONDENSE_INPUT_USD_PER_MTOK" "$DEVILS_ADVOCATE_CONDENSE_OUTPUT_USD_PER_MTOK")"
  devils_advocate_log_cost "condense-${cost_label}-part${chunk_idx}" "$c_in" "$c_out" "$c_cost"

  >&3 echo "1"
  echo "[TRANCHE ${chunk_idx} -- fichiers : ${file_list//$'\n'/, }]

${condensed_text}"
}

# $1 = diff complet, $2 = cle API, $3 = label pour le journal de cout,
# $4 = chemin OPTIONNEL d'un fichier ou ecrire la couverture reelle --
# retourne (stdout) le diff tel quel s'il tient sous
# DEVILS_ADVOCATE_CONDENSE_THRESHOLD_CHARS, sinon le decoupe en tranches
# (devils_advocate_split_diff_by_file) et condense CHAQUE tranche
# separement (devils_advocate_condense_chunk, retry inclus) -- plus jamais
# un seul appel Haiku monolithique sur un diff pouvant depasser sa fenetre
# de contexte.
#
# 10/08 (soir, bug reel trouve en testant AVEC un vrai appel API avant tout
# usage reel) : la 1ere version ecrivait la couverture dans une variable
# globale DA_COVERAGE_NOTE -- mais cette fonction est TOUJOURS appelee via
# une substitution de commande ($(...)), qui s'execute dans un SOUS-SHELL ;
# toute variable fixee a l'interieur ne remonte jamais a l'appelant, meme
# une variable "globale" au sens bash du terme. La note de couverture
# n'aurait donc jamais fonctionne en prod malgre les tests locaux (gratuits,
# sans appel API) qui ne pouvaient pas reveler ce bug puisqu'ils
# n'appelaient jamais la fonction via $(...). Fixe en ecrivant la note dans
# un FICHIER (le seul canal qui survit a un sous-shell) si $4 est fourni.
devils_advocate_diff_for_review() {
  local diff_content="$1" api_key="$2" cost_label="$3" coverage_file="${4:-}"
  local len=${#diff_content}
  if [ "$len" -le "$DEVILS_ADVOCATE_CONDENSE_THRESHOLD_CHARS" ]; then
    echo "$diff_content"
    return 0
  fi

  local chunk_files chunk_file chunk_idx=0 ok_count=0 total_count=0
  chunk_files=$(devils_advocate_split_diff_by_file "$diff_content")

  local combined="[DIFF CONDENSE PAR TRANCHES (CLAUDE HAIKU 4.5) -- ${len} caracteres bruts originaux, decoupes par fichier pour ne jamais depasser la fenetre de contexte de Haiku. Diff complet disponible via git log/git show sur ce commit.]
"
  while IFS= read -r chunk_file; do
    [ -z "$chunk_file" ] && continue
    chunk_idx=$((chunk_idx + 1))
    total_count=$((total_count + 1))
    local chunk_result chunk_ok
    exec 3>/tmp/devils-advocate-chunk-ok.$$
    chunk_result=$(devils_advocate_condense_chunk "$chunk_file" "$api_key" "$cost_label" "$chunk_idx")
    exec 3>&-
    chunk_ok=$(cat /tmp/devils-advocate-chunk-ok.$$ 2>/dev/null | tail -1)
    rm -f /tmp/devils-advocate-chunk-ok.$$ "$chunk_file"
    [ "$chunk_ok" = "1" ] && ok_count=$((ok_count + 1))
    combined="${combined}

${chunk_result}"
  done <<< "$chunk_files"

  if [ -n "$coverage_file" ]; then
    echo "${ok_count}/${total_count} tranches condensees avec succes" > "$coverage_file"
  fi
  echo "$combined"
}
