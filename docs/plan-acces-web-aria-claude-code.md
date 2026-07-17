# Plan — accès web fiable pour une session Claude Code (16/07)

> Contexte : cette nuit, la session cloud "commandement" a buté sur trois limites
> distinctes en essayant de suivre le test paper-trading 1M$ en direct. Ce document
> les diagnostique, explique pourquoi chaque tentative a échoué, et fixe la
> direction à prendre — pour que la prochaine session (VPS ou cloud) n'ait pas à
> redécouvrir la même chose.

## 1. Constat — trois pannes différentes, pas une seule

1. **`WebFetch` vers le site public (`ariavanguardzhc.com`) → HTTP 403.**
   Cause : le pare-feu anti-bot (#22, Cloudflare) bloque les requêtes non-navigateur,
   y compris vers un endpoint public sans authentification (`/api/pulse` testé en
   direct). Ce n'est pas un problème d'autorisation — la requête est rejetée avant
   même d'atteindre l'application.
2. **Connecteur "Claude in Chrome" → absent de cette session, confirmé 3 fois**
   (`ListConnectors`, recherche d'outils différés, `ListPlugins`/`SearchPlugins`).
   Le toggle "activé" au niveau du compte (visible dans le panneau Connecteurs)
   n'active PAS l'outil dans une session déjà démarrée — il faut un vrai navigateur
   Chrome + son extension réellement appairés à CETTE session, dès son démarrage.
   VPS Research en dispose (raison exacte non confirmée : environnement différent,
   ou appairage fait tôt dans sa session) ; cette session cloud n'en a jamais eu.
3. **Aucun accès réseau direct au VPS/`aria.db`** (limite déjà connue et documentée
   de longue date pour toute session cloud — cf. `docs/etat-systeme-cable.md`).

## 2. Ce qui a été construit ce soir pour compenser (garder, indépendamment du choix ci-dessous)

`GET /api/aria/diagnostics/paper-ledger` (commit `d81ba5a`) — registre complet du
paper-trading (positions ouvertes/clôturées, thèse, cible, invalidation, P&L),
même patron que `/diagnostics/pool-status`/`/diagnostics/agent-wallet-ledger`
(#158/#159) : gate dédié `ARIA_DIAGNOSTIC_TOKEN` (header `X-Diagnostic-Access`),
exempté du gate Privy/opérateur, pensé pour un pire cas bénin (lecture seule,
jamais un risque financier même si le token fuit dans une conversation).

**Jamais testé en conditions réelles** : reste à vérifier si le pare-feu (#22)
bloque aussi cet appel précis, même avec le bon token — Cloudflare filtre au
niveau de l'edge, potentiellement avant que le token ne soit même lu par
l'application.

## 3. Décision — commandement transféré vers une session VPS

Une session VPS (comme Principal/Secondaire/Research) a un accès réseau direct à
l'API locale du conteneur `aria-api`, derrière nginx et Cloudflare, jamais filtrée.
**Correctif (17/07)** : le port n'est PAS toujours 8000 — le déploiement blue-green
(`deploy.sh`, #154) alterne 8000⟷8001 à chaque déploiement, et le conteneur actif
est celui pointé par `/etc/nginx/conf.d/aria-api-upstream.conf` (jamais deviné). Lire
ce fichier avant de coder le port en dur :

```bash
ACTIVE_PORT="$(grep -oE '127\.0\.0\.1:[0-9]+' /etc/nginx/conf.d/aria-api-upstream.conf | head -1 | cut -d: -f2)"
curl -s "http://127.0.0.1:${ACTIVE_PORT}/api/aria/diagnostics/paper-ledger" \
  -H "X-Diagnostic-Access: $ARIA_DIAGNOSTIC_TOKEN"
```

fonctionne sans dépendre de Cloudflare ni d'un connecteur navigateur. Plus besoin
d'attendre un token/déploiement pour vérifier l'état du test 1M$ — un simple appel
local suffit, à tout moment, depuis la session qui porte le commandement.

## 4. Recommandations pour la session qui reprend le commandement

- **Vérifier l'état en direct via `curl localhost:8000/...`**, pas via une capture
  Telegram/cockpit relayée manuellement — plus rapide, pas de risque d'erreur de
  lecture, et ne dépend d'aucun pare-feu.
- **Garder les 3 endpoints `/api/aria/diagnostics/*`** même une fois le commandement
  sur VPS — ils restent utiles pour une future session cloud, ou un outil externe,
  sans exiger un accès filesystem/VPS direct.
- **Ne pas dépendre de "Claude in Chrome`** pour la boucle de décision de trading ni
  pour le monitoring courant — vérifié ce soir (démo Research sur DexScreener) que
  la lecture navigateur est plus lente, plus fragile (déconnexions), plus chère
  (vision par capture) que les clients API déjà en place. Réservé, au mieux, à de la
  diligence ponctuelle sur des pages qui bloquent les clients HTTP classiques
  (ex. BeInCrypto) — jamais la boucle temps réel.
- **Ne pas retenter `WebFetch` vers le domaine de production** depuis une session
  cloud tant que Cloudflare n'a pas une règle d'exception dédiée (voir §5) — un 403
  confirmé une fois ne changera pas sans action côté dashboard Cloudflare.

## 5. Piste ouverte, non tranchée — exception Cloudflare pour les endpoints diagnostics

Si une future session cloud doit absolument vérifier l'état sans passer par un VPS :
une règle Cloudflare (WAF) qui laisse passer spécifiquement `/api/aria/diagnostics/*`
quand le header `X-Diagnostic-Access` est présent et valide serait la manière propre
de le faire — pas un affaiblissement général du pare-feu (#22 reste intact partout
ailleurs). Jamais testé, jamais construit — décision et configuration Cloudflare à
faire par l'opérateur si le besoin se représente.

## 6. Accès web d'ARIA elle-même (hors scope de cette panne, déjà mature)

Ce document couvre l'accès d'une session **Claude Code** à l'état d'ARIA — pas les
clients externes qu'ARIA utilise pour fonctionner (DexScreener, GoPlus, Blockscout,
Etherscan, Tavily, etc.), déjà construits, documentés et opérationnels (cf.
`docs/etat-systeme-cable.md`, `docs/architecture-extensibilite.md`). Aucune panne
identifiée de ce côté ce soir, aucune action requise ici.
