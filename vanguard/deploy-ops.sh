#!/usr/bin/env bash
# Déploiement du DASHBOARD OPÉRATEUR privé (statique) — ops.ariavanguardzhc.com.
#
# Même doctrine que deploy-vitrine.sh (bascule atomique, vérification avant
# suppression de l'ancien contenu, rollback automatique si la vérification
# échoue) -- réutilise LA MÊME lib partagée (deploy_vitrine_lib.sh), jamais
# une copie divergente.
#
# Ce que fait ce script (idempotent, rejouable) :
#   1) build vanguard/product-frontend/ via Docker (node:22-slim) — aucune
#      dépendance node sur l'hôte, résultat identique à la CI — et écrit un
#      marqueur de build (dist/build-info.txt, le commit court) ;
#   2) publie dist/ dans le webroot de façon ATOMIQUE (swap de dossier) sans
#      supprimer l'ancien webroot ;
#   3) `nginx -t` puis reload ;
#   4) VÉRIFIE, depuis le VPS lui-même, que le sous-domaine répond 200 ET que
#      le marqueur de build correspond exactement au commit déployé ;
#   5) supprime l'ancien webroot SEULEMENT si l'étape 4 est entièrement positive.
#
# Ce script NE touche PAS : le conteneur aria-api, ni la config TLS (gérée
# par certbot). Il ne fait AUCUNE opération git — mets le dépôt au bon commit
# AVANT (ex. `git fetch origin main && git checkout -B main origin/main`).
#
# Prérequis (installation initiale, une seule fois) : voir vanguard/nginx/ops.conf.
#
# Surcharges possibles :  REPO=/opt/aria  WEBROOT=/var/www/aria-ops  HOST=ops.ariavanguardzhc.com
#   OPS_VERIFY_RETRIES (défaut 10)  OPS_VERIFY_INTERVAL (défaut 1, secondes)

set -euo pipefail

REPO="${REPO:-/opt/aria}"
WEBROOT="${WEBROOT:-/var/www/aria-ops}"
HOST="${HOST:-ops.ariavanguardzhc.com}"
OPS_VERIFY_RETRIES="${OPS_VERIFY_RETRIES:-10}"
OPS_VERIFY_INTERVAL="${OPS_VERIFY_INTERVAL:-1}"

# shellcheck source=./deploy_vitrine_lib.sh
source "$REPO/vanguard/deploy_vitrine_lib.sh"

command -v docker >/dev/null || { echo "ERREUR: docker introuvable"; exit 1; }
[ -f "$REPO/vanguard/product-frontend/package.json" ] || { echo "ERREUR: $REPO/vanguard/product-frontend introuvable"; exit 1; }

REPO_COMMIT="$(git -C "$REPO" rev-parse --short=12 HEAD)"

echo "==> [1/5] build dashboard opérateur (Docker node:22-slim)"
docker run --rm \
    -v "$REPO":/repo -w /repo/vanguard/product-frontend node:22-slim \
    sh -c "npm ci --no-audit --no-fund && npm run build"
[ -f "$REPO/vanguard/product-frontend/dist/index.html" ] || { echo "ERREUR: dist/index.html absent — build échoué"; exit 1; }
echo "$REPO_COMMIT" > "$REPO/vanguard/product-frontend/dist/build-info.txt"
echo "    ✓ marqueur de build écrit (commit $REPO_COMMIT)"

echo "==> [2/5] publication atomique -> $WEBROOT (l'ancien contenu est conservé jusqu'à vérification)"
parent="$(dirname "$WEBROOT")"; mkdir -p "$parent"
tmp="$(mktemp -d "$parent/.ops.XXXXXX")"
cp -a "$REPO/vanguard/product-frontend/dist/." "$tmp/"
chown -R www-data:www-data "$tmp" 2>/dev/null || true
publish_atomic "$WEBROOT" "$tmp"

echo "==> [3/5] nginx -t + reload"
nginx -t
systemctl reload nginx

echo "==> [4/5] vérification (vue du VPS, avec retry -- le reload nginx n'est pas instantané)"
LAST_CODE=""; LAST_SCHEME=""; LAST_MARKER=""
verify_ops_once() {
    local code scheme marker
    code="$(curl -s -o /dev/null -w '%{http_code}' --resolve "$HOST:443:127.0.0.1" "https://$HOST/" 2>/dev/null || true)"
    if [ "$code" = "200" ]; then
        scheme="https"
        marker="$(curl -s --resolve "$HOST:443:127.0.0.1" "https://$HOST/build-info.txt" 2>/dev/null || true)"
    else
        scheme="http"
        code="$(curl -s -o /dev/null -w '%{http_code}' -H "Host: $HOST" "http://127.0.0.1/" 2>/dev/null || true)"
        marker="$(curl -s -H "Host: $HOST" "http://127.0.0.1/build-info.txt" 2>/dev/null || true)"
    fi
    LAST_CODE="$code"; LAST_SCHEME="$scheme"; LAST_MARKER="$marker"
    [ "$code" = "200" ] && [ "$marker" = "$REPO_COMMIT" ]
}

if retry_until "$OPS_VERIFY_RETRIES" "$OPS_VERIFY_INTERVAL" verify_ops_once; then
    echo "✅ OK — le dashboard opérateur répond (HTTP $LAST_CODE via $LAST_SCHEME) et sert le commit $REPO_COMMIT"
else
    echo "⚠️  vérification échouée après $((OPS_VERIFY_RETRIES * OPS_VERIFY_INTERVAL))s." >&2
    echo "    HTTP=$LAST_CODE via $LAST_SCHEME · marqueur=$LAST_MARKER (attendu $REPO_COMMIT)" >&2
    echo "    -> restauration de l'ancien contenu, le nouveau (cassé) est conservé dans ${WEBROOT}.failed" >&2
    restore_from_old "$WEBROOT"
    nginx -t && systemctl reload nginx || true
    exit 1
fi

echo "==> [5/5] suppression de l'ancien contenu (vérification positive)"
cleanup_old "$WEBROOT"
