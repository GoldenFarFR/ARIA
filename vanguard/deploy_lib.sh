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
