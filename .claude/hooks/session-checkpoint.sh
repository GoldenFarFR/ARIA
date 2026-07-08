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

# ── Rappel de DÉPLOIEMENT VPS (déclenché par la taille du delta non déployé) ──────────
# Mesure les lignes changées (ajoutées + supprimées) sur `main` depuis le dernier
# déploiement (marqueur .claude/last-deployed-ref, SUIVI par git). Au-delà du seuil, on
# rappelle de déployer manuellement. Throttle : un rappel par nouvel état de main (pas à
# chaque message). L'écriture .undeployed-lines alimente la barre de statut.
DEPLOY_THRESHOLD=2500
REF_FILE="$ROOT/.claude/last-deployed-ref"
REMINDED="$ROOT/.claude/.deploy-reminded-ref"
UNDEPLOYED="$ROOT/.claude/.undeployed-lines"

if command -v git >/dev/null 2>&1 && [ -f "$REF_FILE" ]; then
  ( cd "$ROOT" 2>/dev/null || exit 0
    ref=$(tr -d '[:space:]' < "$REF_FILE" 2>/dev/null)
    target=$(git rev-parse main 2>/dev/null || git rev-parse HEAD 2>/dev/null)
    [ -z "$ref" ] && exit 0
    [ -z "$target" ] && exit 0
    git cat-file -e "${ref}^{commit}" 2>/dev/null || exit 0

    shortstat=$(git diff --shortstat "$ref" "$target" 2>/dev/null)
    ins=$(printf '%s' "$shortstat" | grep -oE '[0-9]+ insertion' | grep -oE '^[0-9]+' || true)
    del=$(printf '%s' "$shortstat" | grep -oE '[0-9]+ deletion' | grep -oE '^[0-9]+' || true)
    total=$(( ${ins:-0} + ${del:-0} ))
    printf '%s\n' "$total" > "$UNDEPLOYED" 2>/dev/null || true

    last=""
    [ -f "$REMINDED" ] && last=$(tr -d '[:space:]' < "$REMINDED" 2>/dev/null || true)
    if [ "$total" -ge "$DEPLOY_THRESHOLD" ] && [ "$target" != "$last" ]; then
      printf '%s\n' "$target" > "$REMINDED" 2>/dev/null || true
      shortref=$(git rev-parse --short=12 "$ref" 2>/dev/null || printf '%s' "$ref")
      cat <<EOF
🚀 RAPPEL DÉPLOIEMENT VPS — $total lignes accumulées depuis le dernier déploiement ($shortref) ; seuil $DEPLOY_THRESHOLD atteint.
Affiche à l'opérateur UNE SEULE LIGNE de rappel, style : « 🚀 Déploiement VPS conseillé — quota 2500 lignes atteint ».
Puis CONTINUE normalement (dépasser le seuil ne bloque rien ; ne t'arrête pas, ne colle PAS les commandes sauf s'il les demande).
Les commandes de déploiement restent disponibles sur demande ("go" / "les commandes").
Quand il CONFIRME un déploiement : mets .claude/last-deployed-ref = commit déployé (git rev-parse main), puis commit + push (remise à zéro).
EOF
    fi
  )
fi

exit 0
