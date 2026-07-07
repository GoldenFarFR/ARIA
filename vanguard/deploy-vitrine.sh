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

# Les variables VITE_* sont injectées AU BUILD (elles finissent dans le bundle
# statique). Chargées depuis vanguard/.env.deploy (gitignored) si présent.
# VITE_PRIVY_APP_ID est un identifiant PUBLIC (pas un secret) : c'est la MÊME
# valeur que PRIVY_APP_ID du backend. Sans elle, la vitrine n'affiche QUE
# « VITE_PRIVY_APP_ID is not configured » — on refuse donc de builder à vide.
ENV_DEPLOY="$REPO/vanguard/.env.deploy"
if [ -f "$ENV_DEPLOY" ]; then set -a; . "$ENV_DEPLOY"; set +a; fi
# Repli automatique : réutiliser l'App ID PUBLIC déjà présent côté backend
# (PRIVY_APP_ID de vanguard/backend/.env) — même valeur, zéro saisie manuelle.
BACKEND_ENV="$REPO/vanguard/backend/.env"
if [ -z "${VITE_PRIVY_APP_ID:-}" ] && [ -f "$BACKEND_ENV" ]; then
    VITE_PRIVY_APP_ID="$(sed -nE 's/^PRIVY_APP_ID=[[:space:]"'"'"']*([^[:space:]"'"'"']+).*/\1/p' "$BACKEND_ENV" | head -1)"
fi
if [ -z "${VITE_PRIVY_APP_ID:-}" ]; then
    echo "ERREUR: VITE_PRIVY_APP_ID introuvable (ni env, ni .env.deploy, ni PRIVY_APP_ID backend)."
    echo "  -> fournis-le :  VITE_PRIVY_APP_ID=<valeur> ./vanguard/deploy-vitrine.sh"
    echo "     (valeur = App ID de ton dashboard Privy — identifiant public)"
    exit 1
fi

echo "==> [1/4] build vitrine (Docker node:22-slim) — VITE_PRIVY_APP_ID injecté"
docker run --rm -e VITE_PRIVY_APP_ID="$VITE_PRIVY_APP_ID" \
    -v "$REPO":/repo -w /repo/vanguard node:22-slim \
    sh -c "npm ci --no-audit --no-fund && npm run build"
[ -f "$REPO/vanguard/dist/index.html" ] || { echo "ERREUR: dist/index.html absent — build échoué"; exit 1; }
# PREUVE que l'App ID est réellement embarqué dans le bundle (sinon écran d'erreur).
if ! grep -rqF "$VITE_PRIVY_APP_ID" "$REPO/vanguard/dist/assets/" 2>/dev/null; then
    echo "ERREUR: VITE_PRIVY_APP_ID absent du bundle construit — à revoir."; exit 1
fi
echo "    ✓ App ID Privy bien présent dans le bundle"

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
# Après certbot, le vhost :80 redirige (301) vers HTTPS — on teste donc HTTPS en
# priorité (via --resolve, sur le vrai certificat), avec repli HTTP avant certbot.
code="$(curl -s -o /dev/null -w '%{http_code}' --resolve "$HOST:443:127.0.0.1" "https://$HOST/" 2>/dev/null || true)"
if [ "$code" = "200" ]; then
    scheme="https"; body="$(curl -s --resolve "$HOST:443:127.0.0.1" "https://$HOST/" 2>/dev/null || true)"
else
    scheme="http"
    code="$(curl -s -o /dev/null -w '%{http_code}' -H "Host: $HOST" "http://127.0.0.1/" 2>/dev/null || true)"
    body="$(curl -s -H "Host: $HOST" "http://127.0.0.1/" 2>/dev/null || true)"
fi
if [ "$code" = "200" ] && printf '%s' "$body" | grep -qiE "aria|vanguard"; then
    echo "✅ OK — la vitrine répond (HTTP $code via $scheme) et sert son contenu sur $HOST"
else
    echo "⚠️  réponse inattendue (HTTP $code via $scheme) — vérifie le server block nginx"
    exit 1
fi
