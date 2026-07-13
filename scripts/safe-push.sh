#!/usr/bin/env bash
# safe-push.sh — push d'une branche temporaire VPS, jamais silencieusement vers le
# mauvais dépôt.
#
# Contexte (13/07) : deux incidents où un VPS a "réussi" un `git push origin ...`
# sans aucune erreur, mais contre le mauvais dépôt (origin mal configuré côté
# session) -- une fois vers aria-ops au lieu d'ARIA, une fois vers un dépôt
# introuvable des deux. Ce script remplace `git push origin <branche>` dans les
# dispatchs VPS : il vérifie que le remote local correspond bien au dépôt visé
# AVANT de pousser, et pousse toujours vers une URL explicite (jamais l'alias
# `origin`) -- un push vers le mauvais dépôt devient une erreur bloquante et
# visible au lieu d'un succès silencieux.
#
# Usage : scripts/safe-push.sh <ARIA|aria-ops> <nom-de-branche>
#   La branche DOIT commencer par "claude/" et ne peut jamais être "main"
#   (autorité de commit centralisée : un VPS ne pousse jamais sur main).

set -euo pipefail

REPO_TARGET="${1:-}"
BRANCH_NAME="${2:-}"

if [[ -z "$REPO_TARGET" || -z "$BRANCH_NAME" ]]; then
    echo "Usage: $0 <ARIA|aria-ops> <nom-de-branche>" >&2
    exit 2
fi

declare -A REPO_URLS=(
    [ARIA]="https://github.com/GoldenFarFR/ARIA.git"
    [aria-ops]="https://github.com/GoldenFarFR/aria-ops.git"
)

EXPECTED_URL="${REPO_URLS[$REPO_TARGET]:-}"
if [[ -z "$EXPECTED_URL" ]]; then
    echo "Erreur : dépôt cible inconnu '$REPO_TARGET'. Cibles valides : ${!REPO_URLS[*]}" >&2
    exit 2
fi

if [[ "$BRANCH_NAME" == "main" ]]; then
    echo "Erreur : un VPS ne pousse jamais sur main (autorité de commit centralisée)." >&2
    exit 2
fi

if [[ "$BRANCH_NAME" != claude/* ]]; then
    echo "Erreur : la branche doit commencer par 'claude/' (convention branche temporaire VPS)." >&2
    exit 2
fi

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "Erreur : pas dans un dépôt git." >&2
    exit 2
fi

# Normalisation : ramène HTTPS (https://github.com/OWNER/REPO.git), SSH
# (git@github.com:OWNER/REPO.git) et proxy local (.../git/OWNER/REPO) à la même
# forme canonique "owner/repo" -- insensible à la casse, tolère un .git final.
_normalize() {
    local url="${1%.git}"
    url="$(echo "$url" | sed -E 's#.*[:/]([^/]+/[^/]+)$#\1#')"
    echo "$url" | tr '[:upper:]' '[:lower:]'
}

ACTUAL_ORIGIN="$(git remote get-url origin 2>/dev/null || echo "")"
if [[ -z "$ACTUAL_ORIGIN" ]]; then
    echo "Erreur : aucun remote 'origin' configuré dans ce dépôt local." >&2
    exit 2
fi

if [[ "$(_normalize "$ACTUAL_ORIGIN")" != "$(_normalize "$EXPECTED_URL")" ]]; then
    echo "REFUS DE PUSH — le remote 'origin' local ne correspond pas au dépôt visé." >&2
    echo "  Cible demandée : $REPO_TARGET -> $EXPECTED_URL" >&2
    echo "  origin réel    : $ACTUAL_ORIGIN" >&2
    echo "Ce checkout local n'est probablement pas le bon dépôt -- ne pas forcer, vérifier avant de continuer." >&2
    exit 1
fi

echo "Remote vérifié : origin correspond bien à $REPO_TARGET ($EXPECTED_URL)."
# Pousse vers l'URL RÉELLE de origin (ACTUAL_ORIGIN, déjà lue et vérifiée
# ci-dessus), jamais vers l'alias "origin" par son nom (protection d'origine
# conservée : toujours une URL explicite, jamais une confiance aveugle en
# l'alias) -- et jamais vers EXPECTED_URL codée en dur en HTTPS non plus :
# un checkout configuré en SSH (clé de déploiement, ex. alias github-aria-main)
# n'a aucune credential HTTPS disponible, donc pousser vers l'URL HTTPS
# littérale échoue ("could not read Username for 'https://github.com'") même
# quand origin est parfaitement le bon dépôt. ACTUAL_ORIGIN porte la bonne
# méthode d'auth pour CE checkout ET reste une URL explicite déjà validée.
echo "Push vers $ACTUAL_ORIGIN (vérifié = $REPO_TARGET) HEAD:refs/heads/$BRANCH_NAME ..."
git push "$ACTUAL_ORIGIN" "HEAD:refs/heads/$BRANCH_NAME"
