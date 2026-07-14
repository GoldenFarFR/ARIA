#!/usr/bin/env bash
# Fonctions PURES (aucun docker/nginx/réseau) de la logique de bascule blue-green de
# deploy.sh -- isolées ici pour être testables sans VPS réel (cf. vanguard/tests/
# test_deploy_blue_green.py). deploy.sh les source, ne les redéfinit jamais.

# Lit le port actif depuis le fichier upstream nginx ("server 127.0.0.1:PORT;").
# Échoue explicitement (jamais de port deviné) si le fichier est absent/illisible ou
# ne contient aucun port reconnaissable -- c'est le garde-fou anti-bascule-à-l'aveugle
# décrit dans le plan #154 : deploy.sh doit s'arrêter net si le prérequis nginx (étape
# d'installation manuelle unique) n'a pas été fait.
read_active_port() {
    local upstream_file="$1"
    if [ ! -f "$upstream_file" ]; then
        echo "aria-api-upstream.conf introuvable : $upstream_file (prérequis nginx non installé -- voir vanguard/nginx/aria-api-upstream.conf.template)" >&2
        return 1
    fi
    local port
    port="$(grep -oE '127\.0\.0\.1:[0-9]+' "$upstream_file" | head -1 | cut -d: -f2)"
    if [ -z "$port" ]; then
        echo "aucun port reconnaissable dans $upstream_file" >&2
        return 1
    fi
    echo "$port"
}

# Alternance stricte 8000<->8001 uniquement -- seule paire de ports couverte par la
# doctrine "binding 127.0.0.1 only" (cf. deploy.sh). Tout autre port actif est une
# anomalie qu'on refuse de deviner.
standby_port() {
    local active="$1"
    case "$active" in
        8000) echo 8001 ;;
        8001) echo 8000 ;;
        *)
            echo "port actif inattendu : $active (seuls 8000/8001 sont gérés)" >&2
            return 1
            ;;
    esac
}

# Génère le contenu du fichier upstream nginx pour un port donné.
render_upstream_conf() {
    local port="$1"
    printf 'upstream aria_api_backend {\n    server 127.0.0.1:%s;\n}\n' "$port"
}

# Réessaie une commande jusqu'à succès (exit 0) ou expiration du plafond
# max_attempts x interval_seconds. `systemctl reload nginx` n'est pas instantané --
# les nouveaux workers mettent un court instant à tourner (bug réel constaté en
# déploiement #154 : la vérification finale de deploy.sh tirait un unique curl juste
# après le reload, sans marge, et pouvait faussement déclencher un rollback alors que
# la bascule était en réalité correcte). Fonction IDENTIQUE à celle de
# deploy_vitrine_lib.sh (#157) -- ne pas dupliquer différemment, les deux scripts
# partagent le même besoin de retry post-reload nginx.
retry_until() {
    local max_attempts="$1" interval_seconds="$2"
    shift 2
    local attempt
    for attempt in $(seq 1 "$max_attempts"); do
        if "$@"; then
            return 0
        fi
        if [ "$attempt" -lt "$max_attempts" ]; then
            sleep "$interval_seconds"
        fi
    done
    return 1
}
