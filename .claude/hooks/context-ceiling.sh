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
PCT="$(printf '%s' "$INPUT" | jq -r '.context_window.used_percentage // empty' 2>/dev/null || true)"

# Pas de donnee -> silence. Une alerte fondee sur rien serait pire que pas d'alerte.
[ -n "$PCT" ] || exit 0

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
