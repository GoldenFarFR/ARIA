#!/bin/bash
# UserPromptSubmit hook — SAUVEGARDE AUTO DE SESSION.
#
# But (demandé par l'opérateur) : tous les N messages, rappeler de mettre à jour les
# fichiers qui résument la session, pour que CLAUDE.md reste TOUJOURS alimenté et qu'une
# nouvelle session reparte à jour. Le compteur est un FICHIER (déterministe, survit à la
# compaction du contexte) : le modèle ne compte pas à la main (peu fiable).
#
# Mécanisme : à chaque prompt utilisateur on incrémente .claude/.msg-counter ; tous les
# INTERVAL messages, on écrit sur stdout un rappel qui est injecté dans le contexte du
# modèle (contrat UserPromptSubmit) → l'assistant propose alors la mise à jour, puis
# continue normalement.
#
# Ne bloque JAMAIS le prompt : toute erreur est absorbée, exit 0 systématique.
set -uo pipefail

# Draine le JSON d'entrée (non utilisé — on ne fait que compter).
cat >/dev/null 2>&1 || true

ROOT="${CLAUDE_PROJECT_DIR:-/home/user/ARIA}"
COUNTER="$ROOT/.claude/.msg-counter"
# 150, pas 1000 (03/08, avis Fable 5) -- a 1000 le rappel ne se declenchait quasiment
# jamais avant la fin d'une session, ce qui sapait la discipline HANDOFF-par-composant
# (CLAUDE.md "Regles absolues") plutot que de la faire respecter.
INTERVAL=150

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

# ── PUSH AUTO (déclenché par la taille du delta non poussé) ───────────────────────────
# 17/08 -- seuil abaisse 4000->500 et comportement change d'un simple RAPPEL a une
# INSTRUCTION D'ACTION (decision operateur explicite) : scripts/devils-advocate-review.sh
# gere deja mecaniquement le cumul reel depuis le 07/08 (LAST_REVIEWED_MARKER n'avance
# que sur un vrai appel paye Fable5, jamais sur un push sous son propre seuil de 8000
# lignes) -- pousser souvent, meme par petits lots, ne coute jamais plus cher que
# d'attendre un gros batch. Concerne UNIQUEMENT `git push` (jamais deploy.sh -- le
# deploiement reel sur le VPS reste une decision explicite de l'operateur, impact plus
# large sur le service prod). Mesure les lignes changees (ajoutees + supprimees) sur
# `main` depuis le dernier deploiement (marqueur .claude/last-deployed-ref, SUIVI par
# git) -- reutilise le meme marqueur que l'ancien rappel deploiement, la seule mesure
# de delta non-pousse/non-deploye deja disponible. Throttle : une instruction par
# nouvel etat de main. L'ecriture .undeployed-lines alimente toujours la barre de statut.
DEPLOY_THRESHOLD=500
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
🚀 PUSH AUTO -- $total lignes accumulees depuis le dernier deploiement ($shortref) ; seuil $DEPLOY_THRESHOLD atteint.
Decision operateur explicite (17/08) : des que ce seuil est atteint, POUSSE main directement (git push origin main) SANS demander confirmation -- le cout reel est deja gere par le hook Avocat du Diable (gratuit sous son propre seuil de 8000 lignes cumulees depuis le dernier vrai appel paye). Verifie juste que les tests pertinents passent avant de pousser.
Le DEPLOIEMENT (deploy.sh) reste une decision separee de l'operateur -- ne jamais le lancer automatiquement sur ce seul signal.
Apres le push : affiche a l'operateur UNE SEULE LIGNE, style : « 🚀 Poussé automatiquement -- N lignes ». Puis CONTINUE normalement.
EOF
    fi
  )
fi

# ── Rappel PROACTIF de taille CLAUDE.md (03/08) ───────────────────────────────────────
# test_coherence.py casse la CI a 100 Ko (garde-fou tardif) -- ce bloc alerte plus tot,
# a 80% du plafond, pour permettre une mini-passe de nettoyage au fil de l'eau plutot
# qu'un gros rattrapage (cf. l'incident du 22/07 : 690 lignes/~600 Ko avant compaction).
SIZE_SOFT_THRESHOLD=81920  # 80 Ko, 80% du plafond dur de 100 Ko (test_coherence.py)
CLAUDE_MD="$ROOT/CLAUDE.md"
SIZE_REMINDED="$ROOT/.claude/.claude-md-size-reminded"

if [ -f "$CLAUDE_MD" ]; then
  current_size=$(wc -c < "$CLAUDE_MD" 2>/dev/null | tr -d '[:space:]')
  case "$current_size" in ''|*[!0-9]*) current_size=0 ;; esac

  last_reminded=$(cat "$SIZE_REMINDED" 2>/dev/null | tr -d '[:space:]')
  case "$last_reminded" in ''|*[!0-9]*) last_reminded=0 ;; esac

  if [ "$current_size" -ge "$SIZE_SOFT_THRESHOLD" ] && [ "$current_size" -gt "$last_reminded" ]; then
    printf '%s\n' "$current_size" > "$SIZE_REMINDED" 2>/dev/null || true
    ko=$((current_size / 1024))
    cat <<EOF
📏 CLAUDE.md fait ~${ko} Ko (seuil doux 80 Ko, plafond dur CI 100 Ko).
Affiche à l'opérateur UNE SEULE LIGNE : « 📏 CLAUDE.md approche sa taille limite (~${ko} Ko/100 Ko) — une passe de nettoyage serait utile. »
Puis CONTINUE normalement. Voir la table "Routeur CLAUDE.md" pour classer le contenu avant d'ajouter quoi que ce soit ici.
EOF
  fi
fi

# ── Rappel RAPPORTS AVOCAT DU DIABLE non lus (04/08, gap trouvé en direct par l'opérateur :
# appels payés jamais suivis d'une lecture réelle -- "je paye pour rien") ─────────────────
# scripts/devils-advocate-review.sh écrit désormais UN FICHIER PAR PUSH dans
# architect-reports/pending/<sha>.md (jamais écrasé -- l'ancien fichier unique perdait
# silencieusement tout rapport intermédiaire sur deux pushs rapprochés, gap confirmé
# indépendamment par Gemini ET Claude Fable 5 sur le même test). "Lu" = geste EXPLICITE :
# déplacer le fichier vers architect-reports/archived/ une fois vérifié/agi. Le rappel
# ci-dessous liste tout ce qui reste en attente. Se répète (1) quand la LISTE change
# (nouvelle arrivée pendant que d'anciens sont encore en attente) OU (2) tous les
# INTERVAL messages (même compteur/cadence que le checkpoint HANDOFF ci-dessus) même si
# la liste est STABLE -- backstop trouvé en direct (Fable 5, meme session, sur ce diff
# lui-meme) : un throttle purement "liste changee" ne se repete JAMAIS si la session
# ignore le rappel et qu'aucun NOUVEAU rapport n'arrive -- exactement le bug "je paye
# pour rien" d'origine, deplace du fichier ecrase vers l'etat de rappel.
PENDING_DIR="/opt/aria-data/architect-reports/pending"
PENDING_REMINDED="$ROOT/.claude/.architect-pending-reminded-state"

if [ -d "$PENDING_DIR" ]; then
  pending_list=$(ls "$PENDING_DIR"/*.md 2>/dev/null | xargs -n1 basename 2>/dev/null | sort | tr '\n' ',')
  if [ -n "$pending_list" ]; then
    last_state=""
    [ -f "$PENDING_REMINDED" ] && last_state=$(cat "$PENDING_REMINDED" 2>/dev/null || true)
    if [ "$pending_list" != "$last_state" ] || { [ "$INTERVAL" -gt 0 ] && [ $((n % INTERVAL)) -eq 0 ]; }; then
      printf '%s' "$pending_list" > "$PENDING_REMINDED" 2>/dev/null || true
      count=$(printf '%s' "$pending_list" | tr ',' '\n' | grep -c '\.md$')
      cat <<EOF
🕵️ RAPPORTS AVOCAT DU DIABLE NON LUS ($count) -- $PENDING_DIR
Fichiers : $pending_list
Avant de continuer à écrire du code, lis-les et vérifie chaque affirmation contre le vrai code (agent externe, peut halluciner -- jamais gober). Une fois un rapport traité (agi ou explicitement jugé non-actionnable), déplace-le vers $PENDING_DIR/../archived/ pour le retirer de la file.
EOF
    fi
  fi
fi

exit 0
