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
# meme. 96000 + reasoning.effort=medium reproduit exactement la config deja
# verifiee en conditions reelles par consult-gemini.sh -- jamais reinventee ici.
DEVILS_ADVOCATE_MODEL="anthropic/claude-fable-5"
DEVILS_ADVOCATE_MAX_TOKENS=96000

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

# $1 = contenu du diff, $2 = index memoire partagee (noms de fichiers), $3 = cle API
# Ecrit la reponse JSON brute d'OpenRouter sur stdout, le statut HTTP sur stderr
# (prefixe "HTTP_STATUS:") -- laisse l'appelant decider comment reagir a un echec.
devils_advocate_call() {
  local diff_content="$1" inbox_index="$2" or_key="$3"
  local user_content system_prompt payload resp_tmp http_status

  system_prompt=$(devils_advocate_system_prompt)
  user_content="[MEMOIRE PARTAGEE -- fiches deja deposees]
${inbox_index}

[DIFF]
${diff_content}"

  payload=$(jq -n \
    --arg model "$DEVILS_ADVOCATE_MODEL" \
    --arg system "$system_prompt" \
    --arg user "$user_content" \
    --argjson max_tokens "$DEVILS_ADVOCATE_MAX_TOKENS" \
    '{model: $model, max_tokens: $max_tokens, reasoning: {effort: "medium"}, messages: [{role: "system", content: $system}, {role: "user", content: $user}]}')

  resp_tmp=$(mktemp /tmp/devils-advocate-response.XXXXXX.json)
  http_status=$(curl -s -o "$resp_tmp" -w "%{http_code}" \
    --max-time 120 \
    -X POST https://openrouter.ai/api/v1/chat/completions \
    -H "Authorization: Bearer $or_key" \
    -H "Content-Type: application/json" \
    -H "HTTP-Referer: https://github.com/GoldenFarFR/aria-vanguard" \
    -H "X-Title: ARIA Devil's Advocate" \
    -d "$payload")

  cat "$resp_tmp"
  echo "HTTP_STATUS:${http_status}" >&2
  rm -f "$resp_tmp"
}
