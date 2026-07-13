#!/usr/bin/env bash
# Déploiement de la VITRINE (statique) sur le VPS — apex ariavanguardzhc.com.
#
# Ce que fait ce script (idempotent, rejouable) :
#   1) build la vitrine via Docker (node:22-slim) — AUCUNE dépendance node sur l'hôte,
#      même image que le build produit, donc résultat identique à la CI — et écrit un
#      marqueur de build (dist/build-info.txt, le commit court) ;
#   2) publie dist/ dans le webroot de façon ATOMIQUE (swap de dossier — jamais de
#      demi-page servie pendant la copie) SANS supprimer l'ancien webroot (#157) ;
#   3) `nginx -t` puis reload ;
#   4) VÉRIFIE, depuis le VPS lui-même, que l'apex répond 200, sert bien la vitrine ET
#      que le marqueur de build correspond exactement au commit déployé (#157 -- avant,
#      seule une heuristique "aria/vanguard" dans le body, qui matcherait même du
#      contenu périmé) ;
#   5) supprime l'ancien webroot SEULEMENT si l'étape 4 est entièrement positive.
#
# #157 (remplace l'ancien "supprimer .old avant de vérifier", même famille de bug que
# l'ancien deploy.sh corrigé sous #154) : en cas d'échec de la vérification, l'ancien
# contenu ($WEBROOT.old) reprend sa place et le nouveau contenu cassé est conservé sous
# $WEBROOT.failed (jamais supprimé silencieusement) pour investigation.
#
# La vérification retente sur un court plafond (~10s) : `systemctl reload nginx` n'est
# pas instantané, les nouveaux workers mettent un court instant à tourner (bug réel
# constaté en déploiement #154, même forme de boucle de retry ici -- cf. deploy_vitrine_lib.sh).
#
# Ce script NE touche PAS : le conteneur aria-api, le site nginx `aria-api`, ni la
# config TLS (gérée par certbot). Il ne fait AUCUNE opération git — mets le dépôt
# au bon commit AVANT (ex. `git fetch origin main && git checkout -B main origin/main`).
#
# Prérequis (installation initiale, une seule fois) : voir vanguard/nginx/vitrine.conf.
#
# Surcharges possibles :  REPO=/opt/aria  WEBROOT=/var/www/ariavanguardzhc  HOST=ariavanguardzhc.com
#   VITRINE_VERIFY_RETRIES (défaut 10)  VITRINE_VERIFY_INTERVAL (défaut 1, secondes)

set -euo pipefail

REPO="${REPO:-/opt/aria}"
WEBROOT="${WEBROOT:-/var/www/ariavanguardzhc}"
HOST="${HOST:-ariavanguardzhc.com}"
VITRINE_VERIFY_RETRIES="${VITRINE_VERIFY_RETRIES:-10}"
VITRINE_VERIFY_INTERVAL="${VITRINE_VERIFY_INTERVAL:-1}"

# shellcheck source=./deploy_vitrine_lib.sh
source "$REPO/vanguard/deploy_vitrine_lib.sh"

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

REPO_COMMIT="$(git -C "$REPO" rev-parse --short=12 HEAD)"

echo "==> [1/5] build vitrine (Docker node:22-slim) — VITE_PRIVY_APP_ID injecté"
docker run --rm -e VITE_PRIVY_APP_ID="$VITE_PRIVY_APP_ID" \
    -v "$REPO":/repo -w /repo/vanguard node:22-slim \
    sh -c "npm ci --no-audit --no-fund && npm run build"
[ -f "$REPO/vanguard/dist/index.html" ] || { echo "ERREUR: dist/index.html absent — build échoué"; exit 1; }
# PREUVE que l'App ID est réellement embarqué dans le bundle (sinon écran d'erreur).
if ! grep -rqF "$VITE_PRIVY_APP_ID" "$REPO/vanguard/dist/assets/" 2>/dev/null; then
    echo "ERREUR: VITE_PRIVY_APP_ID absent du bundle construit — à revoir."; exit 1
fi
echo "    ✓ App ID Privy bien présent dans le bundle"
echo "$REPO_COMMIT" > "$REPO/vanguard/dist/build-info.txt"
echo "    ✓ marqueur de build écrit (commit $REPO_COMMIT)"

echo "==> [2/5] publication atomique -> $WEBROOT (l'ancien contenu est conservé jusqu'à vérification)"
parent="$(dirname "$WEBROOT")"; mkdir -p "$parent"
tmp="$(mktemp -d "$parent/.vitrine.XXXXXX")"
cp -a "$REPO/vanguard/dist/." "$tmp/"
chown -R www-data:www-data "$tmp" 2>/dev/null || true
publish_atomic "$WEBROOT" "$tmp"

echo "==> [3/5] nginx -t + reload"
nginx -t
systemctl reload nginx

echo "==> [4/5] vérification (vue du VPS, avec retry -- le reload nginx n'est pas instantané)"
# Après certbot, le vhost :80 redirige (301) vers HTTPS — on teste donc HTTPS en
# priorité (via --resolve, sur le vrai certificat), avec repli HTTP avant certbot.
LAST_CODE=""; LAST_SCHEME=""; LAST_BODY=""; LAST_MARKER=""
verify_vitrine_once() {
    local code scheme body marker
    code="$(curl -s -o /dev/null -w '%{http_code}' --resolve "$HOST:443:127.0.0.1" "https://$HOST/" 2>/dev/null || true)"
    if [ "$code" = "200" ]; then
        scheme="https"
        body="$(curl -s --resolve "$HOST:443:127.0.0.1" "https://$HOST/" 2>/dev/null || true)"
        marker="$(curl -s --resolve "$HOST:443:127.0.0.1" "https://$HOST/build-info.txt" 2>/dev/null || true)"
    else
        scheme="http"
        code="$(curl -s -o /dev/null -w '%{http_code}' -H "Host: $HOST" "http://127.0.0.1/" 2>/dev/null || true)"
        body="$(curl -s -H "Host: $HOST" "http://127.0.0.1/" 2>/dev/null || true)"
        marker="$(curl -s -H "Host: $HOST" "http://127.0.0.1/build-info.txt" 2>/dev/null || true)"
    fi
    LAST_CODE="$code"; LAST_SCHEME="$scheme"; LAST_BODY="$body"; LAST_MARKER="$marker"
    [ "$code" = "200" ] \
        && printf '%s' "$body" | grep -qiE "aria|vanguard" \
        && [ "$marker" = "$REPO_COMMIT" ]
}

if retry_until "$VITRINE_VERIFY_RETRIES" "$VITRINE_VERIFY_INTERVAL" verify_vitrine_once; then
    echo "✅ OK — la vitrine répond (HTTP $LAST_CODE via $LAST_SCHEME) et sert le commit $REPO_COMMIT"
else
    echo "⚠️  vérification échouée après $((VITRINE_VERIFY_RETRIES * VITRINE_VERIFY_INTERVAL))s." >&2
    echo "    HTTP=$LAST_CODE via $LAST_SCHEME · marqueur=$LAST_MARKER (attendu $REPO_COMMIT)" >&2
    echo "    -> restauration de l'ancien contenu, le nouveau (cassé) est conservé dans ${WEBROOT}.failed" >&2
    restore_from_old "$WEBROOT"
    nginx -t && systemctl reload nginx || true
    exit 1
fi

echo "==> [5/5] suppression de l'ancien contenu (vérification positive)"
cleanup_old "$WEBROOT"
