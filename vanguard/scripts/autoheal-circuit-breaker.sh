#!/usr/bin/env bash
# Disjoncteur autoheal (#154) — willfarrell/autoheal redémarre aria-api dès que son
# HEALTHCHECK Docker natif le marque "unhealthy" (panne transitoire : crash, deadlock).
# Sans garde-fou, un commit cassé de façon intermittente ferait boucler autoheal
# indéfiniment en silence, masquant le vrai problème (même doctrine que le fix
# troncature LLM du 12/07 : rendre le phénomène observable, pas le corriger en
# aveugle). Ce script ne redémarre RIEN lui-même — il compte les transitions vers
# "unhealthy" sur une fenêtre glissante et, au-delà du plafond, met autoheal en pause
# (docker stop) avec un log explicite. Aucune action Telegram à ce stade (juste de
# l'observabilité), volontairement.
#
# Installation (une fois, avec le reste du prérequis #154 -- voir vanguard/nginx/
# aria-api-upstream.conf.template) : lancer ce script en arrière-plan (systemd
# recommandé, voir vanguard/systemd/aria-autoheal-circuit-breaker.service.template).
#
# Limite assumée : mettre autoheal en pause le pause pour TOUS les conteneurs
# surveillés (AUTOHEAL_CONTAINER_LABEL=all), pas seulement aria-api -- acceptable
# tant que ce VPS n'héberge qu'un seul service surveillé par autoheal.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./autoheal_lib.sh
source "$SCRIPT_DIR/autoheal_lib.sh"

CONTAINER="${ARIA_AUTOHEAL_TARGET:-aria-api}"
AUTOHEAL_CONTAINER="${ARIA_AUTOHEAL_SIDECAR:-aria-autoheal}"
STATE_FILE="${ARIA_AUTOHEAL_STATE_FILE:-/opt/aria-data/autoheal-circuit-breaker.state}"
MAX_RESTARTS="${ARIA_AUTOHEAL_MAX_RESTARTS:-3}"
WINDOW_SECONDS="${ARIA_AUTOHEAL_WINDOW_SECONDS:-600}"
POLL_INTERVAL="${ARIA_AUTOHEAL_POLL_INTERVAL:-20}"

mkdir -p "$(dirname "$STATE_FILE")"

echo "[autoheal-circuit-breaker] démarré -- cible=$CONTAINER plafond=${MAX_RESTARTS}/${WINDOW_SECONDS}s poll=${POLL_INTERVAL}s"

prev_status=""
while true; do
    sleep "$POLL_INTERVAL"
    status="$(docker inspect -f '{{.State.Health.Status}}' "$CONTAINER" 2>/dev/null || echo "unknown")"

    if [ "$status" = "unhealthy" ] && [ "$prev_status" != "unhealthy" ]; then
        now="$(date +%s)"
        count="$(record_and_count "$STATE_FILE" "$WINDOW_SECONDS" "$now")"
        echo "[autoheal-circuit-breaker] $(date -Iseconds) $CONTAINER unhealthy (transition) -- $count/$MAX_RESTARTS sur ${WINDOW_SECONDS}s"

        if [ "$count" -ge "$MAX_RESTARTS" ]; then
            echo "[autoheal-circuit-breaker] DISJONCTEUR OUVERT -- $count transitions unhealthy en ${WINDOW_SECONDS}s, pause d'autoheal (docker stop $AUTOHEAL_CONTAINER)."
            echo "[autoheal-circuit-breaker] Intervention manuelle requise : diagnostiquer ($CONTAINER logs), puis 'docker start $AUTOHEAL_CONTAINER' + vider $STATE_FILE pour ré-armer."
            docker stop "$AUTOHEAL_CONTAINER" >/dev/null 2>&1 || true
        fi
    fi

    prev_status="$status"
done
