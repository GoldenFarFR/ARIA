#!/usr/bin/env bash
# Déploiement ARIA sur le VPS — séquence sûre, reproductible, un seul conteneur.
#
# Usage (sur le VPS, connecté en root) :
#     cd /opt/aria && ./vanguard/deploy.sh
#
# Ce script encode la BONNE procédure pour ne jamais reproduire les incidents :
#   • commit "unknown"          -> on passe --build-arg GIT_COMMIT (health lisible)
#   • plusieurs conteneurs      -> on supprime TOUT conteneur aria-api avant de lancer
#   • exposition publique        -> binding STRICTEMENT 127.0.0.1 (jamais 0.0.0.0)
#
# Variables surchargeable au besoin (valeurs par défaut = prod VPS) :
#   ARIA_REPO_DIR (/opt/aria) · ARIA_ENV_FILE · ARIA_DATA_DIR
set -euo pipefail

REPO_DIR="${ARIA_REPO_DIR:-/opt/aria}"
ENV_FILE="${ARIA_ENV_FILE:-$REPO_DIR/vanguard/backend/.env}"
DATA_DIR="${ARIA_DATA_DIR:-/opt/aria-data}"
IMAGE="aria-api"
NAME="aria-api"
PORT_BIND="127.0.0.1:8000:8000"   # NE JAMAIS mettre 8000:8000 (exposerait à Internet)

cd "$REPO_DIR"

if [ ! -f "$ENV_FILE" ]; then
  echo "❌ Fichier .env introuvable : $ENV_FILE" >&2
  exit 1
fi

BRANCH="$(git rev-parse --abbrev-ref HEAD)"
echo "==> [1/6] Récupération du code (branche : $BRANCH)"
git pull --ff-only

COMMIT="$(git rev-parse HEAD)"
SHORT="$(git rev-parse --short=12 HEAD)"
echo "    commit à déployer : $SHORT"

echo "==> [2/6] Sauvegarde de l'image actuelle en rollback"
if docker image inspect "$IMAGE:latest" >/dev/null 2>&1; then
  docker tag "$IMAGE:latest" "$IMAGE:rollback"
  echo "    (ancienne image -> $IMAGE:rollback)"
fi

echo "==> [3/6] Build de l'image (commit injecté)"
docker build -f vanguard/Dockerfile --build-arg GIT_COMMIT="$COMMIT" -t "$IMAGE:latest" .

echo "==> [4/6] Suppression de TOUT conteneur aria-api existant (anti-doublon)"
docker ps -aq --filter "name=aria-api" | xargs -r docker rm -f

echo "==> [5/6] Lancement d'UN SEUL conteneur (127.0.0.1 uniquement)"
docker run -d --name "$NAME" --restart unless-stopped \
  -p "$PORT_BIND" \
  -v "$DATA_DIR":/app/backend/data \
  --env-file "$ENV_FILE" \
  "$IMAGE:latest"

echo "==> [6/6] Vérification"
sleep 6
docker ps --filter "name=aria-api"
echo "--- /api/health ---"
HEALTH="$(curl -s http://127.0.0.1:8000/api/health || true)"
echo "$HEALTH"
echo
if printf '%s' "$HEALTH" | grep -q "\"commit\":\"$SHORT\""; then
  echo "✅ OK — le conteneur tourne et sert le commit $SHORT"
else
  echo "⚠️  Health ne confirme pas le commit $SHORT."
  echo "    Logs : docker logs --tail 40 $NAME"
  exit 1
fi
