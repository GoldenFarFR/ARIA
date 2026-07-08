#!/bin/bash
# UserPromptSubmit hook — SAUVEGARDE AUTO DE SESSION.
#
# But (demandé par l'opérateur) : tous les N messages, rappeler de mettre à jour les
# fichiers qui résument la session, pour que CLAUDE.md reste TOUJOURS alimenté et qu'une
# nouvelle session reparte à jour. Le compteur est un FICHIER (déterministe, survit à la
# compaction du contexte) : le modèle ne compte pas à la main (peu fiable).
#
# Mécanisme : à chaque prompt utilisateur on incrémente .claude/.msg-counter ; tous les 20,
# on écrit sur stdout un rappel qui est injecté dans le contexte du modèle (contrat
# UserPromptSubmit) → l'assistant propose alors la mise à jour, puis continue normalement.
#
# Ne bloque JAMAIS le prompt : toute erreur est absorbée, exit 0 systématique.
set -uo pipefail

# Draine le JSON d'entrée (non utilisé — on ne fait que compter).
cat >/dev/null 2>&1 || true

ROOT="${CLAUDE_PROJECT_DIR:-/home/user/ARIA}"
COUNTER="$ROOT/.claude/.msg-counter"
INTERVAL=20

mkdir -p "$ROOT/.claude" 2>/dev/null || true

n=$(cat "$COUNTER" 2>/dev/null || echo 0)
case "$n" in ''|*[!0-9]*) n=0 ;; esac
n=$((n + 1))
printf '%s\n' "$n" > "$COUNTER" 2>/dev/null || true

if [ "$INTERVAL" -gt 0 ] && [ $((n % INTERVAL)) -eq 0 ]; then
  cat <<EOF
🔔 CHECKPOINT SESSION ($n messages) — sauvegarde auto de contexte.
Avant de traiter la demande ci-dessous, propose à l'opérateur EN UNE LIGNE de mettre à
jour les fichiers de résumé de session (pour garder CLAUDE.md alimenté et une nouvelle
session prête) :
  - docs/HANDOFF-<date>.md : état, décisions, commits de la session
  - CLAUDE.md : faits établis / capacités / automatismes si ça a changé
  - docs/etat-systeme-cable.md : si le câblage a évolué
S'il dit oui : mets-les à jour, puis commit + push sur main. Sinon : continue normalement.
Ne laisse pas ce rappel remplacer la réponse à sa demande.
EOF
fi

exit 0
