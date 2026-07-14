# #154 — Rollback automatique blue-green sur `deploy.sh`

Remplace l'ancien comportement (`deploy.sh` supprimait l'ancien conteneur `aria-api`
**avant** de vérifier le nouveau — un déploiement cassé ne pouvait pas être rollback,
ni auto ni manuellement dans l'instant). Détail complet de la veille et des
alternatives évaluées : `docs/aria-learning-inbox/2026-07-13-rollback-auto-health-check-single-vps.md`.

## Étape manuelle UNIQUE (VPS, une seule fois — jamais un `git pull`)

À faire **avant le tout premier déploiement** avec le nouveau `deploy.sh`. Sans ça,
`deploy.sh` échoue vite et clairement (il ne devine jamais de port).

```bash
# 1) Installer l'upstream nginx dédié (indirection nécessaire à la bascule de port)
cp /opt/aria/vanguard/nginx/aria-api-upstream.conf.template /etc/nginx/conf.d/aria-api-upstream.conf

# 2) Éditer le site nginx existant de api.ariavanguardzhc.com (géré par certbot, PAS
#    dans ce dépôt — cf. aria-ops) : remplacer la ligne
#      proxy_pass http://127.0.0.1:8000;
#    par
#      proxy_pass http://aria_api_backend;

# 3) Valider et recharger (aucun changement de comportement à ce stade)
nginx -t && systemctl reload nginx

# 4) willfarrell/autoheal (sidecar, redémarre un conteneur "unhealthy" — panne
#    transitoire, PAS un rollback de version)
docker run -d --name aria-autoheal --restart unless-stopped \
  -e AUTOHEAL_CONTAINER_LABEL=all \
  -v /var/run/docker.sock:/var/run/docker.sock \
  willfarrell/autoheal

# 5) Disjoncteur autoheal (#154 — plafonne les redémarrages auto, cf. section dédiée)
cp /opt/aria/vanguard/systemd/aria-autoheal-circuit-breaker.service.template \
  /etc/systemd/system/aria-autoheal-circuit-breaker.service
systemctl daemon-reload
systemctl enable --now aria-autoheal-circuit-breaker
journalctl -u aria-autoheal-circuit-breaker -f   # Ctrl+C une fois le démarrage confirmé
```

Après cette installation unique, les déploiements suivants ne relancent QUE
`./vanguard/deploy.sh` — aucun autre edit nginx/systemd manuel.

## Nouvelle séquence `deploy.sh`

1. `flock` anti double-déploiement concurrent.
2. `git pull --ff-only`, build de l'image (inchangé).
3. Lecture du port actif dans `/etc/nginx/conf.d/aria-api-upstream.conf` (8000 ou
   8001), calcul du port standby (l'autre).
4. Lancement du nouveau conteneur sur le port standby — **l'ancien continue de
   tourner**, rien n'est supprimé.
5. Health-check du nouveau (jusqu'à 30s, poll toutes les 3s) sur son port direct.
   Échec → suppression du seul nouveau conteneur, l'ancien n'a jamais été touché,
   zéro downtime, `exit 1`.
6. Bascule : renommage (`aria-api` reste le nom du conteneur actif en permanence),
   réécriture atomique de l'upstream nginx, `nginx -t` puis `reload`.
   - `nginx -t` échoue → bascule annulée, upstream restauré, renommages défaits,
     `exit 1`.
7. Vérification finale à travers nginx (pas juste le port direct, via `--resolve`
   comme `deploy-vitrine.sh`), **avec retry** (`retry_until`, jusqu'à 10 x 1s) --
   correctif post-déploiement : `systemctl reload nginx` n'est pas instantané, un
   unique curl juste après pouvait faussement déclencher un rollback alors que la
   bascule était en réalité correcte. Échec → upstream restauré, ancien conteneur
   **conservé** (jamais supprimé tant que le trafic réel n'est pas confirmé), `exit 1`.
8. Seulement si tout est confirmé : suppression de l'ancien conteneur, purge du cache
   Docker.

## Disjoncteur autoheal (`vanguard/scripts/autoheal-circuit-breaker.sh`)

`willfarrell/autoheal` redémarre `aria-api` dès que son `HEALTHCHECK` Docker natif le
marque `unhealthy`. Sans garde-fou, un commit cassé de façon intermittente ferait
boucler autoheal indéfiniment en silence. Le disjoncteur compte les **transitions**
vers `unhealthy` sur une fenêtre glissante de 10 min (par défaut) ; au-delà de 3, il
met autoheal en pause (`docker stop aria-autoheal`) et journalise clairement — aucune
action Telegram à ce stade, juste rendre le phénomène observable (même doctrine que le
fix troncature LLM du 12/07).

Ré-armer après investigation :
```bash
docker start aria-autoheal
rm -f /opt/aria-data/autoheal-circuit-breaker.state
```

**Limite assumée** : la pause coupe autoheal pour TOUS les conteneurs surveillés
(`AUTOHEAL_CONTAINER_LABEL=all`), pas seulement `aria-api` — acceptable tant que ce
VPS n'héberge qu'un seul service surveillé.

## Ce qui n'est PAS touché

- `vanguard/deploy-vitrine.sh` (site nginx distinct, aucune interaction).
- Le docker-compose : n'existe pas dans ce dépôt, pas introduit ici (déploiement
  `docker build`/`docker run` en ligne de commande, inchangé dans son style).
