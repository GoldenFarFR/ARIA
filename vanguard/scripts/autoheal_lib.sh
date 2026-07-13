#!/usr/bin/env bash
# Fonctions PURES de la fenêtre glissante du disjoncteur autoheal (#154, demande
# opérateur : plafonner les redémarrages auto plutôt que boucler en silence).
# Isolées ici pour être testables sans docker (cf. vanguard/tests/
# test_deploy_blue_green.py). autoheal-circuit-breaker.sh les source, ne les
# redéfinit jamais.

# Ajoute l'horodatage $now au fichier d'état, purge les entrées plus vieilles que
# $window_seconds, renvoie le nombre d'entrées restantes (fenêtre glissante, pas un
# compteur qui ne se réinitialise jamais).
record_and_count() {
    local state_file="$1" window_seconds="$2" now="$3"
    touch "$state_file"
    echo "$now" >> "$state_file"
    local cutoff=$((now - window_seconds))
    local kept
    kept="$(awk -v cutoff="$cutoff" '$1 >= cutoff' "$state_file")"
    printf '%s\n' "$kept" > "$state_file"
    grep -c . "$state_file" 2>/dev/null || echo 0
}
