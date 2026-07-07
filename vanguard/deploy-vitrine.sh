#!/usr/bin/env bash
# Déploiement de la VITRINE (statique) sur le VPS — apex ariavanguardzhc.com.
#
# Ce que fait ce script (idempotent, rejouable) :
#   1) build la vitrine via Docker (node:22-slim) — AUCUNE dépendance node sur l'hôte,
#      même image que le build produit, donc résultat identique à la CI ;
#   2) publie dist/ dans le webroot de façon ATOMIQUE (swap de dossier — jamais de
#      demi-page servie pendant la copie) ;
#   3) `nginx -t` puis reload ;
#   4) VÉRIFIE, depuis le VPS lui-même, que l'apex répond 200 et sert bien la vitrine.
#
# Ce script NE touche PAS : le conteneur aria-api, le site nginx `aria-api`, ni la
# config TLS (gérée par certbot). Il ne fait AUCUNE opération git — mets le dépôt
# au bon commit AVANT (ex. `git fetch origin main && git checkout -B main origin/main`).
#
# Prérequis (installation initiale, une seule fois) : voir vanguard/nginx/vitrine.conf.
#
# Surcharges possibles :  REPO=/opt/aria  WEBROOT=/var/www/ariavanguardzhc  HOST=ariavanguardzhc.com

set -euo pipefail

REPO="${REPO:-/opt/aria}"
WEBROOT="${WEBROOT:-/var/www/ariavanguardzhc}"
HOST="${HOST:-ariavanguardzhc.com}"

command -v docker >/dev/null || { echo "ERREUR: docker introuvable"; exit 1; }
[ -f "$REPO/vanguard/package.json" ] || { echo "ERREUR: $REPO/vanguard introuvable"; exit 1; }

echo "==> [1/4] build vitrine (Docker node:22-slim)"
docker run --rm -v "$REPO":/repo -w /repo/vanguard node:22-slim \
    sh -c "npm ci --no-audit --no-fund && npm run build"
[ -f "$REPO/vanguard/dist/index.html" ] || { echo "ERREUR: dist/index.html absent — build échoué"; exit 1; }

echo "==> [2/4] publication atomique -> $WEBROOT"
parent="$(dirname "$WEBROOT")"; mkdir -p "$parent"
tmp="$(mktemp -d "$parent/.vitrine.XXXXXX")"
cp -a "$REPO/vanguard/dist/." "$tmp/"
chown -R www-data:www-data "$tmp" 2>/dev/null || true
if [ -d "$WEBROOT" ]; then rm -rf "${WEBROOT}.old"; mv "$WEBROOT" "${WEBROOT}.old"; fi
mv "$tmp" "$WEBROOT"
rm -rf "${WEBROOT}.old"

echo "==> [3/4] nginx -t + reload"
nginx -t
systemctl reload nginx

echo "==> [4/4] vérification (vue du VPS)"
code="$(curl -s -o /dev/null -w '%{http_code}' -H "Host: $HOST" http://127.0.0.1/)"
if [ "$code" = "200" ] && curl -s -H "Host: $HOST" http://127.0.0.1/ | grep -qiE "aria|vanguard"; then
    echo "✅ OK — la vitrine répond (HTTP $code) et sert bien son contenu sur $HOST"
else
    echo "⚠️  réponse inattendue (HTTP $code) — vérifie le server block nginx (vitrine.conf)"
    exit 1
fi
