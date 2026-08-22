#!/bin/bash
# UserPromptSubmit hook -- PLAFOND DE CONTEXTE (21/08).
#
# Pourquoi il existe : CLAUDE.md impose un compactage a 60% du contexte, mecanise
# le 03/08 via `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=60` dans .claude/settings.json.
# L'operateur a constate le 21/08 que le contexte etait a 76% malgre ce reglage
# TOUJOURS PRESENT dans le fichier : la variable n'est plus honoree par la
# version courante de Claude Code. Un reglage qui a cesse de fonctionner sans
# rien signaler -- exactement la derive que le registre de parametres attrape
# pour le code, et que rien ne surveillait pour la configuration.
#
# Ce hook ne PEUT PAS compacter (seuls l'utilisateur ou le harness le peuvent).
# Il rend le depassement VISIBLE et impossible a ignorer, ce qui est le maximum
# atteignable depuis un hook.
#
# Degradation propre : si le JSON d'entree ne porte pas le pourcentage (format
# different selon la version), le hook se tait au lieu d'alerter a tort.
# Anti-repetition : une seule alerte par tranche de 5 points franchie, sinon
# le rappel devient du bruit qu'on apprend a ignorer -- ce qui le rendrait pire
# qu'inutile.
#
# Ne bloque JAMAIS le prompt : toute erreur est absorbee, exit 0 systematique.
set -uo pipefail

SEUIL=60
ROOT="${CLAUDE_PROJECT_DIR:-/opt/aria}"
ETAT="$ROOT/.claude/.context-ceiling-state"

INPUT="$(cat 2>/dev/null || true)"

# 22/08 -- plusieurs emplacements essayes, plus un calcul de repli. Le hook
# lisait UNIQUEMENT `.context_window.used_percentage` et n'a rien ecrit
# pendant quatre heures pendant que le contexte montait a 65% : le champ
# n'arrive pas sous ce nom. Un garde-fou qui depend d'un seul nom de champ
# non documente est un garde-fou qui tombe en silence -- exactement ce qui
# s'est passe, et exactement ce que l'operateur a du reperer lui-meme.
PCT="$(printf '%s' "$INPUT" | jq -r '
    .context_window.used_percentage
    // .context.used_percentage
    // .usedPercentage
    // .context_window.percentage
    // empty' 2>/dev/null || true)"

# Repli : si seuls les jetons sont fournis, on calcule le pourcentage.
if [ -z "$PCT" ]; then
  PCT="$(printf '%s' "$INPUT" | jq -r '
      (.context_window.used_tokens // .context_window.tokens_used // empty) as $u
      | (.context_window.max_tokens // .context_window.total_tokens // empty) as $m
      | if ($u and $m and $m > 0) then (100 * $u / $m) else empty end' 2>/dev/null || true)"
fi

# Toujours rien -> on trace CE QU'ON A RECU, une fois, pour pouvoir reparer
# au lieu de deviner une seconde fois.
if [ -z "$PCT" ]; then
  TRACE="$ROOT/.claude/.context-ceiling-payload"
  [ -s "$TRACE" ] || printf '%s' "$INPUT" | head -c 4000 > "$TRACE" 2>/dev/null || true
  exit 0
fi

PCT_INT="${PCT%.*}"
[ -n "$PCT_INT" ] && [ "$PCT_INT" -eq "$PCT_INT" ] 2>/dev/null || exit 0
[ "$PCT_INT" -ge "$SEUIL" ] || { rm -f "$ETAT" 2>/dev/null || true; exit 0; }

# Une alerte par tranche de 5 points, pas une par message.
TRANCHE=$(( PCT_INT / 5 * 5 ))
DERNIERE="$(cat "$ETAT" 2>/dev/null || echo 0)"
[ "$TRANCHE" -gt "$DERNIERE" ] || exit 0
printf '%s' "$TRANCHE" > "$ETAT" 2>/dev/null || true

cat <<MSG
PLAFOND DE CONTEXTE DEPASSE : ${PCT_INT}% (seuil CLAUDE.md : ${SEUIL}%).

Le compactage automatique ne se declenche PLUS malgre
CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=60 toujours present dans .claude/settings.json
-- constate par l'operateur le 21/08. Ce hook remplace ce reglage mort.

A faire MAINTENANT, avant de lancer une analyse lourde :
  - proposer /compact a l'operateur, ou
  - synthetiser l'etat en cours dans le HANDOFF du composant concerne
    pour qu'une session compactee reparte sans rien perdre.

Rappel CLAUDE.md : "le compactage a 60% est un filet, pas une excuse pour
remplir la fenetre" -- synthetiser AVANT l'analyse lourde, pas au milieu.
MSG
exit 0
