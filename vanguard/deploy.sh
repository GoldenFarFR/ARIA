#!/usr/bin/env bash
# Déploiement ARIA sur le VPS — blue-green par alternance de port, rollback quasi
# instantané en cas d'échec (#154).
#
# Usage (sur le VPS, connecté en root) :
#     cd /opt/aria && ./vanguard/deploy.sh
#
# Prérequis UNIQUE (une seule fois, avant le tout premier déploiement blue-green) :
#     voir vanguard/nginx/aria-api-upstream.conf.template -- ce script échoue vite et
#     clairement si ce prérequis n'est pas fait (jamais de port deviné).
#
# Principe (#154, remplace l'ancien "supprimer avant de vérifier") : le nouveau
# conteneur est lancé sur le port HÔTE inverse (8000<->8001, le conteneur écoute
# TOUJOURS en interne sur 8000) et vérifié PENDANT que l'ancien tourne encore. nginx
# ne bascule vers le nouveau qu'après un health-check positif, ET la bascule elle-même
# est revérifiée via le trafic RÉEL (à travers nginx, pas juste le port direct) avant
# de supprimer l'ancien conteneur. Le nom "aria-api" désigne en permanence le
# conteneur actif (renommages "aria-api-next"/"aria-api-old" seulement pendant la
# fenêtre de bascule) -- des scripts opérateur (`docker exec aria-api ...` dans
# dry_run_bonding_discovery.py/simulate_lifecycle.py) en dépendent.
#
# Ce script encode aussi les BONNES pratiques historiques :
#   • commit "unknown"          -> on passe --build-arg GIT_COMMIT (health lisible)
#   • exposition publique        -> binding STRICTEMENT 127.0.0.1 (jamais 0.0.0.0)
#
# Variables surchargeable au besoin (valeurs par défaut = prod VPS) :
#   ARIA_REPO_DIR (/opt/aria) · ARIA_ENV_FILE · ARIA_DATA_DIR · ARIA_NGINX_UPSTREAM_FILE
#   · ARIA_API_HOST (pour la vérification finale à travers nginx)
set -euo pipefail

REPO_DIR="${ARIA_REPO_DIR:-/opt/aria}"
ENV_FILE="${ARIA_ENV_FILE:-$REPO_DIR/vanguard/backend/.env}"
DATA_DIR="${ARIA_DATA_DIR:-/opt/aria-data}"
NGINX_UPSTREAM_FILE="${ARIA_NGINX_UPSTREAM_FILE:-/etc/nginx/conf.d/aria-api-upstream.conf}"
API_HOST="${ARIA_API_HOST:-api.ariavanguardzhc.com}"
IMAGE="aria-api"
NAME="aria-api"
LOCK_FILE="$REPO_DIR/.deploy.lock"
HEALTH_RETRIES=10
HEALTH_INTERVAL=3
VERIFY_RETRIES=10
VERIFY_INTERVAL=1

# shellcheck source=./deploy_lib.sh
source "$REPO_DIR/vanguard/deploy_lib.sh"

cd "$REPO_DIR"

# Anti double-déploiement concurrent (plusieurs sessions opérateur possibles sur ce
# VPS) : un déploiement en cours fait échouer le second plutôt qu'une collision sur le
# port standby ou le fichier upstream.
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    echo "❌ Un autre déploiement est déjà en cours (verrou $LOCK_FILE). Réessaie plus tard." >&2
    exit 1
fi

if [ ! -f "$ENV_FILE" ]; then
  echo "❌ Fichier .env introuvable : $ENV_FILE" >&2
  exit 1
fi

BRANCH="$(git rev-parse --abbrev-ref HEAD)"
echo "==> [1/8] Récupération du code (branche : $BRANCH)"
git pull --ff-only

COMMIT="$(git rev-parse HEAD)"
SHORT="$(git rev-parse --short=12 HEAD)"
echo "    commit à déployer : $SHORT"

echo "==> [2/8] Sauvegarde de l'image actuelle en rollback"
if docker image inspect "$IMAGE:latest" >/dev/null 2>&1; then
  docker tag "$IMAGE:latest" "$IMAGE:rollback"
  echo "    (ancienne image -> $IMAGE:rollback)"
fi

echo "==> [3/8] Build de l'image (commit injecté)"
docker build -f vanguard/Dockerfile --build-arg GIT_COMMIT="$COMMIT" -t "$IMAGE:latest" .

echo "==> [4/8] Détection du port actif (nginx upstream)"
ACTIVE_PORT="$(read_active_port "$NGINX_UPSTREAM_FILE")"
STANDBY_PORT="$(standby_port "$ACTIVE_PORT")"
echo "    actif=$ACTIVE_PORT standby=$STANDBY_PORT"

echo "==> [5/8] Lancement du nouveau conteneur sur le port standby ($STANDBY_PORT)"
docker rm -f aria-api-next >/dev/null 2>&1 || true
docker run -d --name aria-api-next --restart unless-stopped \
  -p "127.0.0.1:${STANDBY_PORT}:8000" \
  -v "$DATA_DIR":/app/backend/data \
  --env-file "$ENV_FILE" \
  "$IMAGE:latest"

echo "==> [6/8] Health-check du nouveau conteneur (jusqu'à $((HEALTH_RETRIES * HEALTH_INTERVAL))s), l'ancien continue de tourner"
HEALTH_OK=""
for _ in $(seq 1 "$HEALTH_RETRIES"); do
  sleep "$HEALTH_INTERVAL"
  HEALTH="$(curl -s "http://127.0.0.1:${STANDBY_PORT}/api/health" || true)"
  if printf '%s' "$HEALTH" | grep -q "\"commit\":\"$SHORT\""; then
    HEALTH_OK=1
    break
  fi
done

if [ -z "$HEALTH_OK" ]; then
  echo "⚠️  Health-check du nouveau conteneur a échoué après $((HEALTH_RETRIES * HEALTH_INTERVAL))s."
  echo "    Dernière réponse : $HEALTH"
  echo "    Logs : docker logs --tail 40 aria-api-next"
  echo "    -> l'ancien conteneur ($NAME, port $ACTIVE_PORT) n'a JAMAIS été touché, zéro downtime."
  docker rm -f aria-api-next >/dev/null 2>&1 || true
  exit 1
fi
echo "    ✓ nouveau conteneur sain sur le port $STANDBY_PORT (commit $SHORT confirmé)"

echo "==> [7/8] Bascule (renommage + nginx)"
docker rename "$NAME" aria-api-old
docker rename aria-api-next "$NAME"

UPSTREAM_BACKUP="$(mktemp)"
cp "$NGINX_UPSTREAM_FILE" "$UPSTREAM_BACKUP"
TMP_UPSTREAM="$(mktemp)"
render_upstream_conf "$STANDBY_PORT" > "$TMP_UPSTREAM"

rollback_rename() {
  echo "    -> annulation de la bascule : retour à l'état d'avant (les deux conteneurs restent en vie)."
  docker rename "$NAME" aria-api-next 2>/dev/null || true
  docker rename aria-api-old "$NAME" 2>/dev/null || true
}

mv "$TMP_UPSTREAM" "$NGINX_UPSTREAM_FILE"
if ! nginx -t; then
  echo "⚠️  nginx -t a échoué sur le nouvel upstream -- bascule ANNULÉE." >&2
  cp "$UPSTREAM_BACKUP" "$NGINX_UPSTREAM_FILE"
  rollback_rename
  echo "    Intervention manuelle requise : inspecter $NGINX_UPSTREAM_FILE et le site nginx." >&2
  exit 1
fi
systemctl reload nginx

echo "==> [8/8] Vérification du trafic RÉEL à travers nginx (pas juste le port direct, avec retry -- le reload nginx n'est pas instantané)"
# Même approche que deploy-vitrine.sh : HTTPS via --resolve en priorité (vrai
# certificat), repli HTTP avec Host: (avant certbot / environnement de test).
# `systemctl reload nginx` (ligne 135) n'est pas instantané -- les nouveaux workers
# mettent un court instant à tourner. Un unique curl juste après pouvait faussement
# déclencher un rollback alors que la bascule était en réalité correcte (bug réel
# constaté en déploiement). retry_until (deploy_lib.sh, identique à
# deploy_vitrine_lib.sh côté #157) retente sur un plafond de $((VERIFY_RETRIES * VERIFY_INTERVAL))s.
LAST_CODE=""; LAST_PUBLIC_HEALTH=""
verify_public_traffic_once() {
  local code health
  code="$(curl -s -o /dev/null -w '%{http_code}' --resolve "$API_HOST:443:127.0.0.1" "https://$API_HOST/api/health" 2>/dev/null || true)"
  if [ "$code" = "200" ]; then
    health="$(curl -s --resolve "$API_HOST:443:127.0.0.1" "https://$API_HOST/api/health" 2>/dev/null || true)"
  else
    health="$(curl -s -H "Host: $API_HOST" "http://127.0.0.1/api/health" 2>/dev/null || true)"
  fi
  LAST_CODE="$code"; LAST_PUBLIC_HEALTH="$health"
  printf '%s' "$health" | grep -q "\"commit\":\"$SHORT\""
}

if ! retry_until "$VERIFY_RETRIES" "$VERIFY_INTERVAL" verify_public_traffic_once; then
  echo "⚠️  Bascule nginx effectuée mais le trafic réel ne confirme PAS le commit $SHORT après $((VERIFY_RETRIES * VERIFY_INTERVAL))s." >&2
  echo "    Dernière réponse (HTTP $LAST_CODE) : $LAST_PUBLIC_HEALTH" >&2
  echo "    aria-api-old est CONSERVÉ (pas supprimé) comme filet -- intervention manuelle requise." >&2
  cp "$UPSTREAM_BACKUP" "$NGINX_UPSTREAM_FILE"
  nginx -t && systemctl reload nginx || true
  rollback_rename
  exit 1
fi

echo "    ✓ bascule confirmée à travers nginx -- suppression de l'ancien conteneur"
docker rm -f aria-api-old >/dev/null 2>&1 || true
rm -f "$UPSTREAM_BACKUP"

echo "✅ OK — le conteneur $NAME (port $STANDBY_PORT) sert le commit $SHORT"
echo "==> Purge du cache Docker (déploiement réussi)"
docker image prune -f
docker builder prune -f
