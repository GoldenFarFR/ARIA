# [VPS Research] Rollback automatique sur échec de health-check — setup single-VPS Docker+nginx

## Contexte et constat de départ

Veille sur les patterns éprouvés de rollback automatique post-déploiement,
contrainte explicite : un seul conteneur Docker + nginx sur un seul VPS,
pas d'orchestrateur multi-nœuds (Kubernetes hors de portée ici). Comparé
directement à l'existant, lu dans le code : `vanguard/deploy.sh` (API) et
`vanguard/deploy-vitrine.sh` (statique).

**Constat clé sur `deploy.sh` (fait vérifié, pas une supposition)** :
le script tague déjà l'ancienne image en `aria-api:rollback` (étape 2/6)
et vérifie le health-check après démarrage (étape 6/6) — mais s'il échoue,
il se contente d'`exit 1` avec une invite à consulter les logs. **Il n'y a
aujourd'hui aucun rollback automatique**, et pire : l'étape 4/6 supprime
TOUS les conteneurs `aria-api` existants AVANT de lancer le nouveau — donc
au moment où le health-check échoue, l'ancien conteneur ne tourne déjà
plus. Un rollback "auto" nécessiterait aujourd'hui un rebuild/relance
manuelle depuis `aria-api:rollback`, pas juste un aiguillage instantané.
C'est exactement le gap concret que cette veille doit combler.

`deploy-vitrine.sh` a un gap similaire mais moins grave : le dossier
`.old` (webroot précédent) est supprimé juste après le swap (étape 2/4),
avant même la vérification finale (étape 4/4) — donc là aussi, si la
vérification échoue, il n'y a plus de webroot précédent à restaurer
automatiquement.

---

## Pistes évaluées

### 1. Blue-green par alternance de port (recommandé, priorité haute)

**Mécanisme concret** : au lieu de tuer l'ancien conteneur avant de lancer
le nouveau, lancer le nouveau sur un port alterné (ex. 8000 ↔ 8001,
noms `aria-api-blue`/`aria-api-green`), health-check le nouveau **pendant
que l'ancien tourne encore**, puis basculer nginx (ou le binding direct)
vers le nouveau port seulement si le health-check passe. Si échec :
l'ancien conteneur tourne toujours, donc le "rollback" est trivial — il
n'y a rien à faire, juste ne pas basculer. Décrit en détail et avec
scripts concrets par plusieurs sources récentes (2026) : le point commun
est que nginx ne redémarre pas lors du switch (`reload`, pas `restart`) —
les workers en cours terminent leurs requêtes en vol avant de sortir —
donc bascule et rollback en moins de 5 secondes rapportés en usage réel.

**Effort d'intégration réaliste** : modéré. `deploy.sh` gère déjà le tag
`rollback` et le health-check — il "suffit" de : (a) ne plus supprimer
l'ancien conteneur à l'étape 4 mais le laisser tourner sur son port
actuel, (b) lancer le nouveau sur le port inverse, (c) health-check le
nouveau AVANT de toucher au binding/nginx, (d) ne supprimer l'ancien
conteneur qu'après confirmation. Nécessite de vérifier s'il existe déjà
un site nginx dédié à `aria-api` en amont du port 8000 (le commentaire de
`deploy-vitrine.sh` mentionne "le site nginx aria-api" comme existant
mais distinct de la vitrine) — si oui, la bascule se fait au niveau nginx
(upstream), sinon il faut en introduire un (actuellement le binding est
direct `127.0.0.1:8000:8000` sans proxy intermédiaire visible dans
`deploy.sh`).

**Signal de maturité/fiabilité** : pattern très documenté et stable
(plusieurs guides indépendants 2025-2026, mécanisme nginx `reload`
utilisé tel quel depuis des années, aucune dépendance nouvelle).

### 2. `willfarrell/autoheal` — restart auto sur health-check Docker natif (recommandé, effort quasi nul, complémentaire)

**Mécanisme concret** : un unique conteneur sidecar qui surveille le socket
Docker et redémarre automatiquement tout conteneur marqué `unhealthy` par
son `HEALTHCHECK` Docker natif, sur un intervalle configurable
(`AUTOHEAL_INTERVAL`, 5s par défaut).

**Effort d'intégration réaliste : quasi nul.** Deux prérequis : (1) ajouter
une directive `HEALTHCHECK` au `Dockerfile` d'`aria-api` (actuellement le
health-check n'est vérifié que depuis l'extérieur, dans le script de
déploiement — pas par Docker lui-même) ; (2) lancer le conteneur
`willfarrell/autoheal` une fois, avec accès à `/var/run/docker.sock`.
Aucune modification de `deploy.sh` nécessaire.

**Limite importante à documenter** : autoheal redémarre le **même**
conteneur (même image) — il ne fait pas de rollback vers une version
antérieure. Utile pour les pannes transitoires (crash, deadlock, fuite
mémoire) mais **ne résout pas** le cas "le commit qu'on vient de déployer
est cassé" — ce cas reste couvert par le pattern blue-green (#1) ou par un
rollback manuel vers `aria-api:rollback`. Les deux se complètent, ils ne
sont pas substituables.

**Signal de maturité/fiabilité** : projet mature et largement cité comme
référence standard pour ce besoin précis (multiples articles 2026 le
recommandent comme solution par défaut hors Swarm/Kubernetes), licence
libre, image légère.

### 3. Circuit breaker via nginx / HAProxy (pertinent seulement si #1 est en place, sinon effort disproportionné)

**Mécanisme concret** : côté nginx, `max_fails`/`fail_timeout` sur
l'upstream fait déjà une forme basique de circuit breaker — après N
échecs, nginx arrête temporairement d'essayer ce backend. Côté HAProxy
(alternative à nginx, pas déjà en place ici), le mode `observe layer7` +
`error-limit` + `on-error mark-down` est plus explicite : bascule
`server DOWN` après un seuil d'erreurs mesuré sur le trafic réel — utile
pour détecter une dégradation que les health-checks actifs classiques
loupent (ex. l'endpoint `/health` répond bien mais les vraies requêtes
échouent).

**Effort d'intégration réaliste** : trivial pour la version nginx
(`max_fails`/`fail_timeout`, deux lignes de config) — mais **seulement
utile s'il y a un upstream avec plusieurs backends à basculer entre eux**.
Avec un seul conteneur (état actuel), un circuit breaker n'a rien vers quoi
basculer : il ne devient réellement utile qu'une fois le blue-green (#1)
en place. Passer à HAProxy serait un changement d'infrastructure plus
lourd, non justifié ici (nginx suffit).

**Signal de maturité/fiabilité** : pattern nginx natif, aucune dépendance
supplémentaire ; HAProxy mature mais introduirait un second reverse-proxy
à maintenir — non recommandé tant que nginx seul suffit.

### 4. Feature flags self-hébergés (signal vert mais hors priorité immédiate)

**Mécanisme concret** : bascule instantanée d'une fonctionnalité en
production sans redéploiement — Flagsmith et Unleash sont les deux
références open-source matures (BSD-3-Clause pour le cœur Flagsmith,
Node.js+PostgreSQL pour Unleash), toutes deux "instant disable" sans
rollback ni redeploy. Alternative plus légère : **GO Feature Flag**, sans
base de données à opérer, pensé explicitement pour éviter l'infrastructure
lourde.

**Effort d'intégration réaliste : élevé relativement au besoin.**
Flagsmith/Unleash demandent un service + base de données supplémentaires
à opérer sur le VPS — en tension directe avec la contrainte "un seul
conteneur". GO Feature Flag (fichier de config, pas de DB) serait le choix
cohérent avec la contrainte, mais reste un système différent des rollbacks
de déploiement : il traite le rollback d'une **fonctionnalité applicative**
au niveau du code, pas le rollback d'un **déploiement** (image/commit
cassé). Pertinent si ARIA introduit des fonctionnalités risquées
individuelles à activer/désactiver en douceur, pas comme remplacement du
blue-green pour un déploiement raté.

**Signal de maturité/fiabilité** : Flagsmith et Unleash matures et
largement adoptés ; GO Feature Flag plus jeune mais actif.

---

## Synthèse et recommandation

| Piste | Effort sur le setup actuel | Résout quel problème | Priorité |
|---|---|---|---|
| Blue-green par alternance de port | Modéré (modifie `deploy.sh`) | Commit cassé détecté au déploiement — rollback instantané | **Haute** |
| `willfarrell/autoheal` | Quasi nul (sidecar + `HEALTHCHECK`) | Panne transitoire post-déploiement (crash, deadlock) | **Haute, complémentaire** |
| Circuit breaker nginx (`max_fails`) | Trivial, mais inutile seul | Dégradation non détectée par health-check actif | Moyenne, **après** le blue-green |
| Circuit breaker HAProxy | Élevé (nouveau composant) | Idem, en plus fin | Non recommandé ici |
| Feature flags (Flagsmith/Unleash/GO FF) | Élevé (Flagsmith/Unleash) à modéré (GO FF) | Rollback d'une fonctionnalité applicative, pas d'un déploiement | Basse, hors scope immédiat |

**Aucun signal rouge sur aucune piste** — toutes sont gratuites/open-source
et documentées comme fiables en usage réel single-VPS. Recommandation
concrète : traiter en premier le gap identifié dans `deploy.sh` (l'ancien
conteneur est tué avant que le nouveau soit vérifié) en adoptant le
pattern blue-green par alternance de port, et ajouter `autoheal` en
complément quasi gratuit pour les pannes transitoires. Les deux ensemble
couvrent la quasi-totalité des scénarios de rollback pertinents ici sans
changement d'architecture ni nouvelle dépendance lourde.

## Sources

- [Blue-Green Deployment on a Single VPS — ReadyServer](https://www.readyserver.sg/blog/blue-green-deployment-single-vps-zero-downtime/)
- [Blue/Green Deployment with Nginx Auto-Failover — dev.to](https://dev.to/herdeybayor/bluegreen-deployment-with-nginx-auto-failover-2abo)
- [Building a Self-Healing Blue/Green Deployment with Nginx & Docker — dev.to](https://dev.to/destinyobs/building-a-self-healing-bluegreen-deployment-with-nginx-docker-3k12)
- [Blue-Green deployment of a docker compose setup — technicallyshane.com](https://technicallyshane.com/2025/08/30/blue-green-deployment-of-a-docker-compose-setup.html)
- [willfarrell/docker-autoheal (GitHub)](https://github.com/willfarrell/docker-autoheal)
- [How to Set Up Docker Container Auto-Healing Without Orchestration — OneUptime](https://oneuptime.com/blog/post/2026-02-08-how-to-set-up-docker-container-auto-healing-without-orchestration/view)
- [Circuit breakers — HAProxy config tutorials](https://www.haproxy.com/documentation/haproxy-configuration-tutorials/reliability/circuit-breakers/)
- [nginx-circuit-breaker (GitHub, fiunchinho)](https://github.com/fiunchinho/nginx-circuit-breaker)
- [Canary Deployment with NGINX — Farshad Nick, Medium](https://medium.com/@farshadnick/canary-deployment-with-nginx-a-step-by-step-guide-using-two-methods-a3412f70b78b)
- [Flagsmith — Open Source Feature Flags](https://www.flagsmith.com/open-source)
- [Open source feature flags comparison — Unleash blog](https://www.getunleash.io/blog/11-open-source-feature-flag-tools)
- [GO Feature Flag](https://gofeatureflag.org/)
- Code ARIA vérifié : `vanguard/deploy.sh`, `vanguard/deploy-vitrine.sh`
  (lecture directe, 2026-07-13)

## Frontières confirmées respectées

Aucun code touché, aucune modification de `deploy.sh`/`deploy-vitrine.sh`.
Recherche et références uniquement. Décision d'implémentation (et choix
entre les pistes) laissée au commandement.
