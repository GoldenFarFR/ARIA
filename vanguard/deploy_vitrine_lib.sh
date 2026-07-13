#!/usr/bin/env bash
# Fonctions de bascule/restauration/retry de deploy-vitrine.sh (#157) -- isolées ici
# pour être testables sans docker/nginx (cf. vanguard/backend/tests/
# test_deploy_vitrine_swap.py). deploy-vitrine.sh les source, ne les redéfinit jamais.

# Publie $tmp_dir comme nouveau $webroot, en gardant l'ancien sous "$webroot.old"
# -- jamais supprimé ici. Suppression uniquement après vérification positive,
# cf. cleanup_old ci-dessous (c'est le fix #157 : avant, .old était détruit ici même,
# avant toute vérification -- même famille de bug que l'ancien deploy.sh, #154).
publish_atomic() {
    local webroot="$1" tmp_dir="$2"
    if [ -d "$webroot" ]; then
        rm -rf "${webroot}.old"
        mv "$webroot" "${webroot}.old"
    fi
    mv "$tmp_dir" "$webroot"
}

# Supprime définitivement l'ancien webroot -- appelé UNIQUEMENT après vérification
# positive du nouveau contenu réellement servi.
cleanup_old() {
    local webroot="$1"
    rm -rf "${webroot}.old"
}

# Restaure l'ancien webroot à la place du nouveau (vérification négative). Le contenu
# CASSÉ est conservé sous "$webroot.failed" (nom fixe, écrasé au prochain échec --
# pas d'accumulation illimitée -- mais jamais supprimé silencieusement sans y passer).
# Échoue explicitement si ".old" est absent : jamais de restauration dans le vide.
restore_from_old() {
    local webroot="$1"
    if [ ! -d "${webroot}.old" ]; then
        echo "aucun ${webroot}.old -- impossible de revenir en arrière" >&2
        return 1
    fi
    rm -rf "${webroot}.failed"
    mv "$webroot" "${webroot}.failed"
    mv "${webroot}.old" "$webroot"
}

# Réessaie une commande jusqu'à succès (exit 0) ou expiration du plafond
# max_attempts x interval_seconds. `systemctl reload nginx` n'est pas instantané --
# les nouveaux workers mettent un court instant à tourner (bug réel constaté en
# déploiement #154, corrigé côté deploy.sh sur claude/rollback-deploy-retry-fix-temp
# -- même forme de boucle ici volontairement, à réconcilier si les deux implémentations
# divergent légèrement une fois les deux branches fusionnées sur main).
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
